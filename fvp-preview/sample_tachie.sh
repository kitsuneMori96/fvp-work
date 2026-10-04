#!/bin/bash
# 示例工程物化：tachie demo（BG + 双立绘）-> HCB -> vm 运行 -> 快照/trace/回放。
# 贴图是游戏本体解包产物，只落本地 $SAMPLE_DIR，不进仓库。
# 用法：bash sample_tachie.sh [SAMPLE_DIR]（默认 /tmp/fvp-sample）
# 之后启动编辑器：
#   cat $SAMPLE_DIR/scene.json | fvp-preview --replay $SAMPLE_DIR/replay.json
set -e
SAMPLE_DIR="${1:-/tmp/fvp-sample}"
REPO="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$SAMPLE_DIR"
echo "== 1/4 生成 demo HCB =="
rm -rf "$SAMPLE_DIR/proj"
python3 "$REPO/vm/scenes/gen_tachie_scene.py" --out-dir "$SAMPLE_DIR/proj"
echo "== 2/4 回编（简单场景无 ThreadStart，assembler 可用）=="
ASM="${ASM:-$HOME/rfvp-upstream/target/debug/assembler}"
"$ASM" --project-dir "$SAMPLE_DIR/proj" --output "$SAMPLE_DIR/tachie.hcb" --nls sjis
echo "== 3/4 vm 运行（FVP_BASE_PATH 需指向游戏目录）=="
: "${FVP_BASE_PATH:?请先 export FVP_BASE_PATH=/path/to/game}"
VM="$HOME/.cache/cargo-target/fvp-preview/debug/fvp-preview-vm"
"$VM" "$SAMPLE_DIR/tachie.hcb" --ticks 200 --png-dir "$SAMPLE_DIR/tex" \
  --trace-syscall > "$SAMPLE_DIR/scene.json" 2> "$SAMPLE_DIR/trace.log"
echo "== 4/4 回放（脚本序列 x 场景）=="
python3 "$REPO/replay_ops.py" "$SAMPLE_DIR/trace.log" --tex-dir "$SAMPLE_DIR/tex" \
  --out "$SAMPLE_DIR/replay.json" --check "$SAMPLE_DIR/scene.json"
echo "=== 示例工程就绪：$SAMPLE_DIR ==="
echo "启动：cat $SAMPLE_DIR/scene.json | fvp-preview --replay $SAMPLE_DIR/replay.json"
