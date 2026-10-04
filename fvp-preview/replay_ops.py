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
                         "PrimGroupOut", "GraphLoad", "GraphRGB",
                         "PartsSelect", "PartsAssign", "PrimSetTile",
                         "PrimSetText", "PrimSetSnow",
                         "MotionAlpha", "MotionAlphaStop", "MotionMove",
                         "MotionMoveStop", "MotionMoveS2", "MotionMoveS2Stop",
                         "MotionMoveZ", "MotionMoveZStop", "MotionMoveR",
                         "MotionMoveRStop", "MotionAnim", "MotionAnimStop",
                         "TextPrint"}
# motion 写操作映射（读 *Test 不写，不管）：引擎每帧 tick 插值改 prim，
# replay 只精确建模 alpha（线性+completed 判定），其余记 approx、check 时剔除。
# TextPrint 不产生几何行（文本渲染二期），但把台词缀到后续行标签上，
# 脚本窗格才有“台词→演出”的对应感。ruby 标记原样保留（忠实）。
MOTION_FIELDS = {"MotionMove": {"x", "y"}, "MotionMoveS2": {"fx", "fy"},
                 "MotionMoveZ": {"z"}, "MotionMoveR": {"angle"},
                 "MotionAnim": {"image"}}


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
    # 贴图时间线（槽复用精确对齐）：
    # vm 逐 tick 扫描 graph 槽，epoch 变化即落 g<tid>_<k>.png + texsave 行；
    # replay 按 op 所在 tick 消费对应 save 的文件名（phantom save 无人消费即无害）。
    # GraphLoad(有名)必 dirty（同名重载也一样）故必有 save；GraphRGB(100,100,100)是
    # 字节级 no-op 无 save；PartsSelect 落空（parts 未载等）无 save——都没 save 就不换图。
    saves = {}        # (tick, tid) -> [path...]（同 tick 同槽至多一份，存 list 容错）
    tick_of = []      # tickends: [(gseq_上限, tick)]，gseq<=上限属该 tick 及之前
    cur_file = {}     # tid -> 当前行应显示的文件
    loaded = {}       # tid -> 槽是否有内容（Nil=卸载后行显示空白）
    parts_target = {} # parts_id -> graph 槽（PartsAssign）
    pending_text = "" # 最近一句台词，缀到下一个发射的行标签上
    amo = {}          # pid -> {src,dst,dur,t0,typ}（alpha motion，精确建模）
    approx = {}       # pid -> set(field)（motion 插值中，check 剔除）
    no_save_warn = 0

    def tick_of_gseq(g):
        t = 0
        for lim, tt in tick_of:
            if g <= lim:
                return tt
            t = tt
        return t

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

    def snapshot(tick_now):
        # 只收 draw=true 且 alpha!=0 的 Sprt（引擎两者都不画）+ 全部 Group；
        # app 本来就跳过 !draw，视觉等价，体积小两个数量级。
        # motion-alpha 已完成的直接写 dst 精确值（线性插值+hold，16ms/tick）；
        # 飞行中的保持指令态（ approx，check 时剔除，行标注见 label）。
        lst = []
        for pid in sorted(prims):
            p = prims[pid]
            if p["type"] == "Sprt" and p["draw"]:
                a = p["alpha"]
                m = amo.get(pid)
                if m is not None and (tick_now - m["t0"]) * 16 >= m["dur"]:
                    a = m["dst"]
                if a == 0:
                    continue
                src = p["src"]
                img = cur_file.get(src, "") if loaded.get(src, False) and src >= 0 else ""
                lst.append({
                    "id": pid, "parent": p["parent"], "draw": True,
                    "x": p["x"], "y": p["y"], "z": p["z"],
                    "angle": p["angle"], "fx": p["fx"], "fy": p["fy"],
                    "attr": p["attr"], "opx": p["opx"], "opy": p["opy"],
                    "u": p["u"], "v": p["v"], "off_x": 0.0, "off_y": 0.0,
                    "alpha": a,
                    "image": img,
                })
            elif p["type"] == "Group":
                lst.append({"id": pid, "parent": p["parent"], "draw": p["draw"],
                            "x": p["x"], "y": p["y"], "group": True, "image": ""})
        return lst

    re_line = re.compile(r"syscall: (\w+) \[(.*)\]\s*$")
    re_tick = re.compile(r"\[vm\] tickend (\d+)\s*$")
    re_save = re.compile(r"\[vm\] texsave tick=(\d+) tid=(\d+) seq=(\d+) -> (\S+)")
    # 预扫：全局 op 序号（每个 syscall 行+1）-> tick 映射 + texsave 收集。
    gseq = 0
    for line in open(trace_path, encoding="utf-8", errors="replace"):
        if re_line.search(line):
            gseq += 1
            continue
        m = re_tick.search(line)
        if m:
            tick_of.append((gseq, int(m.group(1))))
            continue
        m = re_save.search(line)
        if m:
            saves.setdefault((int(m.group(1)), int(m.group(2))), []).append(m.group(4))
    print(f"预扫: ops={gseq} tickends={len(tick_of)} texsaves={sum(len(v) for v in saves.values())}")
    rows = []
    prev_json = None
    n_ops = 0
    n_emit = 0
    gseq = 0
    for line in open(trace_path, encoding="utf-8", errors="replace"):
        m = re_line.search(line)
        if not m:
            continue
        gseq += 1
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
            if kinds[0] == "int":
                tid = int(num[0])
                if 0 <= tid <= 4095:
                    if kinds[1] == "str":
                        texmap[str(tid)] = num[1]
                        loaded[tid] = True
                        key = (tick_of_gseq(gseq), tid)
                        if saves.get(key):
                            cur_file[tid] = saves[key].pop(0)
                        else:
                            # 有名装载必 dirty 必有 save；没有=模型破了，大声报。
                            no_save_warn += 1
                            if no_save_warn <= 5:
                                print(f"  [warn] op#{gseq} GraphLoad {tid} 无对应 texsave")
                    elif kinds[1] == "nil":
                        loaded[tid] = False  # 卸载：行显示空白
        elif name == "GraphRGB":
            if kinds[0] == "int":
                tid = int(num[0])
                if 0 <= tid <= 4095:
                    key = (tick_of_gseq(gseq), tid)
                    if saves.get(key):
                        cur_file[tid] = saves[key].pop(0)
                    # 无 save = (100,100,100) no-op 或槽空，不换图
        elif name == "PartsAssign":
            if I(0) is not None and I(1) is not None:
                parts_target[I(0)] = I(1)
        elif name == "PartsSelect":
            if kinds[0] == "int" and kinds[1] == "int" and 0 <= int(num[1]) < 256:
                tid = parts_target.get(int(num[0]))
                if tid is not None and 0 <= tid <= 4095:
                    key = (tick_of_gseq(gseq), tid)
                    if saves.get(key):
                        cur_file[tid] = saves[key].pop(0)
                    # 无 save = 引擎侧落空（parts 未载等），不换图
        elif name in ("PrimSetTile", "PrimSetText", "PrimSetSnow"):
            # 改类型：引擎绘制序 walker 跳过 Tile/Text/Snow 自身（只遍历孩子），
            # replay 快照同样只收 Sprt+Group，置类型即排除。
            _pid = I(0)
            if _pid is not None and 1 <= _pid <= 4095:
                _p = P(_pid)
                _p["type"] = {"PrimSetTile": "Tile", "PrimSetText": "Text",
                              "PrimSetSnow": "Snow"}[name]
                _p["attr"] |= 0x40
        elif name == "MotionAlpha":
            # (id,src,dst,dur,type)：Nil 回退当前值；dur 非法则引擎直接扔掉；
            # type=1 立即置 src；type=0 线性，completed 由 snapshot 按 tick 结算。
            # 新 motion 到来先结算旧的（重复 fade 最常见：旧的早 completed，值已是 dst）。
            _pid = I(0)
            if _pid is not None and 1 <= _pid <= 4095:
                _p = P(_pid)
                _now = tick_of_gseq(gseq)
                if _pid in amo:
                    _m0 = amo[_pid]
                    if (_now - _m0["t0"]) * 16 >= _m0["dur"]:
                        _p["alpha"] = _m0["dst"]
                        approx.get(_pid, set()).discard("alpha")
                _src = I(1) if I(1) is not None and 0 <= I(1) <= 255 else _p["alpha"]
                _dst = I(2) if I(2) is not None and 0 <= I(2) <= 255 else _p["alpha"]
                _dur = I(3)
                _typ = I(4) if I(4) is not None else 0
                if _dur is not None and 1 <= _dur <= 300000:
                    if _typ == 1:
                        _p["alpha"] = _src
                        amo.pop(_pid, None)
                        approx.get(_pid, set()).discard("alpha")
                    else:
                        amo[_pid] = {"src": _src, "dst": _dst, "dur": _dur,
                                     "t0": _now}
                        approx.setdefault(_pid, set()).add("alpha")
        elif name == "MotionAlphaStop":
            # 停在 completed 之后=精确 dst；否则冻结在飞行值（未知，approx）。
            _pid = I(0)
            if _pid is not None and _pid in amo:
                m = amo.pop(_pid)
                if (tick_of_gseq(gseq) - m["t0"]) * 16 >= m["dur"]:
                    P(_pid)["alpha"] = m["dst"]
                    approx.get(_pid, set()).discard("alpha")
        elif name in MOTION_FIELDS:
            _pid = I(0)
            if _pid is not None and 1 <= _pid <= 4095:
                approx.setdefault(_pid, set()).update(MOTION_FIELDS[name])
        elif name == "TextPrint":
            for _k, _v in vals:
                if _k == "str" and _v.strip():
                    pending_text = _v.strip()[:40]
                    break
        elif name in ("MotionMoveStop", "MotionMoveS2Stop", "MotionMoveZStop",
                      "MotionMoveRStop", "MotionAnimStop"):
            pass  # 冻结值未知，保持 approx（保守）
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
        snap = snapshot(tick_of_gseq(gseq))
        js = json.dumps(snap, ensure_ascii=False)
        if js != prev_json:
            prev_json = js
            n_emit += 1
            _label = f"{name} {argstr}"
            if pending_text:
                _label += f" 💬{pending_text}"
                pending_text = ""
            rows.append({"seq": n_ops, "tick": tick_of_gseq(gseq),
                         "label": _label, "prims": snap})

    json.dump({"viewport": {"w": 1280.0, "h": 720.0},
               "camera": {"x": 0, "y": 0, "z": 0}, "rows": rows},
              open(out, "w", encoding="utf-8"), ensure_ascii=False)
    import os as _os
    print(f"ops={n_ops} rows={len(rows)} -> {out} ({_os.path.getsize(out)//1024}KB)")
    leftover = sum(len(v) for v in saves.values())
    print(f"texsave 对齐: 无save有名装载={no_save_warn} 未消费phantom={leftover}")
    if no_save_warn == 0:
        print("贴图时间线: 精确对齐（每行 tid 指向装载时刻的像素）")

    if check:
        import os as _os2
        live = {p["id"]: p for p in
                json.load(open(check, encoding="utf-8"))["prims"] if "group" not in p}
        # 视觉集对比：贴图不存在的行在 app 里同样跳过（texture_for 失败），不计差异。
        final = {p["id"]: p for p in rows[-1]["prims"]
                 if "group" not in p and p.get("image")
                 and _os2.path.exists(p["image"])}
        diffs = []
        skipped = 0
        final_tick = rows[-1]["tick"] if rows else 0
        for pid, lp in sorted(live.items()):
            fp = final.get(pid)
            if fp is None:
                diffs.append((pid, "MISSING", None, None))
                continue
            skip = set(approx.get(pid, set()))
            # completed 的 alpha-motion 是精确 dst，恢复比对
            if "alpha" in skip and pid in amo:
                m = amo[pid]
                if (final_tick - m["t0"]) * 16 >= m["dur"]:
                    skip.discard("alpha")
            for k in ("x", "y", "z", "angle", "fx", "fy", "attr",
                      "opx", "opy", "alpha", "draw", "parent"):
                if k in skip:
                    skipped += 1
                    continue
                if lp.get(k) != fp.get(k):
                    diffs.append((pid, k, lp.get(k), fp.get(k)))
        for pid in sorted(set(final) - set(live)):
            if "image" in approx.get(pid, set()):
                skipped += 1
                continue
            diffs.append((pid, "EXTRA", None, None))
        print(f"回放终态 vs 活快照: diffs={len(diffs)} (motion插值剔除={skipped})")
        for d in diffs[:20]:
            print("  ", d)


if __name__ == "__main__":
    sys.exit(main())
