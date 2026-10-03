#!/usr/bin/env python3
"""conformance 测试场景生成器（手写 YAML + 测试纹理）。

产出 <out-dir>/disassembly.yaml + config.yaml + project.toml + testpix.png，
再用 rfvp assembler 编成 HCB：
  assembler --project-dir <out-dir> --output state.hcb --nls sjis

场景（与 harness/src/main.rs 的期望逐项对应）：
- 100: 纯 Group 容器，偏移 (400,50) —— 测父链累加。注意 Group 化会清 x/y，
  所以容器偏移必须在最后一次 Group 化之后设置（真机行为，已验证）。
- 101: 普通分支 + OP pivot(40,30) + 旋转 900(90°)。
- 102: z 分支 + 贴图 pivot + RS2 非等比 + alpha 200，放在 (0,0) 保证屏内。
- 103: 100 的子 prim，普通分支。
- V3D 相机 (0,0,100)。

syscall id 是 per-HCB import 表序号（真机按名分发），沿用 demo 的编号习惯即可。
"""
import argparse
import os

ENC = "cp932"

IMPORTS = [
    ("GraphLoad", 2), ("PrimSetSprt", 4), ("PrimSetXY", 3), ("PrimSetDraw", 2),
    ("PrimSetAlpha", 2), ("PrimGroupIn", 2), ("PrimGroupOut", 1), ("PrimSetNull", 1),
    ("PrimSetOP", 3), ("PrimSetZ", 2), ("PrimSetRS", 3), ("PrimSetBlend", 2),
    ("MotionMoveStop", 1), ("MotionMoveZStop", 1), ("MotionAlphaStop", 1),
    ("MotionAnimStop", 1), ("MotionAlpha", 6), ("MotionAlphaTest", 1),
    ("TextBuff", 3), ("PrimSetText", 4), ("TextPrint", 2), ("TextClear", 1),
    ("TextFont", 3), ("TextColor", 4), ("TextFunction", 4), ("TextSize", 3),
    ("TextOutSize", 3), ("TextShadowDist", 2), ("TextSpeed", 2),
    ("TextSkip", 2), ("TextFormat", 7), ("TextSuspendChr", 2),
    ("ThreadWait", 1), ("ThreadNext", 0),
    ("V3DMotion", 6), ("ColorSet", 5),
    ("PrimSetRS2", 4), ("V3DSet", 3), ("PrimSetWH", 3),
]


def sz(m, a):
    if m == "init_stack":
        return 3
    if m in ("jmp", "jz"):
        return 5
    if m == "syscall":
        return 3
    if m in ("push_nil", "ret", "push_return"):
        return 1
    if m == "push_i8":
        return 2
    if m == "push_i16":
        return 3
    if m == "push_string":
        return 2 + len(a.encode(ENC)) + 1
    raise ValueError(m)


I = []


def em(m, *ops):
    I.append((m, list(ops)))


def grp(child, parent):
    em("push_i16", child)
    em("push_i16", parent)
    em("syscall", "PrimGroupIn")


def sprt(pid):
    em("push_i16", pid)
    em("push_i16", pid)
    em("push_nil")
    em("push_nil")
    em("syscall", "PrimSetSprt")


def op(pid, x, y):
    em("push_i16", pid)
    em("push_i16", x)
    em("push_i16", y)
    em("syscall", "PrimSetOP")


def xy(pid, x, y):
    em("push_i16", pid)
    em("push_i16", x)
    em("push_i16", y)
    em("syscall", "PrimSetXY")


def wh(pid, w, h):
    em("push_i16", pid)
    em("push_i16", w)
    em("push_i16", h)
    em("syscall", "PrimSetWH")


def rs(pid, r, s):
    em("push_i16", pid)
    em("push_i16", r)
    em("push_i16", s)
    em("syscall", "PrimSetRS")


def rs2(pid, r, sx, sy):
    em("push_i16", pid)
    em("push_i16", r)
    em("push_i16", sx)
    em("push_i16", sy)
    em("syscall", "PrimSetRS2")


def z(pid, v):
    em("push_i16", pid)
    em("push_i16", v)
    em("syscall", "PrimSetZ")


def alpha(pid, v):
    em("push_i16", pid)
    em("push_i16", v)
    em("syscall", "PrimSetAlpha")


def draw(pid, v):
    em("push_i16", pid)
    em("push_i16", v)
    em("syscall", "PrimSetDraw")


def build():
    em("init_stack", 0, 0)
    grp(18, 0)
    grp(100, 18)
    grp(101, 18)
    sprt(101)
    op(101, 40, 30)
    xy(101, 100, 120)
    rs(101, 900, 1000)
    alpha(101, 255)
    draw(101, 1)
    grp(102, 18)
    sprt(102)
    xy(102, 0, 0)
    z(102, 1100)
    rs2(102, 0, 2000, 500)
    alpha(102, 200)
    draw(102, 1)
    grp(103, 100)
    sprt(103)
    xy(103, 150, 0)
    wh(103, 200, 150)
    alpha(103, 255)
    draw(103, 1)
    xy(100, 400, 50)  # 容器偏移：必须在最后一次 Group 化之后
    em("push_i8", 0)
    em("push_i8", 0)
    em("push_i16", 100)
    em("syscall", "V3DSet")
    em("ret")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--title", default="conform-state")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    build()

    addr = 4
    lines = ["- address: 4", "  args_count: 0", "  locals_count: 0", "  insts:"]
    for m, ops in I:
        lines.append(f"  - address: {addr}")
        lines.append(f"    mnemonic: {m}")
        if m == "init_stack":
            lines += ["    operands:", f"    - '{ops[0]}'", f"    - '{ops[1]}'"]
        elif m in ("push_i8", "push_i16"):
            lines += ["    operands:", f"    - '{ops[0]}'"]
        elif m == "syscall":
            lines += ["    operands:", f"    - {ops[0]}"]
        else:
            lines.append("    operands: []")
        addr += sz(m, ops[0] if m == "push_string" else None)
    with open(os.path.join(args.out_dir, "disassembly.yaml"), "w") as f:
        f.write("\n".join(lines) + "\n")

    cfg = [
        "entry_point: 4",
        "non_volatile_global_count: 0",
        "volatile_global_count: 0",
        "game_mode: 8",
        "game_mode_reserved: 0",
        f"game_title: {args.title}",
        "syscalls:",
    ]
    for i, (n, c) in enumerate(IMPORTS):
        cfg += [f"- id: {i}", f"  name: {n}", f"  args_count: {c}"]
    cfg.append("custom_syscall_count: 0")
    with open(os.path.join(args.out_dir, "config.yaml"), "w") as f:
        f.write("\n".join(cfg) + "\n")

    with open(os.path.join(args.out_dir, "project.toml"), "w") as f:
        f.write('config_file = "config.yaml"\ndisassembly_file = "disassembly.yaml"\n')

    # 测试纹理：200x150 纯红 + 白 X（harness 用近邻+均匀性过滤处理十字线）。
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (200, 150), (255, 0, 0, 255))
    d = ImageDraw.Draw(im)
    d.line([0, 0, 200, 150], fill=(255, 255, 255, 255), width=6)
    d.line([0, 150, 200, 0], fill=(255, 255, 255, 255), width=6)
    im.save(os.path.join(args.out_dir, "testpix.png"))
    print(f"scene written to {args.out_dir} ({len(I)} insts)")


if __name__ == "__main__":
    main()
