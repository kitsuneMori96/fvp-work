# -*- coding: utf-8 -*-
"""FVP 原生演示 HCB 构建器: 背景模糊差分调用证明.

复刻原作链 (Sakura_dump.txt:function_1064_ / Sakura.lua:f_0001063E):
  function_4854_/f_0005074C : prim 186 = 本体  (GraphLoad+PrimSetSprt+PrimSetAlpha 255)
  function_4855_/f_00050859 : prim 187 = 模糊b (GraphLoad+PrimSetSprt+PrimSetAlpha 0, 初始隐藏)
  f_00052882 : PrimSetAlpha(187) 渐显 = 对话中切模糊的真正执行者
  对话 f_0004CEFD 本身不碰 186/187; V3D 只是相机 (docs/syscalls/31_V3D.md).

剧本 (任选一段台词+背景, 取共通线 BG001_020):
  Act1 本体 + あさひ台词 x2 (证明对话本身不致模糊)
  Act2 外部函数 alpha ramp -> 模糊 + 同一句台词重播 (证明切换来自外部函数)
  Act3 V3DMotion 微动 (对 attr=0 的 prim 无视觉影响, 证明 V3D 不产模糊) -> 回本体 -> 循环

产物: demo_blur.hcb (FVP 字节码, 可直接给 Sakura.exe 跑, 见底部运行说明)
      demo_blur.asm.txt (人类可读清单, 与 Sakura_dump.txt 同风格)
验证: 用 fvpanalysis hcb_ir_core.make_ir 回解 + roundtrip 字节一致.

Windows 运行 (FVP 引擎, 在 D:\\soft\\Sakura moyu 下执行):
  1. 备份: copy Sakura.hcb Sakura.hcb.bak
  2. 替换: copy demo_fvp\\demo_blur.hcb Sakura.hcb
  3. 运行: Sakura.exe  (资源 graph_bg.bin 里本来就有 BG001_020/b, 无需拷资源)
  4. 恢复: copy Sakura.hcb.bak Sakura.hcb
注意: 本机 Linux 容器无 Wine 跑不了 Sakura.exe, 这里只保证 HCB 结构合法
      (回解+roundtrip 全过); 真机运行效果以 Sakura.exe 窗口为准.
"""
from __future__ import annotations
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/tmp/fvpanalysis/tools/hcb_ir")
from hcb_ir_core import assemble_ir, make_ir  # noqa: E402

NLS = "sjis"
ENC = "cp932"

# 台词 (Sakura_dump.txt:314126,314130, 原文照抄)
LINE1 = "さて、今夜はシチューだ。たくさん作ったから、いっぱい食べてくれたらうれしいな"
LINE2 = "台所からエプロンを外しながらあさひさんがやって来た。"

BASE = "BG001_020"
BLUR = BASE + "b"

IMPORTS = [  # (name, argc) —— argc 错则 VM 弹栈错乱, 已按 syscall_spec.json 核对
    ("GraphLoad", 2),
    ("PrimSetSprt", 4),
    ("PrimSetXY", 3),
    ("PrimSetDraw", 2),
    ("PrimSetAlpha", 2),
    ("PrimGroupIn", 2),
    ("TextBuff", 3),
    ("PrimSetText", 4),
    ("TextPrint", 2),
    ("ThreadWait", 1),
    ("V3DMotion", 6),
]
SID = {n: i for i, (n, _) in enumerate(IMPORTS)}


def push(v):
    if v is None:
        return ("push_nil", {})
    if isinstance(v, str):
        raw = v.encode(ENC) + b"\x00"
        assert len(raw) <= 255, f"string too long: {v[:20]} ({len(raw)}B)"
        return ("push_string", {"length": len(raw), "text": v,
                                "text_original": v, "raw_hex": raw.hex()})
    v = int(v)
    if -128 <= v <= 127:
        return ("push_i8", {"value": v})
    if -32768 <= v <= 32767:
        return ("push_i16", {"value": v})
    return ("push_i32", {"value": v})


def sc(name):
    return ("syscall", {"id": SID[name], "name": name,
                        "arg_count": dict(IMPORTS)[name]})


def main():
    ins = []  # (mnemonic, args) ; jmp 用 ("jmp", {"label": name}) 占位
    labels = {}

    def emit(m, a=None):
        ins.append([m, a or {}])

    def label(n):
        labels[n] = len(ins)

    def call_sys(name, *vals):
        for v in vals:
            emit(*push(v))
        emit(*sc(name))

    emit("init_stack", {"args": 0, "locals": 0})
    # ---- 预加载 = f_0001063E 尾部: 本体进 186 (可见), b 进 187 (隐藏)
    call_sys("GraphLoad", 186, f"graph_bg/{BASE}")      # f_00037294(a3=2 -> graph_bg/)
    call_sys("PrimSetSprt", 186, 186, 0, 0)             # f_00037345
    call_sys("PrimSetXY", 186, 0, 0)
    call_sys("PrimSetDraw", 186, 1)
    call_sys("PrimSetAlpha", 186, 255)                  # 本体不透明
    call_sys("PrimGroupIn", 186, 0)                     # 原作是 12 (舞台树已建); demo 用根 0, 必在渲染树内
    call_sys("GraphLoad", 187, f"graph_bg/{BLUR}")
    call_sys("PrimSetSprt", 187, 187, 0, 0)
    call_sys("PrimSetXY", 187, 0, 0)
    call_sys("PrimSetDraw", 187, 1)
    call_sys("PrimSetAlpha", 187, 0)                    # 模糊层初始隐藏 = f_00050859 尾
    call_sys("PrimGroupIn", 187, 0)
    # ---- 文本槽 (docs/syscalls/26_Text.md §2.2 四件套)
    call_sys("TextBuff", 0, 1000, 120)  # 引擎限制: 单边≤1024 (超了就弹窗报错)
    call_sys("PrimSetText", 400, 0, 90, 540)
    call_sys("PrimGroupIn", 400, 0)

    label("loop")
    # Act1: 对话本身不切背景 (f_0004CEFD 只写文本槽)
    call_sys("TextPrint", 0, LINE1)
    call_sys("ThreadWait", 2000)
    call_sys("TextPrint", 0, LINE2)
    call_sys("ThreadWait", 2000)
    # Act2: 外部函数 = f_00052882 (PrimSetAlpha ramp) + f_00051202 意图 (187 已在树顶)
    for a in (64, 128, 192, 255):
        call_sys("PrimSetAlpha", 187, a)
        call_sys("ThreadWait", 200)
    call_sys("TextPrint", 0, LINE1)   # 同一句台词, 背景已是模糊 -> 切换来自外部函数
    call_sys("ThreadWait", 2500)
    # Act3: V3D 只是相机; 我们的 prim attr=0 (PrimSetSprt 初始化), 故无视觉影响, 更无模糊
    call_sys("V3DMotion", None, None, 1600, 1500, 1, None)
    call_sys("ThreadWait", 1800)
    call_sys("V3DMotion", None, None, 2000, 1500, 1, None)
    call_sys("ThreadWait", 600)
    call_sys("PrimSetAlpha", 187, 0)  # 回本体
    call_sys("ThreadWait", 800)
    ins.append(["jmp", {"label": "loop"}])
    emit("ret", {})

    # ---- 地址分配 (code 从文件偏移 4 开始) ----
    def size_of(m, a):
        if m == "init_stack":
            return 3
        if m in ("call", "jmp", "jz"):
            return 5
        if m == "syscall":
            return 3
        if m == "push_nil" or m in ("nop", "ret", "retv", "neg", "add"):
            return 1
        if m == "push_i8":
            return 2
        if m == "push_i16":
            return 3
        if m == "push_i32":
            return 5
        if m == "push_string":
            raw = a["text"].encode(ENC) + b"\x00"
            return 2 + len(raw)
        raise ValueError(m)

    label_addr = {}
    addr = 4
    addrs = []
    for m, a in ins:
        addrs.append(addr)
        addr += size_of(m, a)
    for n, i in labels.items():
        label_addr[n] = addrs[i]

    NSP = {"nop": 0, "ret": 4, "retv": 5, "neg": 0x19, "add": 0x1A,
           "init_stack": 1, "call": 2, "syscall": 3, "jmp": 6, "jz": 7,
           "push_nil": 8, "push_true": 9, "push_i32": 0x0A, "push_i16": 0x0B,
           "push_i8": 0x0C, "push_string": 0x0E}
    instructions = []
    for (m, a), ad in zip(ins, addrs):
        args = dict(a)
        if m == "jmp":
            args = {"target": label_addr[args["label"]]}
        instructions.append({"addr": ad, "opcode": NSP[m], "mnemonic": m,
                             "args": args, "size": size_of(m, a)})

    ir = {"schema": "fvp_analysis.hcb_ir.v1", "nls": NLS,
          "program": {"instructions": instructions,
                      "entry_point": instructions[0]["addr"]},
          "sysdesc": {"entry_point": instructions[0]["addr"],
                      "non_volatile_global_count": 0,
                      "volatile_global_count": 0,
                      "game_mode": 8, "game_mode_reserved": 0,
                      "game_title": "BG Blur Proof (FVP)",
                      "game_title_original": "BG Blur Proof (FVP)",
                      "syscalls": [{"id": i, "args": c, "name": n,
                                    "name_original": n} for i, (n, c) in enumerate(IMPORTS)],
                      "custom_syscall_count": 0},
          "functions": [], "cfg": {}}
    blob = assemble_ir(ir, NLS)
    out = HERE / "demo_blur.hcb"
    out.write_bytes(blob)
    print(f"HCB: {out} ({len(blob)} bytes, {len(instructions)} ins)")

    # ---- 人类可读清单 + 回解验证 ----
    with open(HERE / "demo_blur.asm.txt", "w", encoding="utf-8") as f:
        f.write(f"# entry=0x{instructions[0]['addr']:08X} game_mode=8 (1280x720)\n")
        for d in instructions:
            ag = d["args"]
            if d["mnemonic"] == "push_string":
                f.write(f"0x{d['addr']:08X}: push_string {ag['text']}\n")
            elif d["mnemonic"] == "syscall":
                f.write(f"0x{d['addr']:08X}: syscall {ag['name']}\n")
            elif d["mnemonic"] in ("jmp",):
                f.write(f"0x{d['addr']:08X}: jmp 0x{ag['target']:08X}\n")
            elif d["mnemonic"] == "init_stack":
                f.write(f"0x{d['addr']:08X}: init_stack {ag['args']} {ag['locals']}\n")
            elif d["mnemonic"].startswith("push_i"):
                f.write(f"0x{d['addr']:08X}: {d['mnemonic']} {ag['value']}\n")
            else:
                f.write(f"0x{d['addr']:08X}: {d['mnemonic']}\n")
    print("ASM: demo_blur.asm.txt")

    ir2 = make_ir(str(out), NLS)   # 回解
    rb = assemble_ir(ir2, NLS)     # 再组装
    print(f"re-decode funcs={len(ir2['functions'])} insts={len(ir2['program']['instructions'])} "
          f"roundtrip_equal={rb == blob}")


if __name__ == "__main__":
    main()
