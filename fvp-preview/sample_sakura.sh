#!/bin/bash
# 真机开场示例工程：从实际游戏 HCB 开头实录（vm 运行 + trace + 回放 + 映射）。
# 贴图/产物只落本地 $SAMPLE_DIR，不进仓库（游戏本体）。
# 用法：export FVP_BASE_PATH=/path/to/game
#   bash sample_sakura.sh <game.hcb> [SAMPLE_DIR] [TICKS]
# 之后：bash start_editor.sh [SAMPLE_DIR]（或 serve_editor.py --sample-dir …）
set -e
HCB="${1:?用法: sample_sakura.sh <game.hcb> [SAMPLE_DIR] [TICKS]}"
SAMPLE_DIR="${2:-/tmp/fvp-sample-sakura}"
TICKS="${3:-2000}"
REPO="$(cd "$(dirname "$0")" && pwd)"
DISASM="${DISASM:-$HOME/rfvp-upstream/target/debug/disassembler}"
VM="$HOME/.cache/cargo-target/fvp-preview/debug/fvp-preview-vm"
mkdir -p "$SAMPLE_DIR"
: "${FVP_BASE_PATH:?请先 export FVP_BASE_PATH=/path/to/game}"
echo "== 1/5 反汇编（trace join 要用）=="
if [ ! -f "$SAMPLE_DIR/disassembly.yaml" ]; then
  rm -rf "$SAMPLE_DIR/proj" && mkdir -p "$SAMPLE_DIR/proj"
  "$DISASM" --input "$HCB" --output "$SAMPLE_DIR/proj" --lang sjis
else
  echo "反汇编已存在，跳过（删掉重跑可刷新）。"
fi
echo "== 2/5 vm 运行开场 $TICKS ticks =="
rm -rf "$SAMPLE_DIR/tex_raw"
"$VM" "$HCB" --ticks "$TICKS" --png-dir "$SAMPLE_DIR/tex_raw" \
  --trace-syscall > "$SAMPLE_DIR/scene.json" 2> "$SAMPLE_DIR/trace.log"
echo "== 3/5 贴图归档（绝对路径改写，/tmp 易失）=="
mkdir -p "$SAMPLE_DIR/tex"
cp "$SAMPLE_DIR/tex_raw/"*.png "$SAMPLE_DIR/tex/" 2>/dev/null || true
python3 - "$SAMPLE_DIR" <<'EOF'
import json, sys, glob, os
d = sys.argv[1]
olds = sorted(glob.glob(d + '/tex_raw/*.png'))
newmap = {}
for p in olds:
    base = os.path.basename(p)
    newmap[p] = d + '/tex/' + base
def fix(o):
    if isinstance(o, dict):
        return {k: (newmap.get(v, v) if k == 'image' else fix(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [fix(v) for v in o]
    return o
for name in ('scene.json',):
    p = os.path.join(d, name)
    if os.path.exists(p):
        json.dump(fix(json.load(open(p, encoding='utf-8'))),
                  open(p, 'w', encoding='utf-8'), ensure_ascii=False)
print('贴图归档:', len(newmap), '个')
EOF
echo "== 4/5 动态映射（写回用）=="
python3 "$REPO/trace_join.py" "$SAMPLE_DIR/proj" "$SAMPLE_DIR/trace.log" \
  --out "$SAMPLE_DIR/addrmap.json"
echo "== 5/5 脚本序列回放 =="
python3 "$REPO/replay_ops.py" "$SAMPLE_DIR/trace.log" --tex-dir "$SAMPLE_DIR/tex" \
  --out "$SAMPLE_DIR/replay.json" --check "$SAMPLE_DIR/scene.json"
echo "=== 开场示例就绪：$SAMPLE_DIR ==="
echo "启动：bash $REPO/start_editor.sh $SAMPLE_DIR"
