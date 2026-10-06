#!/bin/bash
# 编辑器一键启动（WSL2 内运行，Windows 浏览器访问）。
# 用法：bash start_editor.sh [SAMPLE_DIR] [PORT]
# 剧本编辑模式：SIMPLE_DIR=/path/to/Simple-hcb-Editor SCRIPT=/path/to/剧本.txt
#   bash start_editor.sh [SAMPLE_DIR] [PORT]（自动透传 --simple-dir/--script）
# 输出两个地址：WSL IP（Windows 侧用这个）+ localhost（备用）。
SAMPLE_DIR="${1:-/tmp/fvp-sample}"
PORT="${2:-8000}"
REPO="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$SAMPLE_DIR/scene.json" ]; then
  echo "示例工程不存在，先物化："
  echo "  export FVP_BASE_PATH=/path/to/game"
  echo "  bash $REPO/sample_tachie.sh $SAMPLE_DIR"
  exit 1
fi
# 预览不需要音频：跳过 voice/bgm/se 包的扫表（0007；9P 上全量扫表要 6s）。
export FVP_VFS_SKIP="${FVP_VFS_SKIP:-voice,bgm,se,se_sys,se_env}"
# vm 二进制：release 包自带 bin/；源码用户走 vm/target/release（FVP_VM_BIN 显式优先）。
if [ -z "$FVP_VM_BIN" ]; then
  if [ -x "$REPO/bin/fvp-preview-vm" ]; then export FVP_VM_BIN="$REPO/bin/fvp-preview-vm";
  elif [ -x "$REPO/vm/target/release/fvp-preview-vm" ]; then export FVP_VM_BIN="$REPO/vm/target/release/fvp-preview-vm"; fi
fi
if pgrep -f "serve_editor.py.*$PORT" >/dev/null 2>&1; then
  echo "服务已在跑（端口 $PORT），不重复启动。"
else
  setsid nohup python3 "$REPO/serve_editor.py" --sample-dir "$SAMPLE_DIR" \
    --port "$PORT" ${FVP_HCB:+--hcb "$FVP_HCB" --addrmap "$FVP_ADDRMAP" --out-hcb "$FVP_OUT"} \
    ${SIMPLE_DIR:+--simple-dir "$SIMPLE_DIR"} ${SCRIPT:+--script "$SCRIPT"} > /tmp/editor-$PORT.log 2>&1 < /dev/null &
  sleep 2
fi
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "=== fvp-preview web ==="
echo "Windows 浏览器开：http://$IP:$PORT/"
echo "备用（WSL 内）：http://localhost:$PORT/"
curl -s -m 5 "http://127.0.0.1:$PORT/" -o /dev/null -w "自检: HTTP %{http_code}\n"
