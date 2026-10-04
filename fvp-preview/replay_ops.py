#!/usr/bin/env python3
"""脚本序列回放: syscall trace(执行序+实参) -> replay.json(逐行场景快照).

语义与 extract_scene.py 同源 (Sprt 全重置/Nil 保持/rot%3600/scale 越界 1000/
Z 三形态/OP 仅 Sprt/GroupIn 建组清零), 只是值来自动态 trace 而非常量扫描.
行点击 -> 画布渲染执行到该处的场景 ("对每个脚本渲染图片形成系列").

自检: 回放终态 vs vm 活快照逐字段对比, 打印差异 (motion 插值等非 syscall
写入会在此现形, 属已知近似).
"""
import json
import re
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
from extract_scene import new_prim, norm_rot, clamp_scale  # noqa: E402
from trace_join import parse_val, split_args  # noqa: E402

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
TRACKED = set(FIELDS) | {"PrimSetNull", "PrimSetBlend", "PrimGroupIn",
                         "PrimGroupOut", "GraphLoad"}


def short(v):
    kind, val = v
    if kind == "int":
        return str(val)
    if kind == "float":
        return repr(val)
    if kind == "nil":
        return "-"
    if kind == "str":
        s = val if len(val) <= 24 else val[:24] + "…"
        return f'"{s}"'
    return "?"


def main():
    if len(sys.argv) < 3:
        print("usage: replay_ops.py <trace.log> --tex-dir DIR [--out replay.json] [--check live_snapshot.json]")
        return 2
    trace_path = sys.argv[1]
    tex_dir = "/tmp/vmtex"
    out = "replay.json"
    check = None
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--tex-dir":
            tex_dir = args[i + 1]; i += 2
        elif args[i] == "--out":
            out = args[i + 1]; i += 2
        elif args[i] == "--check":
            check = args[i + 1]; i += 2
        else:
            i += 1

    prims = {}
    texmap = {}

    def P(pid):
        return prims.setdefault(pid, new_prim())

    def do_init(pid, typ):
        # prim_init_with_type: 置类型 + draw=true + Group 清 x/y，全程加脏位 0x40。
        p = P(pid)
        p["type"] = typ
        p["draw"] = True
        if typ == "Group":
            p["x"] = p["y"] = 0
        p["attr"] |= 0x40

    def snapshot():
        # 只收 draw=true 的 Sprt + 全部 Group（树形需要）；
        # app 本来就跳过 !draw，视觉等价，体积小两个数量级。
        lst = []
        for pid in sorted(prims):
            p = prims[pid]
            if p["type"] == "Sprt" and p["draw"]:
                src = p["src"]
                lst.append({
                    "id": pid, "parent": p["parent"], "draw": True,
                    "x": p["x"], "y": p["y"], "z": p["z"],
                    "angle": p["angle"], "fx": p["fx"], "fy": p["fy"],
                    "attr": p["attr"], "opx": p["opx"], "opy": p["opy"],
                    "u": p["u"], "v": p["v"], "off_x": 0.0, "off_y": 0.0,
                    "alpha": p["alpha"],
                    "image": f"{tex_dir}/g{src}.png" if src >= 0 else "",
                })
            elif p["type"] == "Group":
                lst.append({"id": pid, "parent": p["parent"], "draw": p["draw"],
                            "x": p["x"], "y": p["y"], "group": True, "image": ""})
        return lst

    re_line = re.compile(r"syscall: (\w+) \[(.*)\]\s*$")
    rows = []
    prev_json = None
    n_ops = 0
    n_emit = 0
    for line in open(trace_path, encoding="utf-8", errors="replace"):
        m = re_line.search(line)
        if not m:
            continue
        name = m.group(1)
        if name not in TRACKED:
            continue
        vals = tuple(parse_val(t) for t in split_args(m.group(2)))
        if any(v[0] == "unknown" for v in vals):
            continue
        kinds = [v[0] for v in vals]
        num = [v[1] for v in vals]

        def I(j):
            return num[j] if j < len(vals) and kinds[j] == "int" else None

        if name == "GraphLoad":
            if kinds[0] == "int" and kinds[1] == "str":
                texmap[str(int(num[0]))] = num[1]
        else:
            pid = I(0)
            if pid is None or not (1 <= pid <= 4095):
                continue
            p = P(pid)
            if name == "PrimSetNull":
                do_init(pid, "None")
            elif name == "PrimGroupOut":
                p["parent"] = None
                p["attr"] |= 0x40  # unlink 副作用
            elif name == "PrimGroupIn":
                grp = I(1)
                if grp is not None and 0 <= grp <= 4095 and pid != grp:
                    do_init(grp, "Group")
                    p["parent"] = grp
                    p["attr"] |= 0x40  # 入组挂链副作用
            elif name == "PrimSetSprt":
                do_init(pid, "Sprt")
                p.update({"opx": 0, "opy": 0, "alpha": 255,
                          "angle": 0, "fx": 1000, "fy": 1000, "u": 0, "v": 0,
                          "w": 0, "h": 0, "z": 1000, "attr": 0,
                          "src": I(1) if I(1) is not None and -2 <= I(1) <= 4095 else -1,
                          "x": I(2) or 0, "y": I(3) or 0})
            elif name == "PrimSetXY":
                if I(1) is not None:
                    p["x"] = I(1)
                if I(2) is not None:
                    p["y"] = I(2)
                p["attr"] |= 0x40
            elif name == "PrimSetRS":
                if I(1) is not None:
                    p["angle"] = norm_rot(I(1))
                if I(2) is not None:
                    p["fx"] = p["fy"] = clamp_scale(I(2))
                p["attr"] |= 0x40
            elif name == "PrimSetRS2":
                if I(1) is not None:
                    p["angle"] = norm_rot(I(1))
                if I(2) is not None:
                    p["fx"] = clamp_scale(I(2))
                if I(3) is not None:
                    p["fy"] = clamp_scale(I(3))
                p["attr"] |= 0x40
            elif name == "PrimSetOP":
                if p["type"] == "Sprt":
                    if I(1) is not None:
                        p["opx"] = I(1)
                    if I(2) is not None:
                        p["opy"] = I(2)
                    if I(1) is not None or I(2) is not None:
                        p["attr"] |= 0x02
                    p["attr"] |= 0x40
            elif name == "PrimSetZ":
                if kinds[1] == "int":
                    p["z"] = max(100, min(10000, int(num[1])))
                    p["attr"] |= 0x04
                elif kinds[1] == "float":
                    p["attr"] |= 0x04
                elif kinds[1] == "nil":
                    p["attr"] &= ~0x04
                p["attr"] |= 0x40
            elif name == "PrimSetDraw":
                if I(1) is not None:
                    p["draw"] = bool(I(1))
            elif name == "PrimSetAlpha":
                if I(1) is not None and 0 <= I(1) <= 255:
                    p["alpha"] = I(1)
            elif name == "PrimSetWH":
                if I(1) is not None:
                    p["w"] = I(1)
                if I(2) is not None:
                    p["h"] = I(2)
                p["attr"] |= 0x01 | 0x40
            elif name == "PrimSetUV":
                if I(1) is not None:
                    p["u"] = I(1)
                if I(2) is not None:
                    p["v"] = I(2)
                p["attr"] |= 0x01 | 0x40
            # PrimSetBlend: 不影响几何, 只记行
        n_ops += 1
        argstr = ",".join(short(v) for v in vals[:4])
        # 自压缩：可视快照不变的行不收（Group 抖动占 65%，视觉无意义）。
        snap = snapshot()
        js = json.dumps(snap, ensure_ascii=False)
        if js != prev_json:
            prev_json = js
            n_emit += 1
            rows.append({"seq": n_ops, "label": f"{name} {argstr}",
                         "prims": snap})

    json.dump({"viewport": {"w": 1280.0, "h": 720.0},
               "camera": {"x": 0, "y": 0, "z": 0}, "rows": rows},
              open(out, "w", encoding="utf-8"), ensure_ascii=False)
    import os as _os
    print(f"ops={n_ops} rows={len(rows)} -> {out} ({_os.path.getsize(out)//1024}KB)")

    if check:
        import os as _os2
        live = {p["id"]: p for p in
                json.load(open(check, encoding="utf-8"))["prims"] if "group" not in p}
        # 视觉集对比：贴图不存在的行在 app 里同样跳过（texture_for 失败），不计差异。
        final = {p["id"]: p for p in rows[-1]["prims"]
                 if "group" not in p and p.get("image")
                 and _os2.path.exists(p["image"])}
        diffs = []
        for pid, lp in sorted(live.items()):
            fp = final.get(pid)
            if fp is None:
                diffs.append((pid, "MISSING", None, None))
                continue
            for k in ("x", "y", "z", "angle", "fx", "fy", "attr",
                      "opx", "opy", "alpha", "draw", "parent"):
                if lp.get(k) != fp.get(k):
                    diffs.append((pid, k, lp.get(k), fp.get(k)))
        for pid in sorted(set(final) - set(live)):
            diffs.append((pid, "EXTRA", None, None))
        print(f"回放终态 vs 活快照: diffs={len(diffs)}")
        for d in diffs[:20]:
            print("  ", d)


if __name__ == "__main__":
    sys.exit(main())
