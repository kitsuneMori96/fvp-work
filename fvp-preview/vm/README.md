# fvp-preview-vm — B 轨产品：真机跑 HCB，吐预览快照

把 HCB 跑进**真 rfvp VM**（`GameData + Parser + ThreadManager + VmRunner`），
停下后把 prim 树导出成 A 轨快照 JSON，管道给 `fvp-preview` 即看即得：

```sh
FVP_BASE_PATH=/path/to/game fvp-preview-vm scene.hcb --ticks 300 \
  --png-dir /tmp/vmtex | fvp-preview
```

> `FVP_BASE_PATH` 指向游戏资源根（`.bin` 包所在目录，`GraphLoad` 经 VFS 解包）。
> 同 conform：需要 `setup-rfvp.sh` 备好的兄弟检出（含 conform patch），
> 本目录是独立 workspace（主 workspace 没配上游时不受影响）。

## 停止条件（取先到者）

- `--ticks N`：跑满 N 帧（1 tick = 16ms 游戏时间）。
- `--break-pc ADDR`（十进制或 0x 十六进制）：任一 context 的 pc **首次越过**
  该地址。注意一个 tick 跑多条指令，只能判越过不能判精确相等；
  精确单步（`context_dispatch_opcode` 逐条）列二期。
- 无 RUNNING context（等待输入/脚本结束）：状态已稳定，提前收工。

## 快照内容

- 只导出 `typ==Sprt(4)` 且 `draw` 且贴图可解的 prim；贴图按 `g<texture_id>.png`
  导出到 `--png-dir`（NVSG 解码后的真像素，不是近似图）。
- 字段与 `fvp-preview` 的快照协议对齐（含 parent 链、attr、OP、alpha、V3D 相机）。
- viewport 默认 800x600（z 分支投影中心依赖它；非常规分辨率用 `--viewport W H`）。

## 已知限制（v1）

- 文本 prim 不导出（无 `default.ttf` 时 Text 系 syscall 空转；要文字需把游戏字体
  放进 `FVP_BASE_PATH` 并确认 `FontEnumerator` 能读到）。
- 视频/音频 syscall 打桩为空（只求快照，不求播放）。
- 需要输入的脚本（`ThreadWait` 等点击）会在“无 RUNNING”处停住——快照仍是有效态，
  只是停在等待点（编辑器可据此提示用户）。
