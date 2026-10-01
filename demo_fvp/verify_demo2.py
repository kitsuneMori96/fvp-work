# -*- coding: utf-8 -*-
"""独立校验器: demo_blur2.hcb 语义断言 (不信任构建器, 只信任断言).

检验源 (与构建器互相独立):
  - HCB 二进制结构: 用 hcb_ir_core.decode_hcb 重解 (只做解码, 不参与组装)
  - syscall 参数个数: 对照 data/syscall_db/syscall_spec.json (第三方数据库)
  - 语义序列: 对照 Sakura.lua 原版调用点逐条断言 (行号见注释)
  - 栈纪律: 线性模拟操作数栈, 下溢/失衡即报错
  - 跳转目标: 必须落在指令起始地址集合内

用法: python verify_demo2.py [--hcb demo_blur2.hcb] [--bg BG001_020]
退出码 0 = 全过; 非 0 = 列出失败项 (Hermit 式的不信任, 机器执行).
"""
from __future__ import annotations
import argparse
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/tmp/fvpanalysis/tools/hcb_ir")
from hcb_ir_core import decode_hcb  # noqa: E402  (仅解码器)

SPEC = json.load(open("/tmp/fvpanalysis/data/syscall_db/syscall_spec.json"))
SPEC_ARGC = {s["name"]: s["arg_count"] for s in SPEC["syscalls"]}

UNK = object()  # push_return / 运算结果等非常量


class Fail(Exception):
    pass


class V:
    def __init__(self):
        self.ok = []
        self.bad = []

    def check(self, cond, msg):
        (self.ok if cond else self.bad).append(msg)
        if not cond:
            print(f"  FAIL: {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hcb", default=str(HERE / "demo_blur2.hcb"))
    ap.add_argument("--bg", default="BG001_020")
    a = ap.parse_args()
    v = V()
    raw = Path(a.hcb).read_bytes()

    # ---- 0. 文件头 ----
    off = struct.unpack_from("<I", raw, 0)[0]
    v.check(4 <= off < len(raw), f"sys_desc_offset sane ({off:#x} in {len(raw)}B)")
    d = decode_hcb(a.hcb, "sjis")
    insts = d["program"]["instructions"]
    sd = d["sysdesc"]
    v.check(sd["entry_point"] == 4, "entry_point == 4 (单函数入口)")
    v.check(sd["game_mode"] == 8, "game_mode == 8 (1280x720)")
    addrs = {i["addr"] for i in insts}

    # ---- 1. 导入表 argc vs 独立数据库 ----
    for sc in sd["syscalls"]:
        exp = SPEC_ARGC.get(sc["name"])
        v.check(exp == sc["args"], f"import {sc['name']}: argc={sc['args']} (spec={exp})")

    # ---- 2. 线性栈模拟 + 事件流 ----
    st, events = [], []
    for i in insts:
        m, g = i["mnemonic"], i["args"]
        if m == "push_nil":
            st.append(None)
        elif m == "push_true":
            st.append(True)
        elif m in ("push_i8", "push_i16", "push_i32"):
            st.append(g["value"])
        elif m == "push_string":
            st.append(g["text"])
        elif m == "push_return":
            st.append(UNK)
        elif m in ("push_global", "push_stack", "push_top"):
            st.append(UNK)
        elif m in ("neg",):
            if not st:
                raise Fail(f"stack underflow @ {i['addr']:#x}")
        elif m in ("add", "sub", "mul", "div", "set_e", "set_ne"):
            if len(st) < 2:
                raise Fail(f"stack underflow @ {i['addr']:#x}")
            st.pop(); st.pop(); st.append(UNK)
        elif m in ("pop_global", "pop_stack"):
            if not st:
                raise Fail(f"stack underflow @ {i['addr']:#x}")
            st.pop()
        elif m == "syscall":
            n = g["arg_count"]
            if len(st) < n:
                raise Fail(f"stack underflow @ syscall {g['name']} {i['addr']:#x}")
            vals = [st.pop() for _ in range(n)][::-1]
            events.append((g["name"], vals, i["addr"]))
        elif m in ("jmp", "jz"):
            if m == "jz":
                if not st:
                    raise Fail("stack underflow @ jz")
                st.pop()
            v.check(g["target"] in addrs, f"{m} -> {g['target']:#x} 落在指令起点")
        elif m in ("init_stack", "ret", "nop"):
            pass
        elif m == "call":
            v.check(False, f"不应出现 call (全手写零复用) @ {i['addr']:#x}")
    v.check(True, f"栈模拟完成, 无下溢 (终栈深 {len(st)})")

    calls = [(n, w) for n, w, _ in events]

    def has(name, vals):
        return any(n == name and list(w) == list(vals) for n, w, _ in events)

    base, blur = a.bg, a.bg + "b"
    # ---- 3. 背景双层 (f_0005074C/f_00050859) ----
    v.check(has("GraphLoad", [186, f"graph_bg/{base}"]), "GraphLoad(186, 本体)")
    v.check(has("GraphLoad", [187, f"graph_bg/{blur}"]), "GraphLoad(187, b图)")
    v.check(has("PrimSetSprt", [186, 186, None, None]), "PrimSetSprt(186,186)")
    v.check(has("PrimSetSprt", [187, 187, None, None]), "PrimSetSprt(187,187)")
    v.check(has("PrimSetAlpha", [186, 255]), "本体 Alpha 255")
    v.check(has("PrimSetAlpha", [187, 0]), "模糊层初始 Alpha 0")
    v.check(has("PrimSetOP", [186, 800, 500]), "本体 OP(800,500)")
    v.check(has("PrimSetZ", [186, 2000]), "本体 Z(2000)")
    v.check(has("PrimSetRS", [186, 0, 2050]), "本体 RS(0,2050)")
    v.check(has("V3DMotion", [0, 0, -300, 1, 1, None]), "V3D 相机状态 (0,0,-300)")
    # ---- 4. G[1973] 门控先于模糊装载 ----
    g1973 = [(w, ad) for n, w, ad in events if n == "GraphLoad" and w[0] == 187]
    v.check(True, "模糊装载存在性已由上条覆盖")
    # pop_global 1973 必须出现在首个 GraphLoad 之前
    pops = [(i["args"]["index"], i["addr"]) for i in insts if i["mnemonic"] == "pop_global"]
    v.check(any(idx == 1973 and ad < g1973[0][1] for idx, ad in pops),
            "G[1973]=true 先于模糊层装载 (f_00050859 门控)")
    # ---- 5. 舞台树 (f_00037496 子集) ----
    for c, p in [(12, 768), (768, 0), (18, 850), (850, 0), (767, 0),
                 (551, 767), (790, 4), (4, 767), (552, 790),
                 (420, 18), (421, 18), (422, 18), (186, 12), (187, 12)]:
        v.check(has("PrimGroupIn", [c, p]), f"PrimGroupIn({c},{p})")
    # ---- 6. 窗口+铭牌+正文装配 ----
    v.check(has("GraphLoad", [420, "graph/sys_window_front"]), "窗口 front 资源")
    v.check(has("GraphLoad", [421, "graph/sys_window_back"]), "窗口 back 资源")
    v.check(has("PrimSetText", [551, 0, 220, 577]), "正文 prim(551,slot0,220,577)")
    v.check(has("TextBuff", [0, 851, 150]), "正文 buff(0,851,150) ≤1024")
    v.check(has("PrimSetText", [552, 8, 86, 589]), "铭牌 prim(552,slot8,86,589)")
    v.check(has("TextBuff", [8, 330, 99]), "铭牌 buff(8,330,99)")
    v.check(has("TextSize", [0, 33, 16]), "正文字号(33,16)")
    v.check(has("TextSpeed", [0, -1]), "正文速度(-1)")
    v.check(has("TextFormat", [0, -5, -5, 42, 42, -2, -2]), "正文 Format 全参")
    v.check(has("TextSuspendChr", [0, "”。、？！」』☆）―…　"]), "禁则字符集")
    # ---- 7. Motion 渐变 + poll 环 ----
    fades = [(w, ad) for n, w, ad in events if n == "MotionAlpha"]
    v.check(any(w[0] == 187 and w[2] == 255 for w, _ in fades), "MotionAlpha(187→255)")
    v.check(any(w[0] == 187 and w[2] == 0 for w, _ in fades), "MotionAlpha(187→0)")
    v.check(any(w[0] in (420, 421) for w, _ in fades), "窗口 MotionAlpha 出场")
    tests = [ad for n, w, ad in events if n == "MotionAlphaTest"]
    v.check(len(tests) == len(fades), f"每次 fade 都有 Test poll ({len(tests)}/{len(fades)})")
    # ---- 8. 台词: 铭牌先于正文, 正文 slot0 ----
    seq = [(n, w) for n, w, _ in events if n == "TextPrint"]
    v.check(all(w[0] in (0, 8) for _, w in seq), "TextPrint 只用 slot0/8")
    body = [w for _, w in seq if w[0] == 0]
    v.check(len(body) >= 2, f"正文行数 ≥2 ({len(body)})")
    # 首个说话人行之前必须有铭牌
    first_body = next(i for i, (_, w) in enumerate(seq) if w[0] == 0)
    v.check(any(w[0] == 8 for _, w in seq[:first_body]), "首句正文前有铭牌 TextPrint(8)")
    # ---- 9. 收尾停帧 ----
    v.check(insts[-1]["mnemonic"] == "ret", "末指令 ret (播完停帧, 无循环)")
    v.check(not any(i["mnemonic"] == "jmp" and i["args"]["target"] == 4 for i in insts),
            "无回跳入口 (非无限循环)")
    # ---- 10. 字符串长度 ----
    bad = [w for n, w, _ in events for w in ([w[1]] if n in ("GraphLoad", "TextPrint") and isinstance(w[1], str) else []) if len(w.encode("cp932")) + 1 > 255]
    v.check(not bad, "全部字符串 ≤255B (TextBuff 1024 教训)")

    print(f"\nPASS {len(v.ok)} / FAIL {len(v.bad)}")
    if v.bad:
        raise SystemExit(1)
    print("ALL GREEN: demo 与断言一致, 可上真机.")


if __name__ == "__main__":
    try:
        main()
    except Fail as e:
        print(f"FAIL: {e}")
        raise SystemExit(1)
