# -*- coding: utf-8 -*-
# =============================================================================
#  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
#  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
#  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
#  SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
"""STT 语音识别 —— 可插拔接口，默认 SenseVoice（funasr）"""
import logging
import os
import re
import tempfile
import unicodedata
import wave

import numpy as np

log = logging.getLogger("hexgf.stt")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根（src/ 的上一级）


def resolve_model_path(model: str) -> str:
    """优先使用包内 models/ 下预置的本地模型，否则原样返回由 funasr 自动下载。"""
    if model.count("/") == 1 and not model.startswith("/"):
        local = os.path.join(BASE_DIR, "models", model.split("/")[-1])
        if os.path.isdir(local):
            return local
    return model


# 对话文本保留的标点（TTS 只喂文字+这些标点，其余符号/emoji 一律清掉）
_KEEP_PUNCT = set("，。！？；：、…—～“”‘’「」『』·,.;:?!'\"")


def clean_sensevoice(text: str) -> str:
    """清洗 SenseVoice 输出，只留文字+标点。

    剥掉 <|zh|> 事件标记、<\\n> 等 <...> 残留，以及 emoji/颜文字/特殊符号，
    避免喂给 TTS 时出戏。白名单：字母(含汉字/日韩) + 数字 + 常用标点，其余丢弃。
    """
    text = re.sub(r"<[^>]*>", "", text or "")
    out = []
    for ch in text:
        if ch.isspace():
            out.append(" ")
        elif unicodedata.category(ch)[0] == "L" or ch.isdigit() or ch in _KEEP_PUNCT:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


class STTBase:
    def transcribe(self, audio: np.ndarray) -> str:
        """audio: float32 16kHz 单声道 → 文本"""
        raise NotImplementedError


def _resolve_bpemodel(model_dir: str):
    """在模型目录里找 sentencepiece bpe 模型。

    funasr 构建 tokenizer 时依赖 config.yaml 的 tokenizer_conf.bpemodel，
    本地模型目录里官方文件叫 chn_jpn_yue_eng_ko_spectok.bpe.model（可能没有 bpe.model），
    不显式指过去就会 sp.load(None) 直接崩。这里按名/通配各找一次。
    """
    for name in ("bpe.model", "chn_jpn_yue_eng_ko_spectok.bpe.model"):
        p = os.path.join(model_dir, name)
        if os.path.isfile(p):
            return p
    import glob
    hits = glob.glob(os.path.join(model_dir, "*.bpe.model"))
    return hits[0] if hits else None


def _resolve_device(device: str) -> str:
    """auto → 有 GPU 用 cuda:0，没有落 cpu；显式写 cuda 但本机无 CUDA → 回退 cpu 并警告。

    让无 GPU 的机器开箱即用，不再因 config 写死 cuda:0 启动即崩。
    """
    import torch
    if device == "auto":
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    if "cuda" in str(device) and not torch.cuda.is_available():
        log.warning("[stt] 配置指定 %s 但本机无可用 CUDA，回退到 cpu", device)
        return "cpu"
    return device


class SenseVoiceSTT(STTBase):
    def __init__(self, model="iic/SenseVoiceSmall", device="auto",
                 quantize=True, language="zh", **kwargs):
        from funasr import AutoModel
        model = resolve_model_path(model)
        device = _resolve_device(device)
        self.language = language
        extra = {}
        if os.path.isdir(model):
            bpemodel = _resolve_bpemodel(model)
            if bpemodel:
                # 本地模型必须显式给 bpemodel，否则 funasr 用 config 里的 null 直接崩
                extra["tokenizer"] = "SentencepiecesTokenizer"
                extra["tokenizer_conf"] = {
                    "bpemodel": bpemodel,
                    "unk_symbol": "<unk>",
                    "split_with_space": True,
                }
        self.model = AutoModel(
            model=model,
            trust_remote_code=True,
            vad_model=None,
            device=device,
            quantize=quantize,
            disable_update=True,
            disable_pbar=True,
            **extra,
        )

    def transcribe(self, audio: np.ndarray) -> str:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            pcm = np.clip(audio, -1.0, 1.0)
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes((pcm * 32767).astype("<i2").tobytes())
            res = self.model.generate(
                input=path,
                cache={},
                language=self.language,
                use_itn=True,
                batch_size_s=60,
                merge_vad=False,
                merge_length_s=15,
            )
            text = res[0]["text"] if res else ""
            return clean_sensevoice(text)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


class TestSTT(STTBase):
    """联调用：不依赖模型，固定返回一句话，验证整条链路"""

    def __init__(self, text="你好呀", **kwargs):
        self.text = text

    def transcribe(self, audio: np.ndarray) -> str:
        return self.text


def build_stt(cfg: dict) -> STTBase:
    c = dict(cfg.get("stt", {}))
    engine = c.pop("engine", "sensevoice")
    if engine == "sensevoice":
        return SenseVoiceSTT(**c)
    if engine == "test":
        return TestSTT(**c)
    raise ValueError(f"未知 STT 引擎: {engine}")
