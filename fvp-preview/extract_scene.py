#!/usr/bin/env python3
"""静态场景抽取: disassembler 工程 -> app Snapshot JSON + 写回地址映射.

只做静态 last-write-wins (分支/循环/动画中间值忽略, 文档见 README 验收口径).
语义逐条对齐 rfvp graph.rs / prim.rs:
  PrimSetSprt 全重置 / Nil=保持 / rot%3600 / scale越界->1000 /
  PrimSetZ Int->clamp+0x04, Float->仅0x04, Nil->清0x04 /
  PrimSetOP 仅 Sprt 生效且置 0x02 / PrimGroupIn(a,b): a 进组 b(b 清 x/y 变 Group).

写回只改等宽 push_* 立即数, 地址/宽度记在 addrmap.json.
"""
import json
import sys
import yaml

PUSH_INT = {"push_i8": 1, "push_i16": 2, "push_i32": 4}
NIL = "NIL"
UNKNOWN = "UNKNOWN"


def const_of(inst):
    m = inst["mnemonic"]
    ops = inst.get("operands") or []
    if m in PUSH_INT:
        try:
            return ("int", int(ops[0]), PUSH_INT[m])
        except (ValueError, IndexError):
            return ("unknown", None, 0)
    if m == "push_f32":
        try:
            return ("float", float(ops[0]), 4)
        except (ValueError, IndexError):
            return ("unknown", None, 0)
    if m == "push_nil":
        return ("nil", None, 0)
    if m == "push_string":
        return ("str", ops[0] if ops else "", -1)
    return ("unknown", None, 0)


def new_prim():
    return {
        "type": "None", "draw": False, "alpha": 0,
        "x": 0, "y": 0, "z": 0, "w": 0, "h": 0, "u": 0, "v": 0,
        "opx": 0, "opy": 0, "angle": 0, "fx": 1000, "fy": 1000,
        "attr": 0, "src": -1, "parent": None, "writes": 0,
    }


def norm_rot(r):
    r = r % 3600
    return r + 3600 if r < 0 else r


def clamp_scale(s):
    return s if 0 <= s <= 10000 else 1000


def main():
    if len(sys.argv) < 2:
        print("usage: extract_scene.py <project_dir> [--max-addr N] [--out scene.json] [--addrmap addrmap.json]")
        return 2
    proj = sys.argv[1]
    max_addr = None
    out = "scene.json"
    addrmap_out = "addrmap.json"
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--max-addr":
            max_addr = int(args[i + 1]); i += 2
        elif args[i] == "--out":
            out = args[i + 1]; i += 2
        elif args[i] == "--addrmap":
            addrmap_out = args[i + 1]; i += 2
        else:
            i += 1

    cfg = yaml.safe_load(open(f"{proj}/config.yaml", encoding="utf-8"))
    argc = {s["name"]: s["args_count"] for s in cfg["syscalls"]}

    # 快扫: disassembly.yaml 66MB, 全量 yaml 解析 7 分钟; 手写行扫描数秒.
    # 条目形态: "  - address: N" / "    mnemonic: M" / "    operands:" / "    - 'v'".
    insts = []
    addr = None
    mnem = None
    ops = None
    in_ops = False
    import re
    re_addr = re.compile(r"^(?:- |  - )address: (\d+)\s*$")
    re_mnem = re.compile(r"^    mnemonic: (\S+)\s*$")
    re_op = re.compile(r"^    - (?:'(.*)'|(\S+))\s*$")
    with open(f"{proj}/disassembly.yaml", encoding="utf-8") as f:
        for line in f:
            m = re_addr.match(line)
            if m:
                if mnem is not None:
                    if max_addr is None or addr < max_addr:
                        insts.append({"address": addr, "mnemonic": mnem,
                                      "operands": ops or []})
                addr = int(m.group(1)); mnem = None; ops = None; in_ops = False
                continue
            m = re_mnem.match(line)
            if m:
                mnem = m.group(1)
                if mnem != "syscall":
                    ops = []
                else:
                    ops = None
                in_ops = False
                continue
            if line == "    operands:\n":
                ops = []; in_ops = True
                continue
            if in_ops:
                m = re_op.match(line)
                if m:
                    ops.append(m.group(1) if m.group(1) is not None else m.group(2))
                    continue
                in_ops = False
    insts.sort(key=lambda t: t["address"])

    prims = {}
    texmap = {}   # GraphLoad slot -> path
    addrmap = {}  # prim_id -> field -> {addr,width,old}

    def P(pid):
        if pid not in prims:
            prims[pid] = new_prim()
        return prims[pid]

    def mark(pid, field, inst, old):
        kind, _, width = const_of(inst)
        addrmap.setdefault(str(pid), {})[field] = {
            "addr": inst["address"], "mnemonic": inst["mnemonic"],
            "width": width, "old": old,
        }

    TARGETS = {"PrimSetSprt", "PrimSetXY", "PrimSetRS", "PrimSetRS2",
               "PrimSetOP", "PrimSetZ", "PrimSetDraw", "PrimSetAlpha",
               "PrimSetWH", "PrimSetUV", "PrimSetBlend",
               "PrimGroupIn", "PrimGroupOut", "PrimSetNull", "GraphLoad"}

    n = len(insts)
    idx = 0
    applied = skipped = 0
    while idx < n:
        ins = insts[idx]
        if ins["mnemonic"] == "syscall" and (ins.get("operands") or [""])[0] in TARGETS:
            name = ins["operands"][0]
            k = argc.get(name, 0)
            prevs = insts[max(0, idx - k):idx]
            # 栈纪律: 参数必须是紧邻的常量推送; 非常量(变量/算结果)一律跳过.
            if len(prevs) < k or any(
                    p["mnemonic"] not in PUSH_INT and p["mnemonic"] not in (
                        "push_f32", "push_nil", "push_string")
                    for p in prevs):
                skipped += 1
                idx += 1
                continue
            vals = [const_of(p) for p in prevs]
            kinds = [v[0] for v in vals]
            if "unknown" in kinds:
                skipped += 1
                idx += 1
                continue
            num = [v[1] if v[0] in ("int", "float", "str") else None for v in vals]

            def I(j):
                return num[j] if kinds[j] == "int" else None

            if name == "GraphLoad":
                if kinds[0] == "int" and kinds[1] == "str":
                    texmap[str(int(num[0]))] = num[1]
                idx += 1
                continue
            pid = I(0)
            if pid is None or not (1 <= pid <= 4095):
                skipped += 1
                idx += 1
                continue
            p = P(pid)
            p["writes"] += 1
            applied += 1

            if name == "PrimSetSprt":
                # 全重置 (graph.rs prim_set_sprt), x/y Nil->0;
                # prim_init_with_type 置 draw=true（渲染相关，不可省）。
                p.update({"type": "Sprt", "draw": True, "opx": 0, "opy": 0, "alpha": 255,
                          "angle": 0, "fx": 1000, "fy": 1000, "u": 0, "v": 0,
                          "w": 0, "h": 0, "z": 1000, "attr": 0,
                          "src": I(1) if I(1) is not None and -2 <= I(1) <= 4095 else -1,
                          "x": I(2) or 0, "y": I(3) or 0})
                for f, j in (("src", 1), ("x", 2), ("y", 3)):
                    if j < len(prevs) and kinds[j] != "nil":
                        mark(pid, f, prevs[j], I(j))
            elif name == "PrimSetXY":
                if I(1) is not None:
                    mark(pid, "x", prevs[1], I(1)); p["x"] = I(1)
                if I(2) is not None:
                    mark(pid, "y", prevs[2], I(2)); p["y"] = I(2)
            elif name == "PrimSetRS":
                if I(1) is not None:
                    mark(pid, "angle", prevs[1], I(1)); p["angle"] = norm_rot(I(1))
                if I(2) is not None:
                    s = clamp_scale(I(2))
                    mark(pid, "fx", prevs[2], I(2)); mark(pid, "fy", prevs[2], I(2))
                    p["fx"] = p["fy"] = s
            elif name == "PrimSetRS2":
                if I(1) is not None:
                    mark(pid, "angle", prevs[1], I(1)); p["angle"] = norm_rot(I(1))
                if I(2) is not None:
                    mark(pid, "fx", prevs[2], I(2)); p["fx"] = clamp_scale(I(2))
                if I(3) is not None:
                    mark(pid, "fy", prevs[3], I(3)); p["fy"] = clamp_scale(I(3))
            elif name == "PrimSetOP":
                if p["type"] == "Sprt":
                    if I(1) is not None:
                        mark(pid, "opx", prevs[1], I(1)); p["opx"] = I(1)
                    if I(2) is not None:
                        mark(pid, "opy", prevs[2], I(2)); p["opy"] = I(2)
                    if I(1) is not None or I(2) is not None:
                        p["attr"] |= 0x02
            elif name == "PrimSetZ":
                if kinds[1] == "int":
                    z = max(100, min(10000, int(num[1])))
                    mark(pid, "z", prevs[1], int(num[1])); p["z"] = z
                    p["attr"] |= 0x04
                elif kinds[1] == "float":
                    p["attr"] |= 0x04
                elif kinds[1] == "nil":
                    p["attr"] &= ~0x04
            elif name == "PrimSetDraw":
                if I(1) is not None:
                    mark(pid, "draw", prevs[1], bool(I(1))); p["draw"] = bool(I(1))
            elif name == "PrimSetAlpha":
                if I(1) is not None and 0 <= I(1) <= 255:
                    mark(pid, "alpha", prevs[1], I(1)); p["alpha"] = I(1)
            elif name == "PrimSetWH":
                if I(1) is not None:
                    mark(pid, "w", prevs[1], I(1)); p["w"] = I(1)
                if I(2) is not None:
                    mark(pid, "h", prevs[2], I(2)); p["h"] = I(2)
                p["attr"] |= 0x01  # WH 同样置 use-rect 位（prim_set_wh 尾）
            elif name == "PrimSetUV":
                if I(1) is not None:
                    mark(pid, "u", prevs[1], I(1)); p["u"] = I(1)
                if I(2) is not None:
                    mark(pid, "v", prevs[2], I(2)); p["v"] = I(2)
                p["attr"] |= 0x01
            elif name == "PrimSetBlend":
                pass  # 混合模式不影响坐标, 记 writes 即可
            elif name == "PrimGroupIn":
                child, grp = I(0), I(1)
                if grp is not None and 0 <= grp <= 4095 and child != grp:
                    g = P(grp)
                    g["type"] = "Group"
                    g["draw"] = True  # init 置位
                    g["x"] = g["y"] = 0  # Group 化清零
                    p["parent"] = grp
            elif name == "PrimGroupOut":
                p["parent"] = None
            elif name == "PrimSetNull":
                prims[pid] = new_prim()
                prims[pid]["draw"] = True  # init 置位（type=None 照样不画）
        idx += 1

    # 输出: app Snapshot 单行 JSON (u/v/off 静态未知, Phase2 用图头回填; 当前为 0)
    out_prims = []
    for pid in sorted(prims):
        p = prims[pid]
        if p["type"] == "Sprt":
            src = p["src"]
            out_prims.append({
                "id": pid, "parent": p["parent"], "draw": p["draw"],
                "x": p["x"], "y": p["y"], "z": p["z"],
                "angle": p["angle"], "fx": p["fx"], "fy": p["fy"],
                "attr": p["attr"], "opx": p["opx"], "opy": p["opy"],
                "u": p["u"], "v": p["v"], "off_x": 0.0, "off_y": 0.0,
                "alpha": p["alpha"], "image": f"g{src}.png" if src >= 0 else "",
                "src": src,
            })
        elif p["type"] == "Group":
            out_prims.append({
                "id": pid, "parent": p["parent"], "draw": p["draw"],
                "x": p["x"], "y": p["y"], "group": True, "image": "",
            })
    snap = {"viewport": {"w": 1280.0, "h": 720.0},
            "camera": {"x": 0, "y": 0, "z": 0}, "prims": out_prims}
    open(out, "w", encoding="utf-8").write(json.dumps(snap, ensure_ascii=False) + "\n")
    json.dump({"texmap": texmap, "addrmap": addrmap},
              open(addrmap_out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    n_sprt = sum(1 for p in prims.values() if p["type"] == "Sprt")
    n_grp = sum(1 for p in prims.values() if p["type"] == "Group")
    print(f"applied={applied} skipped(non-const)={skipped} prims={len(prims)} sprt={n_sprt} group={n_grp} graphload={len(texmap)}")
    print(f"scene -> {out} ({len(out_prims)} prims), map -> {addrmap_out}")


if __name__ == "__main__":
    sys.exit(main())
