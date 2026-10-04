#!/usr/bin/env python3
"""立绘端到端验证场景（BG + 双立绘不同参数）。

产出 <out-dir>/disassembly.yaml + config.yaml + project.toml，
再用 rfvp assembler 编成 HCB：
  assembler --project-dir <out-dir> --output tachie.hcb --nls sjis

流程（抄真机：GraphLoad → PartsLoad → PartsAssign → PartsSelect → Sprt/摆位）：
- 186: BG（graph_bg/BG001_020，1600x900 经 RS 800 缩到 1280x720 满屏）。
- 201: 立绘A（graph_vis/ASAHI_e201a，左，100%，不透明）。
- 202: 立绘B（graph_vis/AZUSA_e201a1，右，80%，alpha 200）。
- V3D 相机默认 (0,0,0)，全普通分支。
"""
import argparse
import os

ENC = "cp932"

IMPORTS = [
    ("GraphLoad", 2), ("PrimSetSprt", 4), ("PrimSetXY", 3), ("PrimSetDraw", 2),
    ("PrimSetAlpha", 2), ("PrimGroupIn", 2), ("PrimSetOP", 3), ("PrimSetRS", 3),
    ("PartsLoad", 2), ("PartsSelect", 2), ("PartsAssign", 2), ("V3DSet", 3),
]


def sz(m, a):
    if m == "init_stack":
        return 3
    if m == "syscall":
        return 3
    if m in ("push_nil", "ret"):
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


def gload(pid, path):
    em("push_i16", pid)
    em("push_string", path)
    em("syscall", "GraphLoad")


def sprt(pid):
    em("push_i16", pid)
    em("push_i16", pid)
    em("push_nil")
    em("push_nil")
    em("syscall", "PrimSetSprt")


def xy(pid, x, y):
    em("push_i16", pid)
    em("push_i16", x)
    em("push_i16", y)
    em("syscall", "PrimSetXY")


def rs(pid, r, s):
    em("push_i16", pid)
    em("push_i16", r)
    em("push_i16", s)
    em("syscall", "PrimSetRS")


def alpha(pid, v):
    em("push_i16", pid)
    em("push_i16", v)
    em("syscall", "PrimSetAlpha")


def draw(pid, v):
    em("push_i16", pid)
    em("push_i16", v)
    em("syscall", "PrimSetDraw")


def tachie(prim, slot, path, entry, x, y, scale, a):
    gload(prim, path)
    em("push_i8", slot)
    em("push_string", path)
    em("syscall", "PartsLoad")
    em("push_i8", slot)
    em("push_i16", prim)
    em("syscall", "PartsAssign")
    em("push_i8", slot)
    em("push_i16", entry)
    em("syscall", "PartsSelect")
    sprt(prim)
    xy(prim, x, y)
    rs(prim, 0, scale)
    alpha(prim, a)
    draw(prim, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    em("init_stack", 0, 0)
    grp(18, 0)  # 容器组挂根：render 走 root 0，不挂则整树不可见
    grp(186, 18)
    gload(186, "graph_bg/BG001_020")
    sprt(186)
    xy(186, 0, 0)
    rs(186, 0, 800)
    alpha(186, 255)
    draw(186, 1)
    grp(201, 18)
    tachie(201, 10, "graph_vis/ASAHI_e201a", 0, 60, 50, 450, 255)
    grp(202, 18)
    tachie(202, 11, "graph_vis/AZUSA_e201a1", 0, 480, 150, 300, 230)
    em("push_i8", 0)
    em("push_i8", 0)
    em("push_i8", 0)
    em("syscall", "V3DSet")
    em("ret")

    addr = 4
    lines = ["- address: 4", "  args_count: 0", "  locals_count: 0", "  insts:"]
    for m, ops in I:
        lines.append(f"  - address: {addr}")
        lines.append(f"    mnemonic: {m}")
        if m == "init_stack":
            lines += ["    operands:", f"    - '{ops[0]}'", f"    - '{ops[1]}'"]
        elif m in ("push_i8", "push_i16"):
            lines += ["    operands:", f"    - '{ops[0]}'"]
        elif m == "push_string":
            lines += ["    operands:", f"    - {ops[0]}"]
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
        "game_title: tachie-e2e",
        "syscalls:",
    ]
    for i, (n, c) in enumerate(IMPORTS):
        cfg += [f"- id: {i}", f"  name: {n}", f"  args_count: {c}"]
    cfg.append("custom_syscall_count: 0")
    with open(os.path.join(args.out_dir, "config.yaml"), "w") as f:
        f.write("\n".join(cfg) + "\n")

    with open(os.path.join(args.out_dir, "project.toml"), "w") as f:
        f.write('config_file = "config.yaml"\ndisassembly_file = "disassembly.yaml"\n')
    print(f"scene written to {args.out_dir} ({len(I)} insts)")


if __name__ == "__main__":
    main()
