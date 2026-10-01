# fvp-work — FVP 引擎背景模糊机制研究与工具

以《樱花萌放》(Sakura, FAVORITE) 为样本, 逆向 FVP 引擎背景模糊差分机制,
并沉淀为可复用的构建器 / 校验器 / 补丁. 上游分析文档见
[Huohua-newbie/fvpanalysis](https://github.com/Huohua-newbie/fvpanalysis).

## 核心结论 (一句话)

模糊不是一张图被“变虚”, 而是双层 prim
(`186`=本体常显, `187`=`b` 模糊图) + 绘制总闸
(`PrimSetDraw`, 主循环按配置 `G[1973]` 每轮 enforcement) +
浓度 (`PrimSetAlpha`, 藏在相机函数副作用里, `alpha = clamp(z-250)`);
剧本层从不直调 `PrimSetAlpha`.

## 目录

- `bg_blur_demo.py` — 从 `graph_bg.bin` 解包 `BGxxx` 本体/`b` 图,
  生成对比与渐变帧 (只依赖 Pillow).
- `demo_fvp/` — FVP 原生字节码 demo 构建器:
  - `build_fvp_blur_demo.py` — v1 最小演示 (已验证有 TextBuff/渐变问题, 留档)
  - `build_fvp_blur_demo2.py` — v2 全手写演示 (窗口 UI/铭牌/落位全参/
    Motion 渐变/`--bg`/`--line` 可配, 播完停帧)
  - `verify_demo2.py` — 独立校验器 (重解 + 对照 syscall 数据库逐条断言,
    96 项全绿方可上真机)
  - `demo_blur*.hcb` + `.asm.txt` — 构建产物 (真机测试时改名覆盖 `Sakura.hcb`,
    测完即恢复, 务必先备份!).
- `demo_out/`、`demo_proof/` — 图片产物与 Ren'Py/pygame 复现脚本.
- `savefix/` — 中文用户名存档重定向方案 (hook DLL + 注入启动器,
  详见其 README 与测试清单; 不动注册表, 不改 exe).
- `patch_savepath.py` — 存档路径相关小工具.
- `save_backup_20251001/` — **本地个人存档备份, 被 gitignore 排除,
  永不上公开仓库.**

## 真机测试铁律

1. 备份 `Sakura.hcb` (md5 留档) 再覆盖; 测完即恢复.
2. 构建器改完先跑 `verify_demo2.py`, 全绿才上真机.
3. 行为问题按“四幕验收”回报 (本体台词/渐变/模糊台词/停帧),
   描述现象即可, 不需要截图.

## 免责

仅供逆向学习与同人研究; 不含任何游戏本体文件;
修改版 exe/补丁的分发请自行评估版权风险.
