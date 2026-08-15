#!/usr/bin/env bash
# =============================================================================
#  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
#  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
#  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
#  SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
# 数字人口型服务 (LiveTalking) 启停 —— 指向包内 third_party/livetalking/
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LT_ROOT="${LT_ROOT:-$SCRIPT_DIR/third_party/livetalking}"
VENV="$LT_ROOT/.venv-lt"
AVATAR_ID="${AVATAR_ID:-wav2lip256_avatar1}"
MODEL="${MODEL:-wav2lip}"
STUN="${STUN:-stun:stun.l.google.com:19302}"
PORT="${PORT:-8010}"

case "${1:-start}" in
  stop)
    pkill -f "app.py --transport webrtc" 2>/dev/null && echo "已停止。" || echo "未在运行。"
    exit 0 ;;
  status)
    if ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then echo "运行中 :$PORT"; else echo "未运行 :$PORT"; fi
    exit 0 ;;
  start) ;;
  *) echo "用法: $0 [start|stop|status]"; exit 1 ;;
esac

[ -d "$VENV" ] || { echo "找不到 $VENV，请先运行 ./install.sh"; exit 1; }
[ -d "$LT_ROOT/data/avatars/$AVATAR_ID" ] || {
  echo "找不到形象 $AVATAR_ID（期望 $LT_ROOT/data/avatars/$AVATAR_ID）";
  echo "请确认已运行 ./install.sh（会自动解压形象包），或用 http://localhost:$PORT/avatar.html 生成";
  exit 1; }
ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN && { echo "端口 $PORT 已被占用，先 $0 stop"; exit 1; }

cd "$LT_ROOT"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
echo "启动 LiveTalking  形象: $AVATAR_ID  模型: $MODEL  调试: http://localhost:$PORT/index.html"
exec python app.py --transport webrtc --model "$MODEL" \
  --avatar_id "$AVATAR_ID" --stun "$STUN"
