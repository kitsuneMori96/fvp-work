# fvp-preview — HCB 可视化预览与剧本编辑器

用真机语义（rfvp VM 实跑字节码）渲染 HCB 画面：在 Web 里直接写剧本 txt，
光标停哪行、秒级看到该行执行后的画面；全量重跑生成可逐帧 scrub 的时间线；
调好的数值可按常量地址写回 HCB。

`v0.1.0` 是第一个可用版本：Sakura（樱花萌放）全流程已验证
（采样→回放 `diffs=0` 常态，剩余差为标题线程 idle 漂移）；
Simple-.hcb-Editor 剧本链已验证（bg/立绘/对话/转场/CG/选项全覆盖）。

## 开箱即用（release 包）

```bash
tar xzf fvp-preview-v0.1.0.tar.gz && cd fvp-preview
export FVP_BASE_PATH=/path/to/游戏目录        # 含 .hcb/.bin 的那一级
# 可选：SIMPLE_DIR=/path/to/Simple-hcb-Editor SCRIPT=/path/to/剧本.txt
bash start_editor.sh [SAMPLE_DIR] [PORT]        # 默认 /tmp/fvp-sample 8000
# Windows 浏览器开 http://<WSL-IP>:<PORT>/
```

release 包自带 `bin/fvp-preview-vm`（Linux x86_64），`start_editor.sh`
会自动用它（`FVP_VM_BIN` 未设置时优先 `bin/`，其次 `vm/target/release/`）。
Python 只用标准库；首次全量重跑/采样需要 cargo 现场编 vm 的请看“从源码构建”。

## 文件发现设计（为什么放哪都能找到）

一切文件归属都在**服务端**，浏览器只传内容不传路径
（浏览器填的 `D:\xxx` 服务端够不着，老面板的手填路径因此作废）。
共五类来源，各有唯一权威位置，面板“来源显示”可查：

| 文件 | 权威位置 | 说明 |
|---|---|---|
| 游戏资源（`.bin` 图/声包） | `FVP_BASE_PATH` | rfvp VFS 启动时扫该目录全部 `.bin` 建文件名表；预览不需要音频，`FVP_VFS_SKIP=voice,bgm,se,se_sys,se_env` 跳过（9P 上全扫要 6s，跳包+大块读后 0.03s，见上游 0007） |
| 时间线（scene/replay/tex） | `--sample-dir`（固定目录） | `/api/scene·replay·tex` 只读这里；页面“上传”存到这里；直接往目录放文件后按“刷新”即可 |
| 即时快照贴图 | `<sample>.instant/tex` | 即时预览的 `--png-dir`，`/api/tex` 白名单放行本目录（快照里是绝对路径） |
| 剧本 txt | `--script`（磁盘 GBK） | 编辑框/即时/重跑三方唯一写盘点，JSON 里是 UTF-8，落盘转 GBK |
| 写回目标 | `--hcb/--addrmap/--out-hcb` | 只用服务端配置；拒绝覆盖原文件（`out` 与 `hcb` 同文件直接拒绝） |

启动参数与环境变量（`start_editor.sh` 透传）：

| 变量/参数 | 作用 | 默认 |
|---|---|---|
| `FVP_BASE_PATH` | 游戏资源根（含 `.hcb/.bin`） | 必填 |
| `FVP_VM_BIN` | vm 二进制 | `bin/`→`vm/target/release/` 自动找 |
| `FVP_VFS_SKIP` | 跳过不用的资源包 | `voice,bgm,se,se_sys,se_env` |
| `SIMPLE_DIR` / `SCRIPT` | 剧本编辑链（Simple 仓库/剧本 txt） | 空=只看时间线 |
| `FVP_HCB/FVP_ADDRMAP/FVP_OUT` | 写回三件套 | 空=写回未就绪（面板会提示加参） |
| `TICKS` | 全量重跑 tick 上限 | 1500 |

## 从源码构建

```
# 1) 备好 rfvp 兄弟检出并打补丁（顺序不能乱）：
#    fvp-preview/conform/rfvp-patches/0001..0008
# 2) CARGO_TARGET_DIR 指向 WSL 侧（9P 上增量编译极慢，见仓库根 .gitignore 注释）
cd fvp-preview/vm && CARGO_TARGET_DIR=~/.cache/cargo-target/fvp-preview \
  cargo build --release --bin fvp-preview-vm
# 产物 vm/target/release/fvp-preview-vm（不进仓；release 包放在 bin/）
```

## 目录

- `serve_editor.py` — 标准库 only 服务（scene/replay/tex/upload/instant/rebuild/writeback）
- `web/index.html` — 单文件前端（渲染 math 与 Rust core 对拍自测 `MATH 13/13`）
- `vm/` — headless 跑 HCB 吐快照 JSON（`--entry-pc/--break-pc/--auto-click/--nls`）
- `conform/rfvp-patches/` — 上游 scratch 补丁序列（0001 窗口 headless … 0008 MotionAlpha Nil 时长）
- `*.sh` — `sample_*.sh` 采样，`preview_simple.sh` 全量桥，`start_editor.sh` 一键启动
- `replay_ops.py` — trace→时间线回放（与 vm 同语义，0004/0008 已同步镜像）
- `sample_verify.txt` — 40 行验证剧本（GBK，bg/立绘/对话/转场/CG/选项全覆盖）

## 已知限制（v0.1.0）

- 选项菜单 auto-click 选不中，分支内容暂需把分支体临时挪出验证（`hit=false` 会明示）。
- `[cg]` 显示链在 247914/253493 深分支提前返回，CG 类素材请先用 `fvp-pack-ls` 确认包内真有该名字（`*_FD01A` 这类跨版本名字包里不存在）。
- 即时预览约 0.6–1s/行（编译缓存+release vm），常驻暖机以后再做。
