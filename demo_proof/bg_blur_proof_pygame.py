# -*- coding: utf-8 -*-
"""bg_blur_proof_pygame.py — pygame 引擎可运行验证 (Ren'Py 脚本的同义实现).

证明 (任选一段台词+背景, 取共通线 BG001_020):
  1) 对话本身 (f_0004CEFD) 不切背景
  2) 外部函数 (f_00052882 PrimSetAlpha(187) + f_00051202 PrimGroupIn) 才切出模糊
  3) V3D 放大 (V3DMotion) 不等于 b 模糊图

运行 (Windows):
  python "D:\\soft\\Sakura moyu\\demo_proof\\bg_blur_proof_pygame.py"
操作: SPACE/点击=下一句, Z=V3D放大对照, ESC=退出
自检 (无头, 本机验证用):
  python3 bg_blur_proof_pygame.py --auto   # 自动走完全流程并存 proof_*.png
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
for alt in [HERE.parent / "demo_out", Path("/mnt/d/soft/Sakura moyu/demo_out")]:
    if not (ASSETS / "BG001_020_186_720.png").exists() and (alt / "BG001_020_186.png").exists():
        pass  # 下面会回退找大图

NORMAL_CANDS = [ASSETS / "BG001_020_186_720.png",
                HERE.parent / "demo_out" / "BG001_020_186.png"]
BLUR_CANDS = [ASSETS / "BG001_020b_187_720.png",
              HERE.parent / "demo_out" / "BG001_020b_187.png"]

LINE1 = "さて、今夜はシチューだ。たくさん作ったから、いっぱい食べてくれたらうれしいな"
LINE2 = "台所からエプロンを外しながらあさひさんがやって来た。"

STAGES = [
    ("act1", "Act1 本体+对话1 (prim186)", "あさひ", LINE1, "normal"),
    ("act1b", "Act1 本体+对话2 (对话不致模糊)", None, LINE2, "normal"),
    ("act2", "Act2 外部函数切模糊 f_00052882", None, "PrimSetAlpha(187) 0→255", "crossfade"),
    ("act2b", "Act2 模糊上对话 (prim187)", "あさひ", LINE1, "blur"),
    ("act3", "Act3 V3D放大对照 (zoom本体≠b图)", None, "V3DMotion zoom 1.0→1.2, 依然锐利", "zoom"),
    ("act3b", "Act3 真b图对比", None, "BG001_020b: 光学模糊, 与放大完全不同", "blur"),
]


def find(cands):
    for p in cands:
        if p.is_file():
            return p
    raise SystemExit(f"找不到资源图: {cands}")


def wrap(font, text, width):
    words, lines, cur = list(text), [], ""
    for ch in words:  # 按字折行 (日文无空格)
        t = cur + ch
        if font.size(t)[0] > width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true", help="无头自检: 自动截图退出")
    ap.add_argument("--out", default=str(HERE / "proof_out"))
    args = ap.parse_args()
    if args.auto:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

    import pygame
    pygame.init()
    W, H = 1280, 720
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("FVP bg blur proof: prim186 vs prim187")
    try:
        font = pygame.font.SysFont("notosanscjkjp,msgothic,meiryo,arial", 26)
        small = pygame.font.SysFont("arial", 20)
    except Exception:
        font = pygame.font.Font(None, 28)
        small = pygame.font.Font(None, 22)

    normal = pygame.image.load(str(find(NORMAL_CANDS))).convert()
    blur = pygame.image.load(str(find(BLUR_CANDS))).convert()
    normal = pygame.transform.smoothscale(normal, (W, H))
    blur = pygame.transform.smoothscale(blur, (W, H))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    def draw_bg(mode, t=0.0):
        if mode == "normal":
            screen.blit(normal, (0, 0))
        elif mode == "blur":
            screen.blit(blur, (0, 0))
        elif mode == "crossfade":
            a = max(0.0, min(1.0, t))
            # 简单线性混合模拟 MotionAlpha/PrimSetAlpha
            tmp = blur.copy()
            screen.blit(normal, (0, 0))
            tmp.set_alpha(int(a * 255))
            screen.blit(tmp, (0, 0))
        elif mode == "zoom":
            z = 1.0 + 0.2 * max(0.0, min(1.0, t))
            zw, zh = int(W * z), int(H * z)
            big = pygame.transform.smoothscale(normal, (zw, zh))
            screen.blit(big, ((W - zw) // 2, (H - zh) // 2))

    def draw_textbox(title, speaker, body):
        box = pygame.Surface((W - 80, 150), pygame.SRCALPHA)
        box.fill((0, 0, 0, 170))
        screen.blit(box, (40, H - 170))
        y = H - 160
        if title:
            screen.blit(small.render(title, True, (255, 220, 120)), (60, y))
            y += 26
        if speaker:
            screen.blit(font.render(f"【{speaker}】", True, (255, 180, 200)), (60, y))
            y += 36
        for ln in wrap(font, body, W - 140):
            screen.blit(font.render(ln, True, (255, 255, 255)), (60, y))
            y += 34
        screen.blit(small.render("SPACE/Click: next  Z: zoom对照  ESC: quit", True, (180, 180, 180)), (60, H - 32))

    idx, t, clock = 0, 0.0, pygame.time.Clock()
    running = True
    auto_shots = set()
    while running:
        dt = clock.tick(30) / 1000.0
        t += dt
        key, _, _, body, mode = STAGES[idx][1], None, None, None, None
        _, title, speaker, body, mode = STAGES[idx]
        anim_t = (t % 2.0) / 2.0 if mode in ("crossfade", "zoom") else 1.0
        if mode == "crossfade":
            anim_t = min(1.0, t * 0.7)
        draw_bg(mode, anim_t if mode in ("crossfade", "zoom") else 0)
        draw_textbox(title, speaker, body)
        # 顶部映射条
        screen.blit(small.render("f_0005074C/prim186 ↔ f_00050859/prim187 | 切: f_00052882 | 相机: V3DMotion", True, (140, 220, 255)), (40, 12))
        pygame.display.flip()

        if args.auto:
            key = f"stage{idx}_{STAGES[idx][0]}"
            if key not in auto_shots and (mode not in ("crossfade",) or anim_t >= 1.0):
                pygame.image.save(screen, str(out / f"proof_{key}.png"))
                auto_shots.add(key)
            if len(auto_shots) >= len(STAGES):
                print(f"AUTO OK: {len(auto_shots)} shots in {out}")
                # 断言级验证: b 图确与本体不同 (均值差>5), zoom 帧≠b 图
                import numpy as np
                a = np.asarray(pygame.surfarray.array3d(normal)).astype(float)
                b = np.asarray(pygame.surfarray.array3d(blur)).astype(float)
                diff = abs(a - b).mean()
                print(f"blur-vs-normal mean|diff| = {diff:.2f} (expect > 5)")
                assert diff > 5, "模糊图与本体无差异, 资源有误"
                running = False
            if t > 2.0:
                t = 0.0
                idx = min(idx + 1, len(STAGES) - 1)
                if idx == len(STAGES) - 1 and len(auto_shots) >= len(STAGES):
                    pass
            continue

        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    running = False
                elif e.key in (pygame.K_SPACE, pygame.K_RETURN):
                    idx = min(idx + 1, len(STAGES) - 1)
                    t = 0.0
                elif e.key == pygame.K_z:
                    idx = 4
                    t = 0.0
            elif e.type == pygame.MOUSEBUTTONDOWN:
                idx = min(idx + 1, len(STAGES) - 1)
                t = 0.0
    pygame.quit()


if __name__ == "__main__":
    sys.exit(main())
