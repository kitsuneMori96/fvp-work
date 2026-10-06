#!/bin/bash
# 打 release 包：fvp-preview-v<VERSION>.tar.gz（含 Linux x86_64 vm 二进制，开箱即用）。
# 用法：bash pack_release.sh [VERSION]（默认 0.1.0）
# 二进制不进 git，只进 release 包（见根 .gitignore）。
set -e
VER="${1:-0.1.0}"
REPO="$(cd "$(dirname "$0")" && pwd)"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/cargo-target/fvp-preview}"
echo "== 编 vm release（含 conform 0001..0008，需 rfvp 兄弟检出已打补丁） =="
cargo build --release --bin fvp-preview-vm --manifest-path "$REPO/vm/Cargo.toml" 2>&1 | tail -1
BIN="$CARGO_TARGET_DIR/release/fvp-preview-vm"
[ -x "$BIN" ] || { echo "缺二进制: $BIN"; exit 1; }
STAGE="$(mktemp -d)/fvp-preview-$VER"
mkdir -p "$STAGE/bin"
# 进包清单：代码+脚本+web+说明；target/__pycache__/日志一律不要
tar -C "$REPO" --exclude='./target' --exclude='./vm/target' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='*.log' -cf - . | tar -C "$STAGE" -xf -
cp "$BIN" "$STAGE/bin/fvp-preview-vm"
OUT="/tmp/fvp-preview-v$VER.tar.gz"
tar -C "$(dirname "$STAGE")" -czf "$OUT" "$(basename "$STAGE")"
sha256sum "$OUT"
ls -la "$OUT"
echo "== 附到 release：$OUT + $BIN（单文件备用） =="
