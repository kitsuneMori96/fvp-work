#!/usr/bin/env python3
"""脚本序列回放: syscall trace(执行序+实参) -> replay.json(逐行场景快照).

语义与 extract_scene.py 同源 (Sprt 全重置/Nil 保持/rot%3600/scale 越界 1000/
Z 三形态/OP 仅 Sprt/GroupIn 建组清零), 只是值来自动态 trace 而非常量扫描.
行点击 -> 画布渲染执行到该处的场景 ("对每个脚本渲染图片形成系列").

自检: 回放终态 vs vm 活快照逐字段对比, 打印差异 (motion 插值等非 syscall
写入会在此现形, 属已知近似).
"""
import json
import os
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
                         "V3DSet", "V3DMotion", "V3DMotionStop",
                         "TextPrint"}
# motion 写操作映射（读 *Test 不写，不管）：引擎每帧 tick 插值改 prim，
# replay 逐行精确建模（linear 家族 5 easing + alpha 立即型，16ms/tick）。
# TextPrint 强制成行（连贯台词，label 即全文），ruby 标记原样保留（忠实）。

# motion 精确模型（与上游 update() 逐条对齐）：
# - elapsed 按 16ms/tick 累加（headless 无 Ctrl 快进、无 prim 暂停，开场实测无一例）；
# - Rust 整数除法=向零截断（Python // 是向下取整，负数必须 rdiv）；
# - i32 as u8 / as i16 是回绕（mod），不是钳制；
# - 新 motion 到来=旧停新起（elapsed 清零），先按旧值结算 base 再起新；
# - PrimSetNull/Sprt 重置：completed 的记录过期丢弃（引擎不再写），运行中的继续写；
# - Stop：completed→dst，否则冻结在插值（精确值）。
# 只有 MotionAnim（帧动画改显示帧）仍是 approx，check 时整 prim 跳过。


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


def rdiv(n, d):
    # Rust i64 除法（向零截断）；d>0 由调用方保证。
    q = abs(n) // abs(d)
    return -q if (n < 0) != (d < 0) else q


def i16wrap(v):
    return ((v + 32768) % 65536) - 32768


def u8wrap(v):
    return v & 0xFF


def ease(typ, src, dst, e, d):
    # typ: "imm"(alpha 立即型, e>0 即 src) / 1 线性 / 2 加速 / 3 减速 / 4 回弹 / 5 弹跳。
    # 调用方保证 0 < e < d（e<=0 取 base，e>=d 取 dst）。
    if typ == "imm":
        return src
    delta = dst - src
    if typ == 1:
        return src + rdiv(delta * e, d)
    if typ == 2:
        return src + rdiv(delta * e * e, d * d)
    if typ == 3:
        return dst - rdiv(delta * (d - e) * (d - e), d * d)
    hd = rdiv(delta, 2)
    hdur = rdiv(d, 2)
    if typ == 4:
        if e > hdur:
            den = (d - hdur) * (d - hdur)
            return dst - rdiv((delta - hd) * (d - e) * (d - e), den) if den else dst
        den = hdur * hdur
        return src + rdiv(hd * e * e, den) if den else src
    if e > hdur:
        den = (d - hdur) * (d - hdur)
        return hd + src + rdiv((delta - hd) * (e - hdur) * (e - hdur), den) if den else dst
    den = hdur * hdur
    return hd + src - rdiv(hd * (hdur - e) * (hdur - e), den) if den else src


def main():
    if len(sys.argv) < 3:
        print("usage: replay_ops.py <trace.log> --tex-dir DIR [--out replay.json] [--check live_snapshot.json]")
        return 2
    trace_path = sys.argv[1]
    tex_dir = "/tmp/vmtex"
    out = "replay.json"
    check = None
    linemap_path = None
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--tex-dir":
            tex_dir = args[i + 1]; i += 2
        elif args[i] == "--out":
            out = args[i + 1]; i += 2
        elif args[i] == "--check":
            check = args[i + 1]; i += 2
        elif args[i] == "--linemap":
            # P1: hcb_build 产的 .linemap.json（行号->字节区间），给每行定 txt 行号。
            linemap_path = args[i + 1]; i += 2
        else:
            i += 1

    prims = {}
    texmap = {}
    # 贴图时间线（槽复用精确对齐）：
    # vm 逐 tick 扫描 graph 槽，epoch 变化即落 g<tid>_<k>.png + texsave 行；
    # replay 按 op 所在 tick 消费对应 save 的文件名（phantom save 无人消费即无害）。
    # GraphLoad(有名)必 dirty（同名重载也一样）故必有 save；GraphRGB(100,100,100)是
    # 字节级 no-op 无 save；PartsSelect 落空（parts 未载等）无 save——都没 save 就不换图。
    saves = {}        # (tick, tid) -> [{path,w,h,ox,oy,u,v}...]
    cur_file = {}     # tid -> 当前行应显示的文件（已换成 --tex-dir 前缀）
    cur_meta = {}     # tid -> 当前行槽元数据 {w,h,ox,oy,u,v}（off/uv/逻辑尺寸随装载变）
    loaded = {}       # tid -> 槽是否有内容（Nil=卸载后行显示空白）
    def take_save(tid, tick):
        # 消费同 tick 同槽的 save；路径换成 --tex-dir 前缀（vm 落盘在 tex_raw，
        # 归档在 tex/，trace 里是原始路径，直接用会悬空）。
        # phantom save 无人消费即无害。
        key = (tick, tid)
        if saves.get(key):
            m = saves[key].pop(0)
            cur_file[tid] = os.path.join(tex_dir, os.path.basename(m["path"]))
            cur_meta[tid] = m
            return True
        return False

    parts_target = {} # parts_id -> graph 槽（PartsAssign）
    pending_text = "" # 最近一句台词，缀到下一个发射的行标签上
    mot = {}          # (pid, kind) -> {s:[src], d:[dst], dur, t0, typ, base:[t0时刻值]}
                      # kind: alpha/move/s2/z/r（move/s2 双字段同记录）
    cam = [0, 0, 0]   # v3d 当前值（容器 current，插值/Stop/替换都会写它）
    v3dm = None       # {s:[sx,sy,sz], d:[dx,dy,dz], dur, t0, typ}（无 src 参数，src=注册时 current）
    anim_prims = set()  # MotionAnim 碰过的 prim（帧动画，整 prim check 跳过）
    no_save_warn = 0

    def P(pid):
        return prims.setdefault(pid, new_prim())

    # kind -> prim 字段
    KIND_FIELDS = {"alpha": ("alpha",), "move": ("x", "y"),
                   "s2": ("fx", "fy"), "z": ("z",), "r": ("angle",)}
    KIND_WRAP = {"alpha": u8wrap, "move": i16wrap, "s2": i16wrap,
                 "z": i16wrap, "r": i16wrap}

    def mot_value(pid, kind, tick):
        # 记录存在时的精确值；无记录返回 None（用指令态）。
        rec = mot.get((pid, kind))
        if rec is None:
            return None
        e = (tick - rec["t0"]) * 16
        if e <= 0:
            return list(rec["base"])
        if e >= rec["dur"]:
            return list(rec["d"])
        w = KIND_WRAP[kind]
        return [w(ease(rec["typ"], s, d, e, rec["dur"]))
                for s, d in zip(rec["s"], rec["d"])]

    def mot_settle(pid, kind, tick):
        # 把记录结算进指令态（Stop/新 op 到来/重置剪枝用），返回结算值。
        rec = mot.get((pid, kind))
        if rec is None:
            return None
        v = mot_value(pid, kind, tick)
        p = P(pid)
        for f, x in zip(KIND_FIELDS[kind], v):
            p[f] = x
        return v

    def mot_kill_completed(pid, tick):
        # PrimSetNull/Sprt 重置：completed 的引擎不再写，丢弃；运行中的保留。
        for kind in ("alpha", "move", "s2", "z", "r"):
            rec = mot.get((pid, kind))
            if rec is not None and (tick - rec["t0"]) * 16 >= rec["dur"]:
                del mot[(pid, kind)]

    def cur_vals(pid, kind, tick):
        # 引擎“当前值”（fallback 用）：记录值优先，否则指令态。
        v = mot_value(pid, kind, tick)
        if v is not None:
            return v
        p = P(pid)
        return [p[f] for f in KIND_FIELDS[kind]]

    def cam_value(tick):
        # v3d 当前值：Stop/替换/自然完成都 snap 到 dst（与 prim motion 的冻结不同！）。
        if v3dm is None:
            return list(cam)
        e = (tick - v3dm["t0"]) * 16
        if e <= 0:
            return list(v3dm["s"])
        if e >= v3dm["dur"]:
            return list(v3dm["d"])
        out = []
        for s, d in zip(v3dm["s"], v3dm["d"]):
            t = v3dm["typ"]
            if t == 5:
                # 上游链式除法 ((x/k)/k)，与单次除法在截断语义下一致，照抄以保万一。
                # （hdur=0 在插值路径不可达：e>0 则 e>=16>dur=1，早 completed。）
                dd = v3dm["dur"]
                hd = rdiv(d - s, 2)
                hdur = rdiv(dd, 2)
                if e > hdur:
                    k = dd - hdur
                    out.append(hd + s + rdiv(rdiv((d - s - hd) * (e - hdur) * (e - hdur), k), k))
                else:
                    out.append(hd + s - rdiv(rdiv(hd * (hdur - e) * (hdur - e), hdur), hdur))
            else:
                out.append(ease(t, s, d, e, v3dm["dur"]))
        return out

    def do_init(pid, typ, tick):
        # prim_init_with_type 条件语义（引擎 prim.rs:352）：只在类型变化时重置
        # （置类型 + draw=true + Group 清 x/y + 离开 Group 时解散孩子链）；
        # 同类型重入只挂脏位 0x40。旧模型无条件重置，会把 Draw 关掉的组刷成可见
        # （Simple 选项菜单组 328 案：GroupIn[324,328] 在 Draw-off 之后）。
        # motion 记录：completed 的过期（引擎停写），运行中的继续（容器独立）。
        p = P(pid)
        if p["type"] != typ:
            if p["type"] == "Group":
                for q in prims.values():
                    if q.get("parent") == pid:
                        q["parent"] = None
            p["type"] = typ
            p["draw"] = True
            if typ == "Group":
                p["x"] = p["y"] = 0
        p["attr"] |= 0x40
        mot_kill_completed(pid, tick)

    def eff_draw(pid):
        # 引擎 render_tree 语义：子树根 draw=false 则整棵不画。沿 parent 链上溯，
        # 自己或任一祖先 draw=false 即不可见（含组关但自开的情况）。
        seen = set()
        cur = pid
        while cur is not None and cur not in seen:
            seen.add(cur)
            q = prims.get(cur)
            if q is None or not q["draw"]:
                return False
            cur = q["parent"]
        return True

    def snapshot(tick_now, legacy_tex):
        # 只收有效可见（own draw + 祖先链全开）且 alpha!=0 的 Sprt + 全部 Group；
        # motion 记录逐行精确插值（linear 家族+easing，16ms/tick），无 approx。
        lst = []
        for pid in sorted(prims):
            p = prims[pid]
            if p["type"] == "Sprt" and p["draw"] and eff_draw(pid):
                vals = {"x": p["x"], "y": p["y"], "z": p["z"],
                        "angle": p["angle"], "fx": p["fx"], "fy": p["fy"],
                        "alpha": p["alpha"]}
                for kind in ("alpha", "move", "s2", "z", "r"):
                    v = mot_value(pid, kind, tick_now)
                    if v is not None:
                        for f, x in zip(KIND_FIELDS[kind], v):
                            vals[f] = x
                if vals["alpha"] == 0:
                    continue
                src = p["src"]
                if legacy_tex:
                    img = f"{tex_dir}/g{src}.png" if src >= 0 else ""
                    gm = {"w": 0, "h": 0, "ox": 0, "oy": 0}
                else:
                    img = (cur_file.get(src, "") if loaded.get(src, False)
                           and src >= 0 else "")
                    gm = cur_meta.get(src, {"w": 0, "h": 0, "ox": 0, "oy": 0})
                lst.append({
                    "id": pid, "parent": p["parent"], "draw": True,
                    "x": vals["x"], "y": vals["y"], "z": vals["z"],
                    "angle": vals["angle"], "fx": vals["fx"], "fy": vals["fy"],
                    "attr": p["attr"], "opx": p["opx"], "opy": p["opy"],
                    "u": p["u"], "v": p["v"], "w": p["w"], "h": p["h"],
                    "off_x": float(gm["ox"]), "off_y": float(gm["oy"]),
                    "gw": gm["w"], "gh": gm["h"],
                    "alpha": vals["alpha"],
                    "image": img,
                })
            elif p["type"] == "Group":
                lst.append({"id": pid, "parent": p["parent"], "draw": p["draw"],
                            "x": p["x"], "y": p["y"], "group": True, "image": ""})
        return lst

    # P1: syscall 行尾可选 `pc=ADDR`（上游 context.rs 附的 call 指令地址）。
    # 无 pc 的老 trace 照跑（pc=None，前端映射回退）。
    re_line = re.compile(r"syscall: (\w+) \[(.*)\](?: pc=(\d+))?\s*$")
    re_call = re.compile(r"calltrace: from=(\d+) to=(\d+)")
    # P1: txt行号归因。linemap: [{line,start,end}] 相对偏移 + base_off。
    # 规则：op 的 pc 若落进行区间则直接归因；否则沿用最近的剧本区 callsite
    # （calltrace from-5=call指令首字节）。后台线程的站外 callsite 不重置归因
    # （v1 近似：站外 op 极少改变快照；P3 上游 trace 加 ctx id 后精确化）。
    line_segs = []
    if linemap_path:
        try:
            _lm = json.load(open(linemap_path, encoding="utf-8"))
            _base = int(_lm.get("base_off", 0))
            for _e in _lm.get("lines", []):
                _a, _b = _base + int(_e["start"]), _base + int(_e["end"])
                if _b > _a:
                    line_segs.append((_a, _b, int(_e["line"])))
            line_segs.sort()
            print(f"linemap: {len(line_segs)} 行区间")
        except Exception as e:
            print(f"linemap 载入失败（行号映射关闭）: {e}")
    def line_of(addr):
        if addr is None:
            return None
        for _a, _b, _ln in line_segs:
            if _a <= addr < _b:
                return _ln
            if _a > addr:
                break
        return None
    re_tick = re.compile(r"\[vm\] tickend (\d+)\s*$")
    re_save = re.compile(r"\[vm\] texsave tick=(\d+) tid=(\d+) seq=(\d+) -> (\S+)")
    re_meta = re.compile(r"logical (\d+)x(\d+) off (\d+),(\d+) uv (\d+),(\d+)")
    # 预扫：全局 op 序号（每个 syscall 行+1）-> tick 数组 + texsave 收集。
    # 无 tickend 的老 trace 走 legacy（tick 全 0，贴图 g<tid>.png）。
    gseq = 0
    tick_at = []
    tickends = 0
    _cur = 0
    for line in open(trace_path, encoding="utf-8", errors="replace"):
        if re_line.search(line):
            gseq += 1
            tick_at.append(_cur)
            continue
        m = re_tick.search(line)
        if m:
            tickends += 1
            _cur = int(m.group(1)) + 1
            continue
        m = re_save.search(line)
        if m:
            meta = {"path": m.group(4), "w": 0, "h": 0,
                    "ox": 0, "oy": 0, "u": 0, "v": 0}
            mm = re_meta.search(line)
            if mm:
                meta.update({"w": int(mm.group(1)), "h": int(mm.group(2)),
                             "ox": int(mm.group(3)), "oy": int(mm.group(4)),
                             "u": int(mm.group(5)), "v": int(mm.group(6))})
            saves.setdefault((int(m.group(1)), int(m.group(2))), []).append(meta)
    legacy_tex = not tickends and not saves
    print(f"预扫: ops={gseq} tickends={tickends} texsaves={sum(len(v) for v in saves.values())}"
          + (" [legacy 贴图]" if legacy_tex else ""))
    rows = []
    prev_json = None
    n_ops = 0
    n_emit = 0
    gseq = 0
    cur_line = None  # P1: 当前 op 归属的 txt 行号
    for line in open(trace_path, encoding="utf-8", errors="replace"):
        m = re_line.search(line)
        if not m:
            if line_segs:
                mc = re_call.search(line)
                if mc:
                    _site = int(mc.group(1)) - 5
                    _ln = line_of(_site) if _site >= 0 else None
                    if _ln is not None:
                        cur_line = _ln
            continue
        gseq += 1
        name = m.group(1)
        _pc = int(m.group(3)) if m.group(3) else None  # P1: 触发本行的 op 指令地址
        _ln = line_of(_pc)
        if _ln is not None:
            cur_line = _ln  # 内联 op 比 callsite 归因更精确
        if name not in TRACKED:
            continue
        vals = tuple(parse_val(t) for t in split_args(m.group(2)))
        if any(v[0] == "unknown" for v in vals):
            continue
        kinds = [v[0] for v in vals]
        num = [v[1] for v in vals]

        def I(j):
            return num[j] if j < len(vals) and kinds[j] == "int" else None

        _forced = ""  # TextPrint 强制行的 label（空则走常规发射）
        if name == "GraphLoad":
            if kinds[0] == "int":
                tid = int(num[0])
                if 0 <= tid <= 4095:
                    if kinds[1] == "str":
                        texmap[str(tid)] = num[1]
                        loaded[tid] = True
                        if not take_save(tid, tick_at[gseq-1]):
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
                    take_save(tid, tick_at[gseq-1])
                    # 无 save = (100,100,100) no-op 或槽空，不换图
        elif name == "PartsAssign":
            if I(0) is not None and I(1) is not None:
                parts_target[I(0)] = I(1)
        elif name == "PartsSelect":
            if kinds[0] == "int" and kinds[1] == "int" and 0 <= int(num[1]) < 256:
                tid = parts_target.get(int(num[0]))
                if tid is not None and 0 <= tid <= 4095:
                    take_save(tid, tick_at[gseq-1])
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
            # (id,src,dst,dur,type)：0..255 守卫；dur 非法扔掉；
            # typ 缺省/非法一律线性（上游 _ => Linear），只有显式 1 才是立即型。
            # Nil 回退：src -> 调用时刻当前值；dst -> 255（不透明）。
            #   证据：tachie 分镜四次同构 setup（Sprt/GroupIn/OP/XY/Alpha0/淡入），
            #   显式 dst=255 的淡入正常显示（3824行16→3850行255），三次 dst=Nil
            #   全灭；原版游戏截图证 Nil 那次可见。上游 motion_alpha 的 Nil->当前值
            #   在此与原版相悖（conform/0004 同步修正引擎侧）。
            _pid = I(0)
            if _pid is not None and 1 <= _pid <= 4095:
                _now = tick_at[gseq - 1]
                _base = cur_vals(_pid, "alpha", _now)
                mot_settle(_pid, "alpha", _now)
                _src = I(1) if I(1) is not None and 0 <= I(1) <= 255 else _base[0]
                _dst = I(2) if I(2) is not None and 0 <= I(2) <= 255 else (
                    255 if len(kinds) > 2 and kinds[2] == "nil" else _base[0])
                _dur = I(3)
                _typ = I(4) if I(4) == 1 else 0
                _typ = "imm" if _typ == 1 else 0
                if _dur is not None and 1 <= _dur <= 300000:
                    mot[(_pid, "alpha")] = {"s": [_src], "d": [_dst],
                                            "dur": _dur, "t0": _now,
                                            "typ": _typ, "base": _base}
        elif name == "MotionAlphaStop":
            _pid = I(0)
            if _pid is not None:
                mot_settle(_pid, "alpha", tick_at[gseq - 1])
                mot.pop((_pid, "alpha"), None)
        elif name == "MotionMove":
            # (id,sx,sy,dx,dy,dur,typ)：Int 回绕 i16 否则回退；typ 必须显式 0..5
            #（Nil/非法=整 op 扔掉）；0=None 型从不写，也扔掉。
            _pid = I(0)
            if _pid is not None and 1 <= _pid <= 4095:
                _now = tick_at[gseq - 1]
                _base = cur_vals(_pid, "move", _now)
                mot_settle(_pid, "move", _now)
                _s = [i16wrap(I(j)) if I(j) is not None else _base[k]
                      for k, j in enumerate((1, 2))]
                _d = [i16wrap(I(j)) if I(j) is not None else _base[k]
                      for k, j in enumerate((3, 4))]
                _dur, _typ = I(5), I(6)
                if (_dur is not None and 1 <= _dur <= 300000
                        and _typ is not None and 1 <= _typ <= 5):
                    mot[(_pid, "move")] = {"s": _s, "d": _d, "dur": _dur,
                                           "t0": _now, "typ": _typ, "base": _base}
        elif name == "MotionMoveS2":
            # (id,sw,sh,dw,dh,dur,typ)：factor 越界(100..10000外)/Nil 回退当前。
            _pid = I(0)
            if _pid is not None and 1 <= _pid <= 4095:
                _now = tick_at[gseq - 1]
                _base = cur_vals(_pid, "s2", _now)
                mot_settle(_pid, "s2", _now)
                def _f(j, b):
                    v = I(j)
                    return v if v is not None and 100 <= v <= 10000 else b
                _s = [_f(1, _base[0]), _f(2, _base[1])]
                _d = [_f(3, _base[0]), _f(4, _base[1])]
                _dur, _typ = I(5), I(6)
                if (_dur is not None and 1 <= _dur <= 300000
                        and _typ is not None and 1 <= _typ <= 5):
                    mot[(_pid, "s2")] = {"s": _s, "d": _d, "dur": _dur,
                                         "t0": _now, "typ": _typ, "base": _base}
        elif name == "MotionMoveZ":
            # (id,sz,dz,dur,typ)：z 为 i16，Int 回绕。
            _pid = I(0)
            if _pid is not None and 1 <= _pid <= 4095:
                _now = tick_at[gseq - 1]
                _base = cur_vals(_pid, "z", _now)
                mot_settle(_pid, "z", _now)
                _s = [i16wrap(I(1)) if I(1) is not None else _base[0]]
                _d = [i16wrap(I(2)) if I(2) is not None else _base[0]]
                _dur, _typ = I(3), I(4)
                if (_dur is not None and 1 <= _dur <= 300000
                        and _typ is not None and 1 <= _typ <= 5):
                    mot[(_pid, "z")] = {"s": _s, "d": _d, "dur": _dur,
                                        "t0": _now, "typ": _typ, "base": _base}
        elif name == "MotionMoveR":
            # (id,sr,dr,dur,typ)：angle 为 i16，回绕。
            _pid = I(0)
            if _pid is not None and 1 <= _pid <= 4095:
                _now = tick_at[gseq - 1]
                _base = cur_vals(_pid, "r", _now)
                mot_settle(_pid, "r", _now)
                _s = [i16wrap(I(1)) if I(1) is not None else _base[0]]
                _d = [i16wrap(I(2)) if I(2) is not None else _base[0]]
                _dur, _typ = I(3), I(4)
                if (_dur is not None and 1 <= _dur <= 300000
                        and _typ is not None and 1 <= _typ <= 5):
                    mot[(_pid, "r")] = {"s": _s, "d": _d, "dur": _dur,
                                        "t0": _now, "typ": _typ, "base": _base}
        elif name in ("MotionMoveStop", "MotionMoveS2Stop", "MotionMoveZStop",
                      "MotionMoveRStop"):
            _pid = I(0)
            if _pid is not None:
                _k = {"MotionMoveStop": "move", "MotionMoveS2Stop": "s2",
                      "MotionMoveZStop": "z", "MotionMoveRStop": "r"}[name]
                mot_settle(_pid, _k, tick_at[gseq - 1])
                mot.pop((_pid, _k), None)
        elif name == "MotionAnim":
            _pid = I(0)
            if _pid is not None and 1 <= _pid <= 4095:
                anim_prims.add(_pid)
        elif name == "MotionAnimStop":
            pass  # 帧动画不建模，check 整 prim 跳过（保守）
        elif name == "V3DSet":
            # Int 写，Nil 回退当前（插值感知）；motion 不受影响继续写。
            _now = tick_at[gseq - 1]
            _cv = cam_value(_now)
            cam[0] = I(0) if I(0) is not None else _cv[0]
            cam[1] = I(1) if I(1) is not None else _cv[1]
            cam[2] = I(2) if I(2) is not None else _cv[2]
        elif name == "V3DMotion":
            # (dx,dy,dz,dur,typ)：Nil 回退当前；dur/typ 非法整 op 扔掉；
            # 注册必先把旧 motion snap 到 dst（与 prim 的冻结不同！）。
            _now = tick_at[gseq - 1]
            _cv = cam_value(_now)
            cam[0], cam[1], cam[2] = _cv
            _d = [I(j) if I(j) is not None else _cv[k] for k, j in enumerate((0, 1, 2))]
            _dur, _typ = I(3), I(4)
            if (_dur is not None and 1 <= _dur <= 300000
                    and _typ is not None and 0 <= _typ <= 5):
                if _typ == 0:
                    v3dm = None  # None 型从不写；旧的已 snap 到 dst
                else:
                    v3dm = {"s": list(_cv), "d": _d, "dur": _dur,
                            "t0": _now, "typ": _typ}
        elif name == "V3DMotionStop":
            # Stop 即 snap 到 dst（自然完成亦然）。
            if v3dm is not None:
                cam[0], cam[1], cam[2] = v3dm["d"]
                v3dm = None
        elif name == "TextPrint":
            # 台词强制成行（连贯剧本）：快照不变也发射，label 即全文。
            for _k, _v in vals:
                if _k == "str":
                    pending_text = _v.strip()[:40]
                    _forced = f"💬{_v.strip()[:200]}" if _v.strip() else "💬(空行)"
                    break
            else:
                _forced = "💬?"
        else:
            pid = I(0)
            if pid is None or not (1 <= pid <= 4095):
                continue
            p = P(pid)
            if name == "PrimSetNull":
                do_init(pid, "None", tick_at[gseq-1])
            elif name == "PrimGroupOut":
                p["parent"] = None
                p["attr"] |= 0x40  # unlink 副作用
            elif name == "PrimGroupIn":
                grp = I(1)
                if grp is not None and 0 <= grp <= 4095 and pid != grp:
                    do_init(grp, "Group", tick_at[gseq-1])
                    p["parent"] = grp
                    p["attr"] |= 0x40  # 入组挂链副作用
            elif name == "PrimSetSprt":
                do_init(pid, "Sprt", tick_at[gseq-1])
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
        # 自压缩：可视快照不变的行不收（Group 抖动占 65%，视觉无意义）；
        # TextPrint 例外强制收（连贯台词，label 即全文）。
        _tick = tick_at[gseq-1]
        _cv_now = cam_value(_tick)
        snap = snapshot(_tick, legacy_tex)
        js = json.dumps(snap, ensure_ascii=False)
        if js != prev_json or _forced:
            prev_json = js
            n_emit += 1
            if _forced:
                _label = _forced
            else:
                _label = f"{name} {argstr}"
                if pending_text:
                    _label += f" 💬{pending_text}"
                    pending_text = ""
            rows.append({"seq": n_ops, "tick": _tick, "pc": _pc, "line": cur_line,
                         "label": _label, "prims": snap,
                         "camera": {"x": _cv_now[0], "y": _cv_now[1], "z": _cv_now[2]}})

    _final_cam = cam_value(tick_at[gseq-1]) if gseq else [0, 0, 0]
    json.dump({"viewport": {"w": 1280.0, "h": 720.0},
               "camera": {"x": _final_cam[0], "y": _final_cam[1], "z": _final_cam[2]},
               "rows": rows},
              open(out, "w", encoding="utf-8"), ensure_ascii=False)
    import os as _os
    print(f"ops={n_ops} rows={len(rows)} -> {out} ({_os.path.getsize(out)//1024}KB)")
    leftover = sum(len(v) for v in saves.values())
    print(f"texsave 对齐: 无save有名装载={no_save_warn} 未消费phantom={leftover}")
    if no_save_warn == 0:
        print("贴图时间线: 精确对齐（每行 tid 指向装载时刻的像素）")

    if check:
        import os as _os2
        live_all = json.load(open(check, encoding="utf-8"))
        live = {p["id"]: p for p in live_all["prims"] if "group" not in p}
        live_cam = live_all.get("camera", {})
        if (live_cam.get("x"), live_cam.get("y"), live_cam.get("z")) != (
                _final_cam[0], _final_cam[1], _final_cam[2]):
            print(f"  相机失配: live={live_cam} replay={_final_cam}")
            diffs_cam = 1
        else:
            print(f"  相机一致: {_final_cam}")
            diffs_cam = 0
        # 视觉集对比：贴图不存在的行在 app 里同样跳过（texture_for 失败），不计差异。
        final = {p["id"]: p for p in rows[-1]["prims"]
                 if "group" not in p and p.get("image")
                 and _os2.path.exists(p["image"])}
        diffs = []
        skipped = 0
        for pid, lp in sorted(live.items()):
            if pid in anim_prims:
                skipped += 1  # 帧动画改显示帧，不建模，整 prim 跳过
                continue
            fp = final.get(pid)
            if fp is None:
                diffs.append((pid, "MISSING", None, None))
                continue
            for k in ("x", "y", "z", "angle", "fx", "fy", "attr",
                      "opx", "opy", "alpha", "draw", "parent"):
                if lp.get(k) != fp.get(k):
                    diffs.append((pid, k, lp.get(k), fp.get(k)))
        for pid in sorted(set(final) - set(live)):
            if pid in anim_prims:
                skipped += 1
                continue
            diffs.append((pid, "EXTRA", None, None))
        print(f"回放终态 vs 活快照: diffs={len(diffs) + diffs_cam} (帧动画跳过={skipped})")
        for d in diffs[:20]:
            print("  ", d)


if __name__ == "__main__":
    sys.exit(main())
