<!--
  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
  SPDX-License-Identifier: GPL-3.0-or-later
-->
# 🗣️ Hex AI Girlfriend · Real-time AI Digital Girlfriend

> 🌐 Language：🇨🇳 [中文](README.md) | 🇬🇧 **English**

> An AI digital human that talks to you **in real time in the browser**: it speaks with
> **your cloned voice**, **lip-syncs**, and can be **interrupted mid-sentence**.
> **100% local · Zero API cost · Works offline** (only the first dependency install needs internet).

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

### 📺 Watch the author · Subscribe

> 🎬 **Hex-Computer-Classroom** — practical tutorials on **AI digital humans · voice cloning · local LLMs**.
> The full build process of this project, along with tips & advanced play, is shared on the channel.
> 👉 Subscribe: **https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA**

---

### 🖼️ Preview

![Project preview](data/picture_1.png)

> Open `http://localhost:7860` in your browser → click **"Start"** → allow the microphone → start talking.

---

### ✨ Features

| Feature | Description |
|---|---|
| 🎤 Real-time voice chat | Talk via the browser, interrupt the AI any time |
| 🎙️ Voice cloning | CosyVoice2 clones your voice from `data/prompt1.mp3` |
| 🗣️ Lip-sync avatar | Wav2Lip / LiveTalking sync the avatar's lips |
| 🤖 Local LLM | Connects to Windows-side llama-server (Qwen GGUF) |
| 🧩 Plug-and-play engines | LLM/STT/TTS switch in `config.yaml`, incl. a `test` mock |
| 🔒 Fully local | No data leaves your machine, offline-capable, $0 cost |

---

### 🚀 Why this project

- **Free forever**: no cloud API keys, install once and use forever.
- **Private**: all speech & chat data stays on your own machine.
- **Beginner-friendly**: the bundle ships all models & dependencies — one command installs everything.
- **Customizable**: swap the reference audio for a new voice, swap the avatar pack for a new look.
- **Natural interaction**: VAD-based endpoint detection lets you interrupt anytime.

---

### 🧱 Architecture

```
┌──────── Browser (web/) ────────┐
│  mic capture / playback / avatar│
└──────────────┬────────────────┘
   WebSocket (OpenAI Realtime style)
┌──────────────▼────────────────┐
│  Orchestrator src/ (FastAPI)   │
│  STT←SenseVoice  LLM←llama.cpp │
│  TTS←CosyVoice2  VAD←silero    │
└──────────────┬────────────────┘
        HTTP /humanaudio
┌──────────────▼────────────────┐
│  Digital human LiveTalking     │
│  WebRTC: audio + video back    │
└───────────────────────────────┘
```

---

### 🛠️ Quick Start

**Requirements**: Windows 10/11 + WSL2 Ubuntu 24.04 + NVIDIA driver (Windows-side only).

1. Unpack the bundle into WSL, e.g. `~/hex-ai-girlfriend`
2. Start llama-server on the **Windows side** (port must match `config.yaml` → `llm.base_url`)
3. Install & run:

```bash
cd ~/hex-ai-girlfriend
dos2unix *.sh && chmod +x *.sh
./install.sh
./start-all.sh
```

4. Open http://localhost:7860 → click **Start** → allow the microphone.

> llama-server example (match the port to `llm.base_url` in `config.yaml`):
> ```
> llama-server -m <model.gguf> -np 1 -c 8192 -fa on \
>   --cache-type-k q8_0 --cache-type-v q8_0 --host 0.0.0.0 --port 8200
> ```

---

### 📄 Daily Use

| Command | Action |
|---|---|
| `./start-all.sh` | One-command start |
| `./start-all.sh status` | Check service status |
| `./start-all.sh stop` | Stop all services |
| `./start-all.sh logs` | Watch live chat logs (Ctrl+C to exit) |
| `./start-livetalking.sh` | Start lip-sync service only (debug) |

---

### ⚙️ Configuration

All in `config.yaml`:

- `llm.engine / stt.engine / tts.engine`: switch engines by editing the field;
  set to `"test"` to verify the whole chain without real models.
- `tts.ref_wav / tts.ref_text`: the reference audio for voice cloning and its verbatim transcript — change them together.
- `persona.md`: the AI's persona (default template "A Xi").
- `vad.*`: conversation feel; raise `vad.min_silence_ms` if it interrupts too eagerly.

---

### ❓ FAQ

| Problem | Fix |
|---|---|
| `bad interpreter: ^M` | run `dos2unix *.sh` first |
| WebRTC won't connect | add `hostAddressLoopback=true` to `.wslconfig`, then `wsl --shutdown` |
| Microphone dead | access via localhost or HTTPS only |
| Interrupts / missed words | tune `vad.min_silence_ms` in `config.yaml` |
| Not enough VRAM | use a smaller model, or set `stt.device: cpu` |

---

### 🙏 Acknowledgments

This project stands on the shoulders of these great open-source projects:

| Project | Role | License |
|---|---|---|
| [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | TTS voice cloning | Apache-2.0 |
| [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) | STT recognition (FunASR) | Apache-2.0 |
| [LiveTalking](https://github.com/HertzDev/LiveTalking) | Lip-sync avatar (Wav2Lip) | Apache-2.0 |
| [silero-vad](https://github.com/snakers4/silero-vad) | Voice activity detection | MIT |
| [llama.cpp](https://github.com/ggerganov/llama.cpp) | Local LLM inference | MIT |
| [Qwen](https://github.com/QwenLM/Qwen3) | Conversational LLM | Apache-2.0 |
| [hf-realtime-voice](https://github.com/philschmid/hf-realtime-voice) | Frontend orb UI & audio pipeline | Apache-2.0 |

---

### ⚠️ Disclaimer

- This project is provided **for learning, research and legal purposes only**.
- Do not use it for any illegal, infringing, or harmful activity.
- **Obtain explicit permission before cloning anyone's voice**; the author is
  not responsible for any legal disputes arising from misuse.
- By using this project you agree to all the terms above.

---

### 📜 License

- This project is released under the **GPL-3.0** license. See [LICENSE](LICENSE).
- Third-party components retain their own copyrights and licenses.
