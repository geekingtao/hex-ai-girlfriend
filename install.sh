#!/usr/bin/env bash
# =============================================================================
#  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
#  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
#  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
#  SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
#  hex-girlfriend 一键安装 (WSL2 / Ubuntu 24.04)
#
#  模型与三个仓库已预置在整合包内，本脚本只需联网装 Python 依赖 + 放置模型/形象。
#  用法: chmod +x install.sh && ./install.sh
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

# llama-server 端口单一来源：config.yaml 的 llm.base_url，改端口只需改那一处
LLM_BASE=$(sed -n 's/^[[:space:]]*base_url:[[:space:]]*\([^ #]*\).*/\1/p' config.yaml | head -1)
LLM_PORT=${LLM_BASE##*:}
LLM_PORT=${LLM_PORT%/v1}

C_OK=$'\033[32m'; C_W=$'\033[33m'; C_R=$'\033[0m'; C_D=$'\033[2m'
say()  { echo; echo "${C_OK}▸${C_R} $*"; }
dim()  { echo "${C_D}   $*${C_R}"; }
die()  { echo "${C_R}✗${C_R}  $*" >&2; exit 1; }

# ---------------------------------------------------------------- [1/6] 自检
say "[1/6] 环境自检"
grep -qi microsoft /proc/version 2>/dev/null || say "  (提示: 看起来不在 WSL 里, 脚本仍会继续)"
command -v nvidia-smi >/dev/null || die "缺少 nvidia-smi (驱动只装 Windows 侧，WSL 内勿装)"
dim "  GPU  : $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)"
command -v ffmpeg >/dev/null || { say "  sudo apt 安装系统依赖..."; sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg libsndfile1 libgl1 libglib2.0-0 tmux; }
command -v tmux  >/dev/null || { say "  安装 tmux..."; sudo apt-get install -y -qq tmux; }
if ! command -v uv >/dev/null; then
  say "  安装 uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
dim "  uv: $(uv --version 2>/dev/null || echo ok)"

# ---------------------------------------------------------------- [2/6] 包内资源
say "[2/6] 校验包内模型/仓库"
for d in "models/CosyVoice2-0.5B" "models/SenseVoiceSmall" \
         "third_party/CosyVoice/cosyvoice" "third_party/livetalking/app.py" "models/silero-vad/silero_vad.jit"; do
  [ -e "$d" ] || die "缺少 $d，请确认整合包完整（解压后勿删任何目录）"
done
dim "  模型与仓库齐全"
[ -f "models/wav2lip256.pth" ] || die "缺少 models/wav2lip256.pth（LiveTalking 模型）"
[ -f "models/wav2lip256_avatar1.tar.gz" ] || die "缺少 models/wav2lip256_avatar1.tar.gz（数字人形象）"

# ---------------------------------------------------------------- [3/6] 编排层 venv
say "[3/6] 编排层虚拟环境 .venv (Python 3.10)"
[ -d .venv ] || uv venv --python 3.10 .venv
dim "  安装 PyTorch 2.5.0 (cu124)..."
uv pip install --python .venv/bin/python torch==2.5.0 torchvision==0.20.0 torchaudio==2.5.0 \
  --index-url https://download.pytorch.org/whl/cu124
dim "  安装全部依赖 (fastapi / funasr / CosyVoice)..."
uv pip install --python .venv/bin/python -r requirements.txt

# ---------------------------------------------------------------- [4/6] LiveTalking venv
say "[4/6] LiveTalking 虚拟环境 third_party/livetalking/.venv-lt (Python 3.10)"
[ -d third_party/livetalking/.venv-lt ] || uv venv --python 3.10 third_party/livetalking/.venv-lt
dim "  安装 PyTorch 2.5.0 (cu124)..."
uv pip install --python third_party/livetalking/.venv-lt/bin/python torch==2.5.0 torchvision==0.20.0 torchaudio==2.5.0 \
  --index-url https://download.pytorch.org/whl/cu124
dim "  安装 LiveTalking 依赖..."
uv pip install --python third_party/livetalking/.venv-lt/bin/python -r third_party/livetalking/requirements.txt

# ---------------------------------------------------------------- [5/6] 放置模型/形象
say "[5/6] 放置模型与数字人形象"
if [ ! -f "third_party/livetalking/models/wav2lip.pth" ]; then
  mkdir -p "third_party/livetalking/models"
  cp "models/wav2lip256.pth" "third_party/livetalking/models/wav2lip.pth"
  dim "  models/wav2lip256.pth → third_party/livetalking/models/wav2lip.pth"
fi
if [ ! -d "third_party/livetalking/data/avatars/wav2lip256_avatar1" ]; then
  mkdir -p "third_party/livetalking/data/avatars"
  tar -xzf "models/wav2lip256_avatar1.tar.gz" -C "third_party/livetalking/data/avatars/"
  dim "  models/wav2lip256_avatar1.tar.gz → third_party/livetalking/data/avatars/"
fi
dim "  完成"

# ---------------------------------------------------------------- [6/6] 完成
cat <<TXT

${C_OK}════════════════════════════════════════════════════════════${C_R}
  安装完成。

  启动（顺序）:
    1) Windows 侧确保 llama-server 已在 :${LLM_PORT} 运行
         llama-server -m 你的模型.gguf -np 1 -c 8192 -fa on \\
           --cache-type-k q8_0 --cache-type-v q8_0 --host 0.0.0.0 --port ${LLM_PORT}
    2) ./start-all.sh        ← 一键启动数字人 + 编排层
    3) 浏览器打开  http://localhost:7860   ，点「开始对话」

  常用:
    ./start-all.sh start             一键启动（默认）
    ./start-all.sh status / stop     查看状态 / 停止
    ./start-all.sh logs              实时看对话记录
    ./start-livetalking.sh           单独起口型（调试用）

  改过 .sh / config.yaml 后:  dos2unix *.sh && chmod +x *.sh
  首次联网装依赖约 10-20 分钟；二次安装各步骤会跳过已装部分。
${C_OK}════════════════════════════════════════════════════════════${C_R}
TXT
