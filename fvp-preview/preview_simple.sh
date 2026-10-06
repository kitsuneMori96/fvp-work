#!/bin/bash
# Simple 剧本 -> 可视化预览：一键桥（本地孵化，不进 Simple 仓库）。
# 用法: bash preview_simple.sh <Simple仓库目录> <剧本txt> [SAMPLE_DIR] [PORT]
# 步骤: hcb_build 拼 .chb -> diff 反推剧本入口 new_off -> vm 实录
#       (--nls gbk) -> 贴图归档 -> replay+check -> 起 web 服务。
# 环境: FVP_BASE_PATH=正式版 moyu 目录（读 graph_*.bin 贴图，只读本地）
#       TICKS（默认 3000）/ AUTO_CLICK（默认 30）/ ENTRY_PC（手动覆盖入口）
set -e
SIMPLE_DIR="${1:?用法: preview_simple.sh <Simple仓库目录> <剧本txt> [SAMPLE_DIR] [PORT]}"
SCRIPT_TXT="${2:?用法: preview_simple.sh <Simple仓库目录> <剧本txt> [SAMPLE_DIR] [PORT]}"
SAMPLE_DIR="${3:-/tmp/simple-preview}"
PORT="${4:-8001}"
TICKS="${TICKS:-1500}"
# 注意：剧本播完后标题后台线程还会动 UI prim，ticks 远超内容结尾会导致
# 终态自检 diffs≠0（idle 漂移，非模型错）。取刚好播完的值最干净。
AUTO_CLICK="${AUTO_CLICK:-30}"
REPO="$(cd "$(dirname "$0")" && pwd)"
VM="$HOME/.cache/cargo-target/fvp-preview/debug/fvp-preview-vm"
: "${FVP_BASE_PATH:?请先 export FVP_BASE_PATH=/path/to/正式版moyu}"

echo "== 1/6 hcb_build 拼字节码 =="
cd "$SIMPLE_DIR"
mkdir -p base "$SAMPLE_DIR"
BUILD_LOG="$SAMPLE_DIR/build.log"
[ -f base/base.chb ] || cp base.chb base/base.chb
[ -f base/cg_loaded.txt ] || cp cg_loaded.txt base/cg_loaded.txt
# script 默认就是 base/Script.txt：同文件（-ef 比 inode）跳过，否则 cp 自杀。
[ "$SCRIPT_TXT" -ef base/Script.txt ] 2>/dev/null || cp "$SCRIPT_TXT" base/Script.txt
python3 hcb_build.py > "$BUILD_LOG" 2>&1 || {
  # P2: 构建报错定位到 txt 行：hcb_build 逐行 print(i)，取最后一个纯数字+1。
  echo "BUILD_FAIL_TXT_LINE=$(grep -E '^[0-9]+$' "$BUILD_LOG" | tail -1 | awk '{print $1+1}')"
  tail -8 "$BUILD_LOG"; exit 1; }
CHB="$SIMPLE_DIR/.test.chb"
[ -f "$CHB" ] || { echo "构建产物缺失"; tail -5 /tmp/simple-build.log; exit 1; }
echo "产物: $CHB $(stat -c%s "$CHB")B"

echo "== 2/6 反推剧本入口 new_off（与 base.chb diff）=="
if [ -z "$ENTRY_PC" ]; then
  ENTRY_PC=$(python3 - "$SIMPLE_DIR/base/base.chb" "$CHB" <<'EOF'
import struct, sys
a = open(sys.argv[1],'rb').read()
b = open(sys.argv[2],'rb').read()
base_off = 0x0008AEC7
entry = struct.unpack_from('<I', b, 0)[0]
cands = set()
i = 4
n = min(len(a), base_off)
while i < n:
    if a[i] != b[i]:
        j = i
        while j < n and a[j] != b[j]:
            j += 1
        for k in range(max(4, i-3), j):
            v = struct.unpack_from('<I', b, k)[0]
            if base_off < v < entry:
                cands.add(v)
        i = j
    else:
        i += 1
if not cands:
    sys.exit('推不出 new_off，请手动 ENTRY_PC=xxx')
print(max(cands))
EOF
) || exit 1
fi
echo "剧本入口: $ENTRY_PC"

echo "== 3/6 vm 实录 $TICKS ticks（gbk 台词）=="
# 保留 build.log（步骤1已写），其余清掉重建（防旧 tex/replay 残留）。
find "$SAMPLE_DIR" -mindepth 1 -maxdepth 1 ! -name build.log -exec rm -rf {} +
mkdir -p "$SAMPLE_DIR/tex_raw"
# RFVP_CALL_TRACE=1: 记录 script 层 call(from->to)，给 replay 做 txt 行号归因（P1）。
export RFVP_CALL_TRACE=1
"$VM" "$CHB" --ticks "$TICKS" --entry-pc "$ENTRY_PC" --auto-click "$AUTO_CLICK" \
  --nls gbk --png-dir "$SAMPLE_DIR/tex_raw" \
  --trace-syscall > "$SAMPLE_DIR/scene.json" 2> "$SAMPLE_DIR/trace.log"

echo "== 4/6 贴图归档 =="
mkdir -p "$SAMPLE_DIR/tex"
cp "$SAMPLE_DIR/tex_raw/"*.png "$SAMPLE_DIR/tex/" 2>/dev/null || true
python3 - "$SAMPLE_DIR" <<'EOF'
import json, sys
d = sys.argv[1] + '/scene.json'
j = json.load(open(d, encoding='utf-8'))
def fix(o):
    if isinstance(o, dict):
        return {k: (v.replace('/tex_raw/', '/tex/') if k == 'image' and isinstance(v, str) else fix(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [fix(v) for v in o]
    return o
json.dump(fix(j), open(d, 'w', encoding='utf-8'), ensure_ascii=False)
EOF

echo "== 5/6 回放+终态自检 =="
cp "$SIMPLE_DIR/.linemap.json" "$SAMPLE_DIR/linemap.json"
python3 "$REPO/replay_ops.py" "$SAMPLE_DIR/trace.log" --tex-dir "$SAMPLE_DIR/tex" \
  --out "$SAMPLE_DIR/replay.json" --check "$SAMPLE_DIR/scene.json" \
  --linemap "$SAMPLE_DIR/linemap.json"

echo "== 6/6 起预览服务 =="
if [ -n "$SKIP_SERVE" ]; then
  echo "(SKIP_SERVE=1，跳过起服务)"
else
  bash "$REPO/start_editor.sh" "$SAMPLE_DIR" "$PORT"
fi
