#!/usr/bin/env python3
"""动态溯源 join: syscall trace(动态名+实参, 序) + 反汇编(静态常量源) -> 可写回 addrmap.

规则 (宁可拒, 绝不错):
  每个 (prim,field) 取 trace 序里 LAST write 的 (name, argtuple).
  全工程找静态 const 站点 (紧邻 k 个常量 push, 值全等): 恰好 1 个 -> 可写回;
  0 个 (动态计算) / 多个 (歧义, 循环/双路径) -> 拒绝, 记原因.
"""
import json
import re
import sys
import yaml

PUSH_INT = {"push_i8": 1, "push_i16": 2, "push_i32": 4}

# field 在 arg 中的位置 (与 extract_scene.py 同语义)
FIELDS = {
    "PrimSetSprt": (("src", 1), ("x", 2), ("y", 3)),
    "PrimSetXY": (("x", 1), ("y", 2)),
    "PrimSetRS": (("angle", 1), ("fx", 2), ("fy", 2)),
    "PrimSetRS2": (("angle", 1), ("fx", 2), ("fy", 3)),
    "PrimSetOP": (("opx", 1), ("opy", 2)),
    "PrimSetZ": (("z", 1),),
    "PrimSetDraw": (("draw", 1),),
    "PrimSetAlpha": (("alpha", 1),),
    "PrimSetWH": (("w", 1), ("h", 2)),
    "PrimSetUV": (("u", 1), ("v", 2)),
}
NUMERIC = {"src", "x", "y", "angle", "fx", "fy", "opx", "opy", "z",
           "draw", "alpha", "w", "h", "u", "v"}


def parse_val(tok):
    tok = tok.strip()
    m = re.fullmatch(r"Int\((-?\d+)\)", tok)
    if m:
        return ("int", int(m.group(1)))
    m = re.fullmatch(r"Float\(([^)]+)\)", tok)
    if m:
        return ("float", float(m.group(1)))
    if tok == "Nil":
        return ("nil", None)
    m = re.fullmatch(r'String\("(.*)"\)', tok, re.S)
    if m:
        return ("str", m.group(1).replace('\\"', '"'))
    m = re.fullmatch(r"ConstString\(\"(.*)\", \d+\)", tok, re.S)
    if m:
        return ("str", m.group(1).replace('\\"', '"'))
    if tok in ("True", "False"):
        return ("int", 1 if tok == "True" else 0)
    return ("unknown", None)


def split_args(s):
    parts, depth, cur, instr = [], 0, "", False
    i = 0
    while i < len(s):
        c = s[i]
        if instr:
            cur += c
            if c == '"' and s[i - 1] != "\\":
                instr = False
            i += 1
            continue
        if c == '"':
            instr = True
            cur += c
        elif c == "(":
            depth += 1
            cur += c
        elif c == ")":
            depth -= 1
            cur += c
        elif c == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    if cur.strip():
        parts.append(cur)
    return parts


def load_static(proj):
    cfg = yaml.safe_load(open(f"{proj}/config.yaml", encoding="utf-8"))
    argc = {s["name"]: s["args_count"] for s in cfg["syscalls"]}
    re_addr = re.compile(r"^(?:- |  - )address: (\d+)\s*$")
    re_mnem = re.compile(r"^    mnemonic: (\S+)\s*$")
    re_op = re.compile(r"^    - (?:'(.*)'|(\S+))\s*$")
    insts = []
    addr = mnem = None
    ops = None
    in_ops = False
    with open(f"{proj}/disassembly.yaml", encoding="utf-8") as f:
        for line in f:
            m = re_addr.match(line)
            if m:
                if mnem is not None:
                    insts.append({"address": addr, "mnemonic": mnem,
                                  "operands": ops or []})
                addr = int(m.group(1)); mnem = None; ops = None; in_ops = False
                continue
            m = re_mnem.match(line)
            if m:
                mnem = m.group(1); ops = [] if mnem != "syscall" else None
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
    by_addr = {t["address"]: i for i, t in enumerate(insts)}
    # 目标 syscall 的 const 站点: key (name, tuple) -> [site]
    sites = {}
    for idx, ins in enumerate(insts):
        if ins["mnemonic"] != "syscall" or not ins["operands"]:
            continue
        name = ins["operands"][0]
        if name not in FIELDS and name != "GraphLoad":
            continue
        k = argc.get(name, 0)
        prevs = insts[max(0, idx - k):idx]
        if len(prevs) < k:
            continue
        tup, ok = [], True
        addrs = []
        for p in prevs:
            pm = p["mnemonic"]
            if pm in PUSH_INT:
                try:
                    v = int((p["operands"] or ["x"])[0])
                except ValueError:
                    ok = False; break
                tup.append(("int", v))
            elif pm == "push_f32":
                try:
                    v = float((p["operands"] or ["x"])[0])
                except ValueError:
                    ok = False; break
                tup.append(("float", v))
            elif pm == "push_nil":
                tup.append(("nil", None))
            elif pm == "push_string":
                tup.append(("str", (p["operands"] or [""])[0]))
            else:
                ok = False; break
            addrs.append((p["address"], pm))
        if not ok:
            continue
        sites.setdefault((name, tuple(tup)), []).append(
            {"pc": ins["address"], "args": addrs})
    return sites


def main():
    if len(sys.argv) < 3:
        print("usage: trace_join.py <project_dir> <trace.log> [--out traced_addrmap.json]")
        return 2
    proj, trace_path = sys.argv[1], sys.argv[2]
    out = "traced_addrmap.json"
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]

    print("静态站点索引中...", flush=True)
    sites = load_static(proj)
    n_sites = sum(len(v) for v in sites.values())
    print(f"const 站点总数: {n_sites}", flush=True)

    re_line = re.compile(r"syscall: (\w+) \[(.*)\]\s*$")
    last_write = {}   # (pid, field) -> (name, tuple, order)
    prim_type = {}
    texmap = {}
    order = 0
    tracked = set(FIELDS) | {"PrimSetNull", "PrimGroupIn", "PrimGroupOut", "GraphLoad"}
    for line in open(trace_path, encoding="utf-8", errors="replace"):
        m = re_line.search(line)
        if not m:
            continue
        name, raw = m.group(1), m.group(2)
        if name not in tracked:
            continue
        vals = tuple(parse_val(t) for t in split_args(raw))
        if any(v[0] == "unknown" for v in vals):
            continue
        order += 1
        if name == "GraphLoad":
            if len(vals) >= 2 and vals[0][0] == "int" and vals[1][0] == "str":
                texmap[str(vals[0][1])] = vals[1][1]
            continue
        if not vals or vals[0][0] != "int":
            continue
        pid = vals[0][1]
        if not (1 <= pid <= 4095):
            continue
        if name == "PrimSetNull":
            prim_type[pid] = "None"
            continue
        if name == "PrimGroupOut":
            continue
        if name == "PrimGroupIn":
            if len(vals) >= 2 and vals[1][0] == "int":
                prim_type[vals[1][1]] = "Group"
            continue
        if name == "PrimSetSprt":
            prim_type[pid] = "Sprt"
        for field, j in FIELDS[name]:
            if j >= len(vals):
                continue
            kind, v = vals[j]
            if field not in NUMERIC:
                continue
            if kind == "nil":
                continue  # Nil=保持, 无源可改
            if kind not in ("int", "float"):
                continue
            if name == "PrimSetOP" and prim_type.get(pid) != "Sprt":
                continue  # 非 Sprt 上 OP 无效
            last_write[(pid, field)] = (name, vals, order)

    addrmap, refused = {}, {"dynamic": 0, "ambiguous": 0}
    for (pid, field), (name, vals, _) in sorted(last_write.items()):
        key = (name, vals)
        cands = sites.get(key, [])
        if len(cands) == 1:
            site = cands[0]
            # 找到 field 对应 arg 位的 push 地址/宽度
            for f, j in FIELDS[name]:
                if f != field:
                    continue
                addr, pm = site["args"][j]
                kind, v = vals[j]
                width = PUSH_INT.get(pm, 4 if kind == "float" else 0)
                if width == 0:
                    refused["dynamic"] += 1
                    break
                old = v if kind == "int" else float(v)
                addrmap.setdefault(str(pid), {})[field] = {
                    "addr": addr, "mnemonic": pm, "width": width,
                    "old": old, "via": f"trace:{name}",
                }
        elif len(cands) == 0:
            refused["dynamic"] += 1
        else:
            refused["ambiguous"] += 1

    json.dump({"addrmap": addrmap, "texmap": texmap},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_live_fields = len(last_write)
    print(f"动态 last-write 字段: {n_live_fields}, 可写回: {sum(len(v) for v in addrmap.values())}, "
          f"拒绝(dynamic={refused['dynamic']}, ambiguous={refused['ambiguous']})")
    print(f"texmap slots: {len(texmap)} -> {out}")


if __name__ == "__main__":
    sys.exit(main())
