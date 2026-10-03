#!/usr/bin/env python3
"""二进制写回: addrmap + 改动 -> 新 HCB, 只改等宽 push_* 立即数.

安全规则 (违反任一条即 abort, 不写文件):
  1. addrmap[prim][field] 必须存在 (静态/追踪到的常量源).
  2. 新值必须装进原宽度 (i8:-128..127, i16:-32768..32767, i32 全域, f32 全域).
  3. 打补丁前回读原文件该地址操作码+旧值, 必须与 addrmap 记录一致.
  4. 同一地址多次改动值必须一致 (防 alias 冲突).

用法: patch_hcb.py <orig.hcb> <addrmap.json> <out.hcb> "prim:field=value" [...]
"""
import json
import struct
import sys

OPCODE = {"push_i8": (0x0C, 1, "<b"), "push_i16": (0x0B, 2, "<h"),
          "push_i32": (0x0A, 4, "<i"), "push_f32": (0x0D, 4, "<f")}


def main():
    if len(sys.argv) < 5:
        print(__doc__)
        return 2
    orig, map_path, out = sys.argv[1:4]
    edits = []
    for e in sys.argv[4:]:
        pf, v = e.split("=")
        pid, field = pf.split(":")
        edits.append((pid, field, v))

    am = json.load(open(map_path, encoding="utf-8"))["addrmap"]
    data = bytearray(open(orig, "rb").read())
    seen_addr = {}

    for pid, field, vstr in edits:
        try:
            site = am[pid][field]
        except KeyError:
            print(f"ABORT: 无映射 prim {pid}.{field} (非常量源, 不可写回)")
            return 1
        addr, mnem = site["addr"], site["mnemonic"]
        if mnem not in OPCODE:
            print(f"ABORT: {pid}.{field} 源 {mnem} 非数值 push")
            return 1
        op, width, fmt = OPCODE[mnem]
        if data[addr] != op:
            print(f"ABORT: addr {addr} 操作码 {data[addr]:#x} != {mnem}({op:#x}), 文件与反汇编不一致")
            return 1
        old = struct.unpack_from(fmt, data, addr + 1)[0]
        if old != site["old"]:
            print(f"ABORT: addr {addr} 现值 {old} != 记录旧值 {site['old']}")
            return 1
        new = float(vstr) if fmt == "<f" else int(vstr)
        if fmt != "<f":
            lo, hi = -(2 ** (width * 8 - 1)), 2 ** (width * 8 - 1) - 1
            if not (lo <= new <= hi):
                print(f"ABORT: {new} 装不进 {mnem} (等宽约束, 不拓宽)")
                return 1
        blob = struct.pack(fmt, new)
        if addr in seen_addr and seen_addr[addr] != blob:
            print(f"ABORT: addr {addr} 冲突改动")
            return 1
        seen_addr[addr] = blob
        data[addr + 1:addr + 1 + width] = blob
        print(f"  prim {pid}.{field}: {old} -> {new} @ {addr} ({mnem})")

    open(out, "wb").write(data)
    print(f"OK -> {out} ({len(seen_addr)} 处)")


if __name__ == "__main__":
    sys.exit(main())
