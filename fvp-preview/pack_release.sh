#!/bin/bash
# 打 release 包：
#   bash pack_release.sh [VERSION]        → Linux x86_64 .tar.gz（含 vm 二进制）
#   bash pack_release.sh [VERSION] win    → Windows x86_64 .zip（含 .exe，开箱即用）
# 二进制不进 git，只进 release 包（见根 .gitignore）。
set -e
VER="${1:-0.1.0}"
PLAT="${2:-linux}"
REPO="$(cd "$(dirname "$0")" && pwd)"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/cargo-target/fvp-preview}"
if [ "$PLAT" = win ]; then
  echo "== Windows 包：直接用已编好的 .exe（vm 源码未变则无需重编） =="
  EXE="${FVP_WIN_EXE:-$HOME/.cache/cargo-target/fvp-win/x86_64-pc-windows-gnu/release/fvp-preview-vm.exe}"
  [ -f "$EXE" ] || { echo "缺 .exe: $EXE（先编：见 README Windows 节）"; exit 1; }
  STAGE="$(mktemp -d)/fvp-preview-$VER-win64"
  mkdir -p "$STAGE/bin"
  tar -C "$REPO" --exclude='./target' --exclude='./vm/target' --exclude='__pycache__' \
    --exclude='./server_config.json' \
    --exclude='*.pyc' --exclude='*.log' -cf - . | tar -C "$STAGE" -xf -
  cp "$EXE" "$STAGE/bin/fvp-preview-vm.exe"
  OUT="/tmp/fvp-preview-v$VER-win64.zip"
  rm -f "$OUT"
  (cd "$(dirname "$STAGE")" && zip -qr "$OUT" "$(basename "$STAGE")")
  sha256sum "$OUT"
  ls -la "$OUT"
  echo "== Windows 软件包：$OUT =="
  echo "   用法：解压 → 设 FVP_BASE_PATH → 双击 start_editor.bat"
  exit 0
fi
echo "== 编 vm release（含 conform 0001..0008，需 rfvp 兄弟检出已打补丁） =="
cargo build --release --bin fvp-preview-vm --manifest-path "$REPO/vm/Cargo.toml" 2>&1 | tail -1
BIN="$CARGO_TARGET_DIR/release/fvp-preview-vm"
[ -x "$BIN" ] || { echo "缺二进制: $BIN"; exit 1; }
STAGE="$(mktemp -d)/fvp-preview-$VER"
mkdir -p "$STAGE/bin"
# 进包清单：代码+脚本+web+说明；target/__pycache__/日志/本地路径配置一律不要
tar -C "$REPO" --exclude='./target' --exclude='./vm/target' --exclude='__pycache__' \
  --exclude='./server_config.json' \
  --exclude='*.pyc' --exclude='*.log' -cf - . | tar -C "$STAGE" -xf -
cp "$BIN" "$STAGE/bin/fvp-preview-vm"
OUT="/tmp/fvp-preview-v$VER.tar.gz"
tar -C "$(dirname "$STAGE")" -czf "$OUT" "$(basename "$STAGE")"
sha256sum "$OUT"
ls -la "$OUT"
echo "== 附到 release：$OUT + $BIN（单文件备用） =="
