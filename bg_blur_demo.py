# -*- coding: utf-8 -*-
"""樱花萌放 背景模糊差分 调用演示.

演示链 (已用 fvpanalysis + Sakura_dump.txt 验证):
  f_0001063E (HCB function_1064_, Sakura_dump.txt:26796 起)
    G[63] = "BG001_020"            # 本体
    f_0005074C(G[63], ...)         # = function_4854_, prim 186, PrimSetAlpha(186,255)
    G[63] = G[63] + "b"            # "BG001_020b"
    f_00050859(G[63], ...)         # = function_4855_, prim 187, PrimSetAlpha(187,0) 初始隐藏
  对话中切换 (对话 f_0004CEFD / function_4809_ 本身不切背景):
    f_00052882(alpha, ...)         # PrimSetAlpha(187, alpha) / f_000520EE 渐变
    经由 f_000525B7 / f_0005261B / f_000526A3 (V3DMotion 包装, G[1973] 门控)
    f_00051202(1/2)                # PrimGroupIn(186/187, 8/12) 选哪层显示

本脚本直接读真实游戏包, 不需要解密 HCB:
  D:\\soft\\Sakura moyu\\graph_bg.bin  (FvpBin: u32 count + u32 name_size + 12B/entry)

用法 (Windows):
  python bg_blur_demo.py --base BG001_020
  python bg_blur_demo.py --base BG001_000 --out ./demo_out
用法 (WSL, 本机路径):
  python3 /mnt/d/soft/\\'Sakura moyu\\'/bg_blur_demo.py --base BG001_020

产物:
  <base>_186.png        # 本体 (prim 186)
  <base>b_187.png       # 模糊差分 (prim 187)
  <base>_compare.png    # 左右并排对比
  <base>_fade_*.png     # 模拟 PrimSetAlpha(187) 0->255 的 5 帧
  调用序列会打印到控制台, 另存 <base>_calls.txt
只依赖: Pillow (pip install pillow)
"""
from __future__ import annotations
import argparse
import struct
import zlib
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    raise SystemExit("需要 Pillow: pip install pillow")

GAME_DIR_CANDIDATES = [
    Path(r"D:\soft\Sakura moyu"),
    Path("/mnt/d/soft/Sakura moyu"),
]


def find_graph_bg(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
    for d in GAME_DIR_CANDIDATES:
        p = d / "graph_bg.bin"
        if p.is_file():
            return p
    raise SystemExit("找不到 graph_bg.bin, 请用 --bin 指定路径")


def read_bin_index(bin_path: Path):
    with open(bin_path, "rb") as f:
        fc, nts = struct.unpack("<II", f.read(8))
        entries = [struct.unpack("<III", f.read(12)) for _ in range(fc)]
        name_table = f.read(nts)
    names = name_table.split(b"\x00")
    # 末尾多一个空串是正常的
    if names and names[-1] == b"":
        names.pop()
    assert len(names) == fc, f"name数 {len(names)} != file数 {fc}"
    return fc, entries, [n.decode("ascii", errors="replace") for n in names]


def read_entry(bin_path: Path, entries, doff: int, dsize: int) -> bytes:
    with open(bin_path, "rb") as f:
        f.seek(doff)
        return f.read(dsize)


def decode_hzc(blob: bytes) -> Image.Image:
    assert blob[:4] == b"hzc1", f"bad magic {blob[:4]!r}"
    unc, hs = struct.unpack_from("<II", blob, 4)
    assert blob[12:16] == b"NVSG", f"bad payload magic {blob[12:16]!r}"
    _unk1, typ, w, h, ox, oy = struct.unpack_from("<H H H H h h", blob, 16)
    payload = blob[44:]
    if payload[:2] == b"\x78\x01" or payload[:2] == b"\x78\x9c" or payload[:2] == b"\x78\xda":
        raw = zlib.decompress(payload)
    else:
        raise SystemExit(f"非 zlib HZC (type={typ} {w}x{h}), payload头 {payload[:8].hex()}, 需走 TLG 分支, 本演示跳过")
    if typ == 0:  # BGR24
        assert len(raw) == w * h * 3, f"raster {len(raw)} != {w*h*3}"
        img = Image.frombytes("RGB", (w, h), raw, "raw", "BGR")
        return img
    if typ == 1:  # BGRA32
        assert len(raw) == w * h * 4, f"raster {len(raw)} != {w*h*4}"
        img = Image.frombytes("RGBA", (w, h), raw, "raw", "BGRA")
        return img.convert("RGB")
    raise SystemExit(f"未处理的 HZC type={typ}, 先用 type0/1 的 BG 做演示")


def fvp_call_trace(base: str) -> str:
    b = base + "b"
    return f"""# ---- FVP 原作调用序列 (Sakura_dump.txt:function_1064_ / Sakura.lua:f_0001063E) ----
pushstring {base}          # G[63] = 本体
popglobal 63
call function_4854_        # f_0005074C : prim 186
  GraphLoad(186, "graph_bg/{base}")   # f_00037294(a3=2 -> graph_bg/)
  PrimSetSprt(186, 186)               # f_00037345
  PrimGroupIn(186, 12)                # f_00037476
  PrimSetTile(186, 19, ...)           # f_000510AE, 1280x720 全屏
  PrimSetAlpha(186, 255)              # 本体不透明, 直接可见
pushstring b
add                        # G[63] = "{b}"
popglobal 63
call function_4855_        # f_00050859 : prim 187
  GraphLoad(187, "graph_bg/{b}")
  PrimSetSprt(187, 187)
  PrimGroupIn(187, 12)
  PrimSetTile(187, 19, ...)
  PrimSetAlpha(187, 0)                # 模糊层初始隐藏
# ---- 对话中切到模糊 (对话 function_4809_/f_0004CEFD 不会自动切) ----
call f_00052882            # PrimSetAlpha(187, 0->255), 或 f_000520EE 渐变
# 上层包装: f_000525B7(V3DSet) / f_0005261B / f_000526A3(V3DMotion), 条件 G[1973]==true
call f_00051202            # PrimGroupIn(187, 8/12), 把模糊层翻到前面
# V3D(V3DMotion/V3DSet)只是相机 X/Y/Z, 不决定用哪张贴图, 反驳"放大自动变模糊".
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="FVP 背景模糊差分调用演示")
    ap.add_argument("--bin", default=None, help="graph_bg.bin 路径, 缺省自动找 D:\\soft\\Sakura moyu")
    ap.add_argument("--base", default="BG001_020", help="本体名 (不带b), 例 BG001_000/BG001_020/BG002_000")
    ap.add_argument("--out", default=None, help="输出目录, 缺省为 bin 同目录/demo_out")
    args = ap.parse_args()

    bin_path = find_graph_bg(args.bin)
    base = args.base
    blur = base + "b"
    out = Path(args.out) if args.out else bin_path.parent / "demo_out"
    out.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] 解析 {bin_path} ...")
    fc, entries, names = read_bin_index(bin_path)
    print(f"      包内 {fc} 个文件, 例: {names[:4]}")
    try:
        i0 = names.index(base)
        i1 = names.index(blur)
    except ValueError:
        print(f"      !! 找不到 {base} / {blur}, 包内 BG001 系:")
        for n in names:
            if n.startswith("BG001"):
                print(f"        {n}")
        raise SystemExit(1)
    print(f"      {base} 是第 {i0} 个, {blur} 是第 {i1} 个 ( consecutive={i1==i0+1} ), 对应'依次调用两个'")

    print("[2/4] 解码 HZC ...")
    img0 = decode_hzc(read_entry(bin_path, entries, *entries[i0][1:]))
    img1 = decode_hzc(read_entry(bin_path, entries, *entries[i1][1:]))
    print(f"      本体 {img0.size} 模糊 {img1.size}")
    img0.save(out / f"{base}_186.png")
    img1.save(out / f"{blur}_187.png")

    print("[3/4] 生成 prim186/187 对比 + PrimSetAlpha 渐变帧 ...")
    w, h = img0.size
    scale = 640 / w
    small0 = img0.resize((640, int(h * scale)))
    small1 = img1.resize((640, int(h * scale)))
    comp = Image.new("RGB", (1280, small0.height))
    comp.paste(small0, (0, 0))
    comp.paste(small1, (640, 0))
    comp.save(out / f"{base}_compare.png")
    # 模拟 f_00052882: PrimSetAlpha(187) 0->255, 本体 186 一直在下面
    for k, a in enumerate([0, 64, 128, 192, 255]):
        alpha = a / 255.0
        frame = Image.blend(img0, img1, alpha).resize((640, int(h * scale)))
        frame.save(out / f"{base}_fade_{k}_{a}.png")
    print(f"      输出目录: {out}")

    print("[4/4] 调用序列 ...")
    trace = fvp_call_trace(base)
    print(trace)
    (out / f"{base}_calls.txt").write_text(trace, encoding="utf-8")
    print("Ren'Py 等价片段 (示意, 图片拷到 game/bg_demo/ 即可用):")
    print(f"""  image bg_normal = "bg_demo/{base}_186.png"
  image bg_blur = "bg_demo/{blur}_187.png"
  label bg_demo:
      scene bg_normal  # prim186, alpha 255
      "本体 (prim 186, GraphLoad+PrimSetSprt+PrimSetAlpha 255)"
      show bg_blur with Dissolve(0.5)  # PrimSetAlpha(187) 0->255
      "模糊 (prim 187, {blur})"
""")


if __name__ == "__main__":
    main()
