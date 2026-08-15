<!--
  Hex AI Girlfriend — 本地实时数字人语音对话
  Copyright (C) 2026 Hex-电脑课堂
  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
  SPDX-License-Identifier: GPL-3.0-or-later
-->
# 🗣️ Hex AI Girlfriend · 本地实时数字人语音对话

> 🌐 语言 / Language：🇨🇳 **中文** | [🇬🇧 English](README_EN.md)

> 在浏览器里和你 **实时语音对话** 的 AI 数字人：用 **你克隆的音色** 说话、**对嘴型**、**随时打断**。
> **全本地运行 · 零 API 费用 · 断网可用**（仅首次安装依赖需要联网）。

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-GPL%20v3-blue.svg" alt="License GPL v3"></a>
  <img src="https://img.shields.io/badge/Platform-Windows%20%2B%20WSL2%2FUbuntu-3b82f6.svg" alt="Platform">
  <img src="https://img.shields.io/badge/TTS-CosyVoice2-8b5cf6.svg" alt="TTS">
  <img src="https://img.shields.io/badge/STT-SenseVoice-10b981.svg" alt="STT">
  <img src="https://img.shields.io/badge/Avatar-Wav2Lip%2FLiveTalking-ef4444.svg" alt="Avatar">
  <img src="https://img.shields.io/badge/LLM-llama.cpp%20%2F%20Qwen-0ea5e9.svg" alt="LLM">
  <img src="https://img.shields.io/badge/Cloud-100%25%20Local-22c55e.svg" alt="100% Local">
</p>

---

### 📺 关注作者 · 订阅频道

> 🎬 **Hex-电脑课堂** —— 专注分享 **AI 数字人 · 语音克隆 · 本地大模型** 的实战教程。
> 本项目从 0 到 1 的完整搭建过程、踩坑记录、进阶玩法，都会在频道逐步更新。
> 👉 点这里订阅：**https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA**

---

### 🖼️ 效果预览

![项目效果预览](data/picture_1.png)

> 浏览器打开 `http://localhost:7860` → 点「开始对话」→ 允许麦克风，即可与数字人实时对话。

---

### ✨ 功能特性

| 功能 | 说明 |
|---|---|
| 🎤 实时语音对话 | 浏览器点「开始对话」即可说话，支持随时打断、抢话 |
| 🎙️ 克隆音色 | CosyVoice2 用 `data/prompt1.mp3` 克隆你自己的声音 |
| 🗣️ 数字人对嘴型 | Wav2Lip / LiveTalking 让数字人嘴巴跟着声音动 |
| 🤖 本地大模型 | 接 Windows 侧 llama-server（Qwen3 等 GGUF 模型） |
| 🧩 引擎可插拔 | LLM/STT/TTS 都可在 `config.yaml` 一键切换，含 `test` 假引擎联调 |
| 🔒 全本地运行 | 推理全在本地，数据不出门、断网可用、零 API 费用 |

---

### 🚀 项目优势

- **零成本**：不需要任何云端 API Key，一次装好永久免费使用。
- **隐私安全**：语音与对话数据全部留在你自己的电脑上，绝不外传。
- **新手友好**：整合包预置全部模型与依赖，`./install.sh` 一条命令装完。
- **可换音色 / 换形象**：换一段参考音频、换一个数字人形象包即可变身。
- **交互自然**：基于 VAD 的端点检测，说话过程中随时插嘴，体验流畅。

---

### 🧱 架构一览

```
┌──────── 浏览器 (web/) ────────┐
│  麦克风采集 / 播放 / 数字人画面  │
└──────────────┬────────────────┘
   WebSocket (OpenAI Realtime 风格)
┌──────────────▼────────────────┐
│  编排层 src/（FastAPI）         │
│  STT←SenseVoice  LLM←llama.cpp│
│  TTS←CosyVoice2  VAD←silero   │
└──────────────┬────────────────┘
        HTTP /humanaudio
┌──────────────▼────────────────┐
│  数字人 LiveTalking (Wav2Lip)  │
│  WebRTC 回传 音轨+视频轨        │
└───────────────────────────────┘
```

---

### 🛠️ 快速开始（新手跟着做）

**环境要求**：Windows 10/11 + WSL2 Ubuntu 24.04 + NVIDIA 显卡驱动。

1. 把整合包解压到 WSL 里，例如 `~/hex-ai-girlfriend`
2. Windows 侧启动 llama-server（端口与 `config.yaml` 的 `llm.base_url` 一致）
3. 安装并启动：

```bash
cd ~/hex-ai-girlfriend
dos2unix *.sh && chmod +x *.sh
./install.sh
```

```bash
./start-all.sh
```

4. 浏览器打开 http://localhost:7860 → 点「开始对话」→ 允许麦克风。

> llama-server 启动示例（端口与 `config.yaml` 的 `llm.base_url` 一致即可）：
> ```
> llama-server -m <模型.gguf> -np 1 -c 8192 -fa on \
>   --cache-type-k q8_0 --cache-type-v q8_0 --host 0.0.0.0 --port 8200
> ```

---

### 📄 日常使用

| 命令 | 作用 |
|---|---|
| `./start-all.sh` | 一键启动（默认） |
| `./start-all.sh status` | 查看各服务状态 |
| `./start-all.sh stop` | 停止所有服务 |
| `./start-all.sh logs` | 实时看对话日志（Ctrl+C 退出） |
| `./start-livetalking.sh` | 单独启动口型服务（调试用） |

---

### ⚙️ 常用配置

`config.yaml` 是唯一需要关注的配置文件：

- `llm.engine / stt.engine / tts.engine`：引擎可插拔，改字段即可切换；
  改成 `"test"` 可无模型联调整条链路（TestTTS 发正弦波看数字人开口）。
- `tts.ref_wav / tts.ref_text`：克隆音色的参考音频及其逐字文本，两个一起换。
- `persona.md`：人设（默认模板「阿汐」，可随意改成你自己的）。
- `vad.*`：对话手感参数，抢答就调大 `vad.min_silence_ms`。

---

### ❓ 常见问题

| 问题 | 解决 |
|---|---|
| `bad interpreter: ^M` | 先 `dos2unix *.sh` |
| WebRTC 连不上 | `.wslconfig` 加 `hostAddressLoopback=true` 后 `wsl --shutdown` |
| 麦克风没反应 | 必须用 localhost 或 HTTPS 访问 |
| 抢答 / 漏字 | 调 `config.yaml` 的 `vad.min_silence_ms` |
| 显存不够 | 换 8B/4B 小模型，STT 可 `stt.device: cpu` |

---

### 🙏 致谢

本项目站在这些优秀的开源项目之上：

| 项目 | 用途 | 协议 |
|---|---|---|
| [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | TTS 语音克隆 | Apache-2.0 |
| [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) | STT 语音识别（FunASR） | Apache-2.0 |
| [LiveTalking](https://github.com/HertzDev/LiveTalking) | 数字人嘴型（Wav2Lip） | Apache-2.0 |
| [silero-vad](https://github.com/snakers4/silero-vad) | 语音端点检测 | MIT |
| [llama.cpp](https://github.com/ggerganov/llama.cpp) | 本地 LLM 推理 | MIT |
| [Qwen](https://github.com/QwenLM/Qwen3) | 对话大模型 | Apache-2.0 |
| [hf-realtime-voice](https://github.com/philschmid/hf-realtime-voice) | 前端 orb 界面与音频管线 | Apache-2.0 |

---

### ⚠️ 免责声明

- 本项目仅供**学习、研究与合法用途**。
- 请勿将本项目用于任何违法、侵权或侵害他人权益的场景。
- **克隆他人声音前，必须获得本人明确授权**；由此引发的任何法律纠纷与纠纷责任，项目作者概不负责。
- 使用本项目即表示你已阅读、理解并同意以上全部条款。

---

### 📜 开源协议

- 本项目基于 **GPL-3.0** 协议开源，详见 [LICENSE](LICENSE)。
- 第三方组件版权归其各自作者所有，遵循各自的开源协议。
