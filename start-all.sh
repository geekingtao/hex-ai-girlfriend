#!/usr/bin/env bash
# =============================================================================
#  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
#  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
#  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
#  SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
#  一键命令：数字人 + 编排层 的启动 / 停止 / 看日志 / 看状态
#  用法: ./start-all.sh [start|stop|logs|status]
#  前提: 已运行 ./install.sh；Windows 侧 llama-server 端口见 config.yaml 的 llm.base_url
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SESSION="hexgirl"
LOG_DIR="$SCRIPT_DIR/logs"
ORCH_LOG="$LOG_DIR/orchestrator.log"   # 编排层：对话/回答/字幕，主要交互记录
LT_LOG="$LOG_DIR/livetalking.log"      # 数字人口型服务

C_OK=$'\033[32m'; C_W=$'\033[33m'; C_R=$'\033[0m'; C_D=$'\033[2m'
say() { echo; echo "${C_OK}▸${C_R} $*"; }
dim() { echo "${C_D}   $*${C_R}"; }
die() { echo "${C_R}✗${C_R}  $*" >&2; exit 1; }

# 端口单一来源：config.yaml，改端口只需改那一处，这里自动跟随
CFG="$SCRIPT_DIR/config.yaml"
LLM_BASE=$(sed -n 's/^[[:space:]]*base_url:[[:space:]]*\([^ #]*\).*/\1/p' "$CFG" | head -1)
AV_BASE=$(sed -n 's/^[[:space:]]*base_url:[[:space:]]*\([^ #]*\).*/\1/p' "$CFG" | tail -1)
LLM_PORT=${LLM_BASE##*:}; LLM_PORT=${LLM_PORT%/v1}
AV_PORT=${AV_BASE##*:}; AV_PORT=${AV_PORT%/v1}
ORCH_PORT=$(sed -n 's/^[[:space:]]*port:[[:space:]]*\([0-9]*\).*/\1/p' "$CFG" | head -1)

case "${1:-start}" in
  stop)
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "已停止。" || echo "没有运行的会话。"
    exit 0 ;;

  status)
    echo "各服务状态（端口以 config.yaml 为准）:"
    # llama-server 在 Windows 侧：WSL2 下 127.0.0.1 不通则回退到网关（Windows 主机 IP）
    gate=$(ip route 2>/dev/null | awk '/default/{print $3; exit}') || true
    llm_ok=0
    for base in "http://127.0.0.1:$LLM_PORT" "http://$gate:$LLM_PORT"; do
      if curl -sf -m 2 "$base/health" >/dev/null 2>&1; then llm_ok=1; break; fi
    done
    if [ "$llm_ok" = "1" ]; then
      printf "  %s %-22s %-6s %s\n" "${C_OK}●${C_R}" "大模型 llama-server" ":$LLM_PORT" "运行中"
    else
      printf "  %s %-22s %-6s %s\n" "${C_W}○${C_R}" "大模型 llama-server" ":$LLM_PORT" "未运行（Windows 侧需启动 llama-server）"
    fi
    if ss -ltn "sport = :$AV_PORT" 2>/dev/null | grep -q LISTEN; then
      printf "  %s %-22s %-6s %s\n" "${C_OK}●${C_R}" "数字人 LiveTalking" ":$AV_PORT" "运行中"
    else
      printf "  %s %-22s %-6s %s\n" "${C_W}○${C_R}" "数字人 LiveTalking" ":$AV_PORT" "未运行"
    fi
    if ss -ltn "sport = :$ORCH_PORT" 2>/dev/null | grep -q LISTEN; then
      printf "  %s %-22s %-6s %s\n" "${C_OK}●${C_R}" "编排层 orchestrator" ":$ORCH_PORT" "运行中"
    else
      printf "  %s %-22s %-6s %s\n" "${C_W}○${C_R}" "编排层 orchestrator" ":$ORCH_PORT" "未运行"
    fi
    exit 0 ;;

  logs)
    shift
    need() { [ -f "$1" ] || { echo "✗ 还没有 $1 —— 服务还没启动过，先 ./start-all.sh"; exit 1; }; }
    case "${1:-}" in
      "")
        need "$ORCH_LOG"
        echo "${C_OK}▸${C_R} 跟随交互记录: ${C_D}$ORCH_LOG${C_R}  (Ctrl+C 退出)"
        exec tail -F "$ORCH_LOG" ;;
      lt)
        need "$LT_LOG"
        exec tail -F "$LT_LOG" ;;
      all)
        need "$ORCH_LOG"; need "$LT_LOG"
        exec tail -F "$ORCH_LOG" "$LT_LOG" ;;
      n|last)
        need "$ORCH_LOG"
        lines="${2:-50}"
        case "$lines" in *[!0-9]*|"") lines=50;; esac
        exec tail -n "$lines" "$ORCH_LOG" ;;
      clear)
        rm -f "$ORCH_LOG" "$LT_LOG"
        echo "已清空日志（下次启动自动重建）。"
        exit 0 ;;
      dir)
        echo "$LOG_DIR"
        exit 0 ;;
      *)
        cat <<'TXT'
用法: ./start-all.sh logs [子命令]   (Ctrl+C 退出跟随)

  （无子命令）  实时跟随交互记录（对话/回答/字幕），Ctrl+C 退出
  lt            实时跟随数字人口型日志
  all           同时跟随两个日志
  n [行数]      只打印最近 N 行，不跟随（默认 50）
  clear         清空两个日志
  dir           打印日志目录

示例:
  ./start-all.sh logs        # 盯对话，看她怎么回
  ./start-all.sh logs n 200  # 回看最近 200 行
TXT
        exit 0 ;;
    esac
    ;;

  start) ;;
  *) die "用法: $0 [start|stop|logs|status]" ;;
esac

# ---------------------------------------------------------------- 前置检查
[ -d "$SCRIPT_DIR/.venv" ] || die "缺少 .venv，请先运行 ./install.sh"
[ -d "$SCRIPT_DIR/third_party/livetalking/.venv-lt" ] || die "缺少 third_party/livetalking/.venv-lt，请先运行 ./install.sh"
command -v tmux >/dev/null || die "缺少 tmux: sudo apt install -y tmux"
tmux has-session -t "$SESSION" 2>/dev/null && die "已在运行，先执行: $0 stop"
mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------- 检查 llama-server
say "检查 Windows 侧 llama-server (:$LLM_PORT)"
LLM_OK=0
for base in "http://127.0.0.1:$LLM_PORT" "http://$(ip route 2>/dev/null | awk '/default/{print $3; exit}' || true):$LLM_PORT"; do
  if curl -sf -m 3 "$base/health" >/dev/null 2>&1; then LLM_OK=1; break; fi
done
if [ "$LLM_OK" = "1" ]; then
  dim "  llama-server 已就绪"
else
  echo "${C_W}  ⚠ 未检测到 llama-server。${C_R}"
  dim "  请先在 Windows 侧启动:"
  dim "    llama-server -m <模型.gguf> -np 1 -c 8192 -fa on \\"
  dim "      --cache-type-k q8_0 --cache-type-v q8_0 --host 0.0.0.0 --port $LLM_PORT"
  read -rp "  仍继续启动其他服务? [y/N] " yn
  [ "${yn:-N}" = "y" ] || { echo "已取消。"; exit 1; }
fi

# ---------------------------------------------------------------- CUDA 12 运行库 (onnxruntime/CosyVoice)
# torch 以 nvidia-* pip 包把 CUDA 12.4 运行库装进 site-packages/nvidia/*/lib。
# CosyVoice 的 speech tokenizer（onnxruntime）加载 CUDAExecutionProvider 时要找
# libcudart.so.12 / libcublasLt.so.12 / libcudnn.so.9，不 export 会回退 CPU 并报错。
NVIDIA_LD=""
for _d in "$SCRIPT_DIR"/.venv/lib/python3.10/site-packages/nvidia/*/lib; do
  [ -d "$_d" ] && NVIDIA_LD="${NVIDIA_LD:+$NVIDIA_LD:}$_d"
done

# ---------------------------------------------------------------- 启动
say "启动 LiveTalking 口型服务 (:$AV_PORT)"
tmux new-session -d -s "$SESSION" -n livetalking
tmux send-keys -t "$SESSION:livetalking" \
  "cd '$SCRIPT_DIR' && ./start-livetalking.sh 2>&1 | tee -a '$LT_LOG'" C-m

say "启动编排层 (:$ORCH_PORT)"
tmux new-window -t "$SESSION" -n orchestrator
tmux send-keys -t "$SESSION:orchestrator" \
  "export LD_LIBRARY_PATH=\"$NVIDIA_LD\" && cd '$SCRIPT_DIR' && source .venv/bin/activate && python src/server.py --config config.yaml 2>&1 | tee -a '$ORCH_LOG'" C-m

sleep 3
cat <<TXT

${C_OK}════════════════════════════════════════════════════════════${C_R}
  已启动。浏览器打开:  ${C_OK}http://localhost:$ORCH_PORT${C_R}

  ● 点「开始对话」并允许麦克风权限（必须 localhost / HTTPS）
  ● 状态  $0 status     停止  $0 stop
  ● 看日志  $0 logs         实时跟随对话记录
  ● 单独   ./start-livetalking.sh（口型调试用）
${C_OK}════════════════════════════════════════════════════════════${C_R}
TXT
