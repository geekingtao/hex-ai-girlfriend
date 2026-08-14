# -*- coding: utf-8 -*-
# =============================================================================
#  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
#  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
#  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
#  SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
"""VAD 语音活动检测：silero-vad + 端点判定器（手感参数全在此）"""
import os

import numpy as np
import torch

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根（src/ 的上一级）


class SileroVAD:
    """silero-vad，逐 512 样本窗（16kHz 下 32ms）输出说话概率。"""

    def __init__(self, device="cpu", sample_rate=16000):
        self.sr = sample_rate
        self.win = 512
        self.device = device
        jit_path = os.path.join(BASE_DIR, "models", "silero-vad", "silero_vad.jit")
        if os.path.exists(jit_path):
            # 离线：直接用包内 jit 权重，不联网
            model = torch.jit.load(jit_path, map_location=device)
        else:
            # 回退：包内缺失时走 torch.hub 在线拉取（需联网）
            model, _ = torch.hub.load(
                "snakers4/silero-vad", "silero_vad",
                trust_repo=True, force_reload=False, onnx=False)
        model.eval()
        self.model = model.to(device)

    def speech_prob(self, frame: np.ndarray) -> float:
        t = torch.from_numpy(np.ascontiguousarray(frame, dtype=np.float32)).unsqueeze(0)
        with torch.no_grad():
            return float(self.model(t, self.sr).item())


class EndpointDetector:
    """消费逐窗概率，产出 speech_started / speech_ended(segment) 事件。

    手感旋钮：
      threshold       人声判定门槛
      min_speech_ms   最短有效语音（防噪声误触发）
      min_silence_ms  停顿多久算说完（抢答就调大）
      pad_ms          语音开始前保留的音频（防切掉句首）

    句首保护：silero 从说话到越过 threshold 有一段检测延迟（min_speech_ms），
    若补音窗口 pad_ms 小于它，句首会被丢。这里强制 pad_frames 至少等于
    min_speech_frames + 8（再多留 ~256ms），保证第一个字完整进 STT。
    """

    def __init__(self, threshold=0.5, min_speech_ms=500, min_silence_ms=1200,
                 pad_ms=300, sr=16000, win=512):
        self.threshold = threshold
        self.sr = sr
        self.win = win
        self.min_speech_ms = float(min_speech_ms)
        self.min_silence_ms = float(min_silence_ms)
        self.pad_ms = float(pad_ms)
        self._sync_frames()
        self.state = "silence"
        self.onset = 0
        self.silence = 0
        self.pre = []      # 最近若干窗口的音频（句首补音，容量见 _sync_frames）
        self.segment = []  # 说话段的音频窗口

    def _sync_frames(self):
        """把 ms 手感换算成帧数；min_speech 变化时也由设置面板触发重算。"""
        frame_ms = self.win / self.sr * 1000.0
        self.min_speech_frames = max(1, int(self.min_speech_ms / frame_ms))
        self.min_silence_frames = max(1, int(self.min_silence_ms / frame_ms))
        # 补音容量必须盖住检测延迟，否则句首丢失（第一个字被吃）
        self.pre_cap = max(int(self.pad_ms / frame_ms), self.min_speech_frames + 8)

    def apply(self, threshold=None, min_speech_ms=None, min_silence_ms=None, pad_ms=None):
        """前端设置面板用：热更新手感参数，不动内部状态。"""
        if threshold is not None:
            self.threshold = float(threshold)
        if min_speech_ms is not None:
            self.min_speech_ms = float(min_speech_ms)
        if min_silence_ms is not None:
            self.min_silence_ms = float(min_silence_ms)
        if pad_ms is not None:
            self.pad_ms = float(pad_ms)
        self._sync_frames()

    def reset(self):
        self.state = "silence"
        self.onset = 0
        self.silence = 0
        self.pre = []
        self.segment = []

    def feed(self, prob: float, audio: np.ndarray):
        """喂一个窗口的概率与该窗口音频，返回事件列表。

        事件格式：(kind, payload)，kind 为 'speech_started' 或 'speech_ended'。
        speech_ended 的 payload 是该段 float32 16k 音频。
        """
        self.pre.append(audio)
        if len(self.pre) > self.pre_cap:
            self.pre.pop(0)
        events = []

        if self.state == "silence":
            if prob >= self.threshold:
                self.onset += 1
                if self.onset >= self.min_speech_frames:
                    # 触发说话开始：带上句首补音（可能比 pad_ms 更长以盖住检测延迟）
                    self.state = "speech"
                    self.silence = 0
                    self.segment = list(self.pre) + [audio]
                    events.append(("speech_started", None))
            else:
                self.onset = 0
        else:  # speech
            self.segment.append(audio)
            if prob < self.threshold:
                self.silence += 1
                if self.silence >= self.min_silence_frames:
                    seg = np.concatenate(self.segment)
                    self.reset()
                    events.append(("speech_ended", seg))
            else:
                self.silence = 0
        return events
