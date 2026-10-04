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
- `fvp-preview/` — HCB 可视化编辑器 (坐标语义预览, 不复刻引擎):
  - `core/` — 坐标变换库 (`build_draw_model` 等价实现, 零依赖, 15 单测).
  - `web/index.html` — **网页预览 (默认工作流, 单文件零依赖)**:
    canvas 2D 按绘制序画 quad, 数学直译自 `core` (加载自测 `MATH 13/13`);
    图层 eye/solo/行选、脚本序列步进、拖拽改 x/y、数字面板、PNG 系列导出。
  - `serve_editor.py` — 本地微服务 (标准库 only): 静态页 + 场景/回放/贴图,
    `POST /api/writeback` 一键调 `patch_hcb.py` (等宽三校验, 绝不覆盖原文件)。
    启动: `python3 serve_editor.py --sample-dir /tmp/fvp-sample [...]`,
    浏览器开 `http://localhost:8000`。
  - `app/` — eframe 桌面版 (已冻结保留, 不再投入; WSLg DPI 问题见 commit 记录).
  - `vm/` — 真 HCB 运行器 (快照导出 `--ref-png` 标尺, `--trace-syscall` 溯源).
  - `extract_scene.py` — 反汇编工程 -> 静态场景 JSON+地址映射 (常量脚本用).
  - `trace_join.py` — syscall trace + 反汇编 join -> 动态 last-write 映射
    (动态脚本用; 0 个/多个候选一律拒绝写回).
  - `patch_hcb.py` — 二进制等宽补丁 (操作码+旧值双校验, 违例 abort).
  - 闭环：`disassembler -> vm --trace-syscall -> trace_join.py -> 编辑 ->
    patch_hcb.py -> vm 重跑断言 (快照 diff 应只有目标字段)`.

## 真机测试铁律

1. 备份 `Sakura.hcb` (md5 留档) 再覆盖; 测完即恢复.
2. 构建器改完先跑 `verify_demo2.py`, 全绿才上真机.
3. 行为问题按“四幕验收”回报 (本体台词/渐变/模糊台词/停帧),
   描述现象即可, 不需要截图.

## 免责

仅供逆向学习与同人研究; 不含任何游戏本体文件;
修改版 exe/补丁的分发请自行评估版权风险.
