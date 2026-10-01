# -*- coding: utf-8 -*-
"""FVP 原生演示 HCB 构建器 v2: 背景模糊差分调用证明 (全手写, 不复用原脚本).

复刻依据 (Sakura.lua 行号 = fvpanalysis/outputs/lua_ir/Sakura.lua):
  背景本体  f_0005074C :161772 (prim 186, Alpha 255)
  背景模糊  f_00050859 :161910 (prim 187, Alpha 0; 头部 G[1973]==true 门控!)
  装载分发  f_00050EDB :162809 -> f_00051038 :162974 -> f_00037294 :118776
            (GraphLoad) + f_00037345 :118845 (PrimSetSprt) -> f_00037476 :119048
            (PrimGroupIn); 调色 f_000510AE :163036 (ColorSet+186-Tile, Sprt 后被覆盖,
            仅 ColorSet(19) 有残留 effect); 收尾 f_00051202 :163212
  舞台树    f_00037496 :119104 (12->768->0; 18->850->0; 551->767->0; 790->4->767)
  消息窗    f_00071272 :223285 (420=sys_window_front, 421=sys_window_back,
            OP(nil,300), XY(0,0), Alpha 0, Blend 0, GroupIn(*,18))
  铭牌      f_00049253 :148884 尾 (PrimSetText(552,8,86,589), OP(552,nil,100),
            slot8 style 全套, Alpha 0) ; 显示 f_00049EC9 :150123 show 分支
            (PrimSetDraw(790,1) + f_000520EE(552,nil,255,200) 等价 MotionAlpha)
  正文      f_0004CB77 :154417 全 nil 默认 (PrimSetText(551,0,220,577),
            TextBuff(0,851,150), style 全套); 显示约定 TextPrint(0,text)
  渐变      MotionAlpha(prim,nil|from,to,ms,nil,nil) + MotionAlphaTest poll
            (照抄 f_0004C9E2 :154300 起; f_0003769F :119287 = ThreadNext)
  对话等待  TextPrint 自带 text wait (click-advance, v1 真机已验证 flow 可走)

与原版有意的差分 ( effect 等价, 逐条列明 ):
  D1 无语音/音频: f_000977B8/f_00055453/AudioSilentOn 全跳 (boot 无声源, 不可闻)
  D2 GraphRGB 跳过: 原版传 G[196..198](boot 未初始化); 新鲜资源默认 tone=中性,
     视觉等价
  D3 f_0004994E/f_00037421 清理照抄 (空槽 no-op); f_000510AE 的 186-Tile 照抄
     (随后被 Sprt 覆盖, 仅 ColorSet(19,nil*4) 残留)
  D4 窗口/铭牌出场用 MotionAlpha 定长淡入 (原版经 f_00071990 状态机, 稳态同);
     窗口 slide-in 若验收指出再补 MotionMove
  D5 模糊渐显用 MotionAlpha(500ms, --fade-ms 可调, 0=instant 还原原版硬切):
     原版 wrapper 路径实为 instant PrimSetAlpha (常被 Dissolve 掩盖); 单机 demo
     用顺滑 fade 修 v1 被投诉的卡顿感
  D6 V3D: 照抄 f_0005261B 的 V3DMotion(0,0,-300,1,1,nil)+全局量 (投影/取景 imu),
     不播 V3D 演示段 (attr=0 不受影响, 论证见前文)
  D7 save/backlog/历史 (f_0008D409/f_00096D1B 等) 跳过: 仅影响回想, 不影响画面

用法:
  python build_fvp_blur_demo2.py --bg BG001_020 \
      --line "あさひ:さて、今夜はシチューだ。たくさん作ったから、いっぱい食べてくれたらうれしいな" \
      --line ":台所からエプロンを外しながらあさひさんがやって来た。"
  --line 格式 "说话人:文本", 说话人空 = 旁白 (铭牌不动, 与原版一致).
  首句建议带说话人 (否则铭牌保持隐藏, 亦与原版一致).

Windows 运行: 备份 Sakura.hcb -> demo_blur2.hcb 改名覆盖 -> Sakura.exe -> 播完停帧 -> 恢复.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/tmp/fvpanalysis/tools/hcb_ir")
from hcb_ir_core import assemble_ir, make_ir  # noqa: E402

NLS = "sjis"
ENC = "cp932"

IMPORTS = [
    ("GraphLoad", 2), ("PrimSetSprt", 4), ("PrimSetXY", 3), ("PrimSetDraw", 2),
    ("PrimSetAlpha", 2), ("PrimGroupIn", 2), ("PrimGroupOut", 1), ("PrimSetNull", 1),
    ("PrimSetOP", 3), ("PrimSetZ", 2), ("PrimSetRS", 3), ("PrimSetBlend", 2),
    ("MotionMoveStop", 1), ("MotionMoveZStop", 1), ("MotionAlphaStop", 1),
    ("MotionAnimStop", 1), ("MotionAlpha", 6), ("MotionAlphaTest", 1),
    ("TextBuff", 3), ("PrimSetText", 4), ("TextPrint", 2), ("TextClear", 1),
    ("TextFont", 3), ("TextColor", 4), ("TextFunction", 4), ("TextSize", 3),
    ("TextOutSize", 3), ("TextShadowDist", 2), ("TextSpeed", 2), ("TextSkip", 2),
    ("TextFormat", 7), ("TextSuspendChr", 2), ("ThreadWait", 1), ("ThreadNext", 0),
    ("V3DMotion", 6), ("ColorSet", 5),
]
SID = {n: i for i, (n, _) in enumerate(IMPORTS)}

SUSPEND = "”。、？！」』☆）―…　"


class B:
    def __init__(self):
        self.ins = []
        self.labels = {}
        self._n = 0

    def emit(self, m, a=None):
        self.ins.append([m, a or {}])

    def label(self, n=None):
        n = n or f"L{self._n}"; self._n += 1
        self.labels[n] = len(self.ins)
        return n

    def push(self, v):
        if v is None:
            self.emit("push_nil", {})
        elif isinstance(v, str):
            raw = v.encode(ENC) + b"\x00"
            assert len(raw) <= 255, f"string too long ({len(raw)}B): {v[:30]}"
            self.emit("push_string", {"length": len(raw), "text": v,
                                      "text_original": v, "raw_hex": raw.hex()})
        else:
            v = int(v)
            if -128 <= v <= 127:
                self.emit("push_i8", {"value": v})
            elif -32768 <= v <= 32767:
                self.emit("push_i16", {"value": v})
            else:
                self.emit("push_i32", {"value": v})

    def popg(self, idx):
        self.emit("pop_global", {"index": idx})

    def sc(self, name, *vals):
        for v in vals:
            self.push(v)
        self.emit("syscall", {"id": SID[name], "name": name,
                              "arg_count": dict(IMPORTS)[name]})

    def fade(self, prim, to_ms, frm=None, dur=300):
        """MotionAlpha(prim,frm,to,dur,nil,nil) + poll (照抄 f_0004C9E2)."""
        self.sc("MotionAlpha", prim, frm, to_ms, dur, None, None)
        top = self.label()
        self.sc("MotionAlphaTest", prim)
        self.emit("push_return", {})
        self.push(True)
        self.emit("set_e", {})
        self.emit("jz", {"label": f"{top}_done"})
        self.sc("ThreadNext")
        self.emit("jmp", {"label": top})
        self.label(f"{top}_done")


def build(base, blur, lines, fade_ms):
    b = B()
    b.emit("init_stack", {"args": 0, "locals": 0})
    # ---- G[1973]=true (blur 使能, f_00050859 门控; f_00039C85 原版 init 等价)
    b.push(True); b.popg(1973)
    # ---- 舞台树 (f_00037496 相关子集; GroupIn 自动建组)
    for c, p in [(12, 768), (768, 0), (18, 850), (850, 0), (767, 0),
                 (551, 767), (790, 4), (4, 767), (552, 790),
                 (420, 18), (421, 18), (422, 18)]:
        b.sc("PrimGroupIn", c, p)
    # ---- 本体 (f_0005074C 全参照抄)
    b.sc("MotionMoveStop", 186); b.sc("MotionMoveZStop", 186); b.sc("MotionAlphaStop", 186)
    b.sc("MotionAnimStop", 210); b.sc("PrimSetAlpha", 210, 0)   # f_0004994E
    b.sc("PrimSetDraw", 788, 0); b.push(0); b.popg(121)
    b.sc("GraphLoad", 186, None); b.sc("PrimGroupOut", 186); b.sc("PrimSetNull", 186)  # f_00037421
    b.sc("ColorSet", 19, None, None, None, None)                # f_000510AE 残留
    # 注: f_000510AE 的 PrimSetTile(186,19,0,0,1380,820) 紧随其后即被
    # PrimSetSprt 覆盖类型, 唯一残留是 ColorSet(19), 故 Tile 本体跳过
    b.sc("GraphLoad", 186, f"graph_bg/{base}")
    b.sc("PrimSetSprt", 186, 186, None, None)
    b.sc("PrimSetOP", 186, 800, 500)
    b.sc("PrimSetXY", 186, 0, 0)
    b.sc("PrimSetZ", 186, 2000)
    b.sc("PrimSetRS", 186, 0, 2050)
    b.sc("PrimSetAlpha", 186, 255)
    b.sc("PrimGroupIn", 186, 12)
    # V3D 相机状态 (f_0005261B 照抄)
    b.push(0); b.popg(310); b.push(True); b.popg(17); b.push(0); b.popg(71)
    b.sc("V3DMotion", 0, 0, -300, 1, 1, None)
    b.push(0); b.popg(1176); b.push(0); b.popg(1177); b.push(-300); b.popg(1178)
    b.push(None); b.popg(17)
    b.push(1); b.popg(310); b.push(0); b.popg(71)
    # ---- 模糊 (f_00050859 全参照抄; 无 V3D, Alpha 0)
    b.sc("MotionMoveStop", 187); b.sc("MotionMoveZStop", 187)
    b.sc("MotionAnimStop", 210); b.sc("PrimSetAlpha", 210, 0)
    b.sc("PrimSetDraw", 788, 0); b.push(0); b.popg(121)
    b.sc("GraphLoad", 187, None); b.sc("PrimGroupOut", 187); b.sc("PrimSetNull", 187)
    b.sc("ColorSet", 19, None, None, None, None)
    b.sc("GraphLoad", 187, f"graph_bg/{blur}")
    b.sc("PrimSetSprt", 187, 187, None, None)
    b.sc("PrimSetOP", 187, 800, 500)
    b.sc("PrimSetXY", 187, 0, 0)
    b.sc("PrimSetZ", 187, 2000)
    b.sc("PrimSetRS", 187, 0, 2050)
    b.sc("PrimSetAlpha", 187, 0)
    b.sc("PrimGroupIn", 187, 12)
    # ---- 消息窗 (f_00071272 照抄; graph/ 前缀 = f_00037294 默认分支)
    for prim, name in ((420, "graph/sys_window_front"), (421, "graph/sys_window_back")):
        b.sc("GraphLoad", prim, name)
        b.sc("PrimSetSprt", prim, prim, None, None)
        b.sc("PrimSetOP", prim, None, 300)
        b.sc("PrimSetXY", prim, 0, 0)
        b.sc("PrimSetAlpha", prim, 0)
        b.sc("PrimSetBlend", prim, 0)
        b.sc("PrimGroupIn", prim, 18)
    # ---- 铭牌 552/slot8 (f_00049253 尾照抄)
    b.sc("PrimSetText", 552, 8, 86, 589)
    b.sc("PrimSetOP", 552, None, 100)
    b.sc("TextBuff", 8, 330, 99)
    b.sc("TextFont", 8, -1, None)
    b.sc("TextColor", 8, 10, 11, 100)
    b.sc("TextSize", 8, 33, None)
    b.sc("TextOutSize", 8, 5, 4)
    b.sc("TextShadowDist", 8, 5)
    b.sc("TextSpeed", 8, 1)
    b.sc("TextFormat", 8, -5, 0, 0, 0, 0, 0)
    b.sc("PrimSetAlpha", 552, 0)
    # ---- 正文 551/slot0 (f_0004CB77 全 nil 默认照抄)
    b.sc("PrimSetText", 551, 0, 220, 577)
    b.sc("TextBuff", 0, 851, 150)
    b.sc("TextFont", 0, -1, None)
    b.sc("TextColor", 0, 10, 11, 100)
    b.sc("TextFunction", 0, 0, 2, 2)
    b.sc("TextSize", 0, 33, 16)
    b.sc("TextOutSize", 0, 5, 4)
    b.sc("TextShadowDist", 0, 5)
    b.sc("TextSpeed", 0, -1)
    b.sc("TextSkip", 0, 3)
    b.sc("TextFormat", 0, -5, -5, 42, 42, -2, -2)
    b.sc("TextSuspendChr", 0, SUSPEND)
    b.push(577); b.popg(52)
    for g in (230, 227, 228, 123, 294, 253, 1224):
        (b.push(0) if g != 294 else b.push(0)); b.popg(g)
    # ---- 窗口出场 (MotionAlpha 300ms, l18 常量)
    b.fade(420, 255, None, 300)
    b.fade(421, 255, None, 300)
    # ---- 台词 (铭牌 TextPrint(8) 等价 f_0004CCDA 尾; 正文 TextPrint(0))
    shown_name = False
    for speaker, text in lines:
        if speaker:
            b.sc("TextClear", 8)
            b.sc("TextPrint", 8, f"　{speaker}　")
            if not shown_name:
                b.sc("PrimSetDraw", 790, 1)   # f_00049EC9 show 分支
                b.fade(552, 255, None, 200)
                shown_name = True
        b.sc("TextPrint", 0, text)
    # ---- 模糊渐显 (外部函数! f_00052882 等价, MotionAlpha 顺滑版 D5)
    if fade_ms > 0:
        b.fade(187, 255, None, fade_ms)
    else:
        b.sc("PrimSetAlpha", 187, 255)   # instant = 原版 wrapper 路径实况
    for speaker, text in lines:
        if speaker:
            b.sc("TextClear", 8)
            b.sc("TextPrint", 8, f"　{speaker}　")
        b.sc("TextPrint", 0, text)
    # ---- 收尾回本体, 停帧
    if fade_ms > 0:
        b.fade(187, 0, 255, fade_ms)
    else:
        b.sc("PrimSetAlpha", 187, 0)
    b.emit("ret", {})
    return b


def assemble(b, title):
    def size_of(m, a):
        if m == "init_stack":
            return 3
        if m in ("call", "jmp", "jz"):
            return 5
        if m == "syscall":
            return 3
        if m in ("push_nil", "push_true", "nop", "ret", "retv", "neg", "add",
                 "sub", "mul", "div", "set_e", "push_return"):
            return 1
        if m == "push_i8":
            return 2
        if m == "push_i16":
            return 3
        if m == "push_i32":
            return 5
        if m == "push_string":
            return 2 + len(a["text"].encode(ENC)) + 1
        if m in ("push_global", "pop_global"):
            return 3
        raise ValueError(m)

    NSP = {"nop": 0, "init_stack": 1, "call": 2, "syscall": 3, "ret": 4,
           "retv": 5, "jmp": 6, "jz": 7, "push_nil": 8, "push_true": 9,
           "push_i32": 0x0A, "push_i16": 0x0B, "push_i8": 0x0C,
           "push_string": 0x0E, "push_global": 0x0F, "pop_global": 0x15,
           "neg": 0x19, "add": 0x1A, "sub": 0x1B, "mul": 0x1C, "div": 0x1D,
           "set_e": 0x22, "push_return": 0x14}
    addr, addrs = 4, []
    for m, a in b.ins:
        addrs.append(addr)
        addr += size_of(m, a)
    la = {n: addrs[i] for n, i in b.labels.items()}
    instructions = []
    for (m, a), ad in zip(b.ins, addrs):
        args = dict(a)
        if m in ("jmp", "jz") and "label" in args:
            args = {"target": la[args["label"]]}
        if m == "push_return":
            args = {}
        instructions.append({"addr": ad, "opcode": NSP[m], "mnemonic": m,
                             "args": args, "size": size_of(m, a)})
    ir = {"schema": "fvp_analysis.hcb_ir.v1", "nls": NLS,
          "program": {"instructions": instructions, "entry_point": 4},
          "sysdesc": {"entry_point": 4, "non_volatile_global_count": 0,
                      "volatile_global_count": 0, "game_mode": 8,
                      "game_mode_reserved": 0, "game_title": title,
                      "game_title_original": title,
                      "syscalls": [{"id": i, "args": c, "name": n,
                                    "name_original": n} for i, (n, c) in enumerate(IMPORTS)],
                      "custom_syscall_count": 0},
          "functions": [], "cfg": {}}
    return assemble_ir(ir, NLS), instructions


def main():
    ap = argparse.ArgumentParser(description="FVP 模糊差分演示 v2 构建器")
    ap.add_argument("--bg", default="BG001_020")
    ap.add_argument("--line", action="append", default=[],
                    help="说话人:文本 (说话人空=旁白), 可重复")
    ap.add_argument("--fade-ms", type=int, default=500)
    ap.add_argument("--out", default=str(HERE / "demo_blur2.hcb"))
    args = ap.parse_args()
    lines = []
    for item in args.line or ["あさひ:さて、今夜はシチューだ。たくさん作ったから、いっぱい食べてくれたらうれしいな",
                              ":台所からエプロンを外しながらあさひさんがやって来た。"]:
        sp, _, tx = item.partition(":")
        lines.append((sp.strip(), tx.strip()))
    base = args.bg
    blur = base if base.endswith("b") else base + "b"
    b = build(base, blur, lines, args.fade_ms)
    blob, instructions = assemble(b, "BG Blur Proof v2 (FVP)")
    out = Path(args.out)
    out.write_bytes(blob)
    print(f"HCB: {out} ({len(blob)} bytes, {len(instructions)} ins)")
    with open(out.with_suffix(".asm.txt"), "w", encoding="utf-8") as f:
        for d in instructions:
            ag = d["args"]
            m = d["mnemonic"]
            if m == "push_string":
                f.write(f"0x{d['addr']:08X}: push_string {ag['text']}\n")
            elif m == "syscall":
                f.write(f"0x{d['addr']:08X}: syscall {ag['name']}\n")
            elif m in ("jmp", "jz"):
                tgt = ag.get("target", "?")
                f.write(f"0x{d['addr']:08X}: {m} 0x{tgt:08X}\n" if isinstance(tgt, int) else f"0x{d['addr']:08X}: {m}\n")
            elif m == "init_stack":
                f.write(f"0x{d['addr']:08X}: init_stack {ag['args']} {ag['locals']}\n")
            elif m.startswith("push_i"):
                f.write(f"0x{d['addr']:08X}: {m} {ag['value']}\n")
            elif m in ("push_global", "pop_global"):
                f.write(f"0x{d['addr']:08X}: {m} {ag['index']}\n")
            else:
                f.write(f"0x{d['addr']:08X}: {m}\n")
    print("ASM: demo_blur2.asm.txt")
    ir2 = make_ir(str(out), NLS)
    print(f"re-decode funcs={len(ir2['functions'])} insts={len(ir2['program']['instructions'])} "
          f"roundtrip_equal={assemble_ir(ir2, NLS) == blob}")


if __name__ == "__main__":
    main()
