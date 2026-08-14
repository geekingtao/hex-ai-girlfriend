# -*- coding: utf-8 -*-
# =============================================================================
#  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
#  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
#  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
#  SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
"""音频工具：PCM16 <-> float32、float32 -> WAV 字节、重采样"""
import io
import wave

import numpy as np


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """浏览器传来的 Int16LE PCM -> float32 ([-1, 1])"""
    a = np.frombuffer(data, dtype="<i2")
    return a.astype(np.float32) / 32768.0


def numpy_to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    """float32 单声道 -> WAV 文件字节（16kHz 用于喂数字人）"""
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def wav_to_pcm16_bytes(wav: bytes) -> bytes:
    """WAV 文件字节 -> 裸 Int16LE PCM16（剥掉 RIFF/data 头；无头则原样返回）。

    TTS 产出的 numpy_to_wav_bytes 带 WAV 头，而 WS response.output_audio.delta
    要的是 16kHz 裸 PCM16 的 base64，浏览器端直接按 Int16 解析。数字人那边
    则要完整 WAV（/humanaudio 收文件），所以剥头只用于发给浏览器的那一路。
    """
    if wav[:4] == b"RIFF":
        idx = wav.find(b"data", 12)
        if idx >= 0:
            return wav[idx + 8:]
    return wav


def resample_np(x: np.ndarray, in_rate: int, out_rate: int) -> np.ndarray:
    """线性插值重采样（语音足够用）"""
    if in_rate == out_rate:
        return x
    n = max(1, int(len(x) * out_rate / in_rate))
    xp = np.linspace(0, len(x) - 1, n)
    return np.interp(xp, np.arange(len(x)), x)
