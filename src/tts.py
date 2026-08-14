# -*- coding: utf-8 -*-
# =============================================================================
#  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
#  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
#  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
#  SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
# TTS 语音合成：可插拔接口，默认 CosyVoice2-0.5B（funasr 生态）
import logging
import sys

import numpy as np

from audio_utils import numpy_to_wav_bytes, resample_np

log = logging.getLogger("hexgf.tts")


class TTSBase:
    def synthesize(self, text, on_chunk=None):
        raise NotImplementedError

    def warmup(self):
        """预热：跑一次真实合成初始化 CUDA 内核。默认空实现。"""
        pass


class CosyVoice2TTS(TTSBase):
    def __init__(self, model_dir, ref_wav=None, ref_text="", device="cuda:0",
                 speed=1.0, chunk_ms=0, cosyvoice_repo=None, instruct="",
                 speaker="中文女", **kwargs):
        if cosyvoice_repo:
            sys.path.insert(0, cosyvoice_repo)
        from cosyvoice.cli.cosyvoice import CosyVoice2
        self.model = CosyVoice2(model_dir, load_jit=False, load_trt=False, fp16=False)
        self.speed = float(speed)
        self.chunk_ms = int(chunk_ms or 0)
        self.instruct = instruct or None
        self.speaker = speaker
        self.prompt_text = ref_text or ""
        # 传文件路径而非张量：inference_zero_shot/instruct2 内部会自己 load_wav 到 24k
        self.ref_wav = ref_wav or ""

    def _reset_stream_hop(self):
        """上游 Bug：CosyVoice2 流式循环里 token_hop_len 25→50→100 递增后不复位，
        导致第二次及以后的合成首块≈整句，表现为"非流式"。每次合成前复位。
        CosyVoice2 包装对象的 .model 才是 CosyVoice2Model；找不到就跳过。"""
        try:
            m = getattr(self.model, "model", None)
            if m is not None and getattr(m, "token_hop_len", None) is not None:
                m.token_hop_len = 25
        except Exception:
            pass

    @staticmethod
    def _iter_wav(gen):
        """CosyVoice generator -> 24k mono numpy (skip empty)."""
        for chunk in gen:
            tts_speech = chunk.get("tts_speech")
            if tts_speech is None:
                continue
            wav = tts_speech.squeeze(0).float().cpu().numpy()
            if wav.size == 0:
                continue
            yield wav

    def _emit(self, wav, on_chunk):
        """24k numpy -> 16k WAV bytes -> callback."""
        if wav.size == 0 or on_chunk is None:
            return
        on_chunk(numpy_to_wav_bytes(resample_np(wav, 24000, 16000), 16000))

    def _cached_prompt_input(self):
        """一次性缓存 zero-shot prompt 侧特征（ref.wav → 文本 token/语音 token/语音特征/说话人嵌入），
        模拟 frontend_zero_shot 的 CosyVoice2 截断规则（sample_rate==24000）。每句增量合成时复用，
        不再重编码 ref.wav —— 逐句重编码是逐句 tts() 1.5s 级句间缝隙的根源。"""
        front = self.model.frontend
        norm_ref = front.text_normalize(self.prompt_text, split=False, text_frontend=True)
        prompt_text, _ = front._extract_text_token(norm_ref)
        speech_feat, _ = front._extract_speech_feat(self.ref_wav)
        speech_token, _ = front._extract_speech_token(self.ref_wav)
        if self.model.sample_rate == 24000:
            # CosyVoice2：强制 speech_feat % speech_token = 2
            token_len = min(int(speech_feat.shape[1] / 2), speech_token.shape[1])
            speech_feat = speech_feat[:, :2 * token_len]
            speech_token = speech_token[:, :token_len]
        embedding = front._extract_spk_embedding(self.ref_wav)
        return {
            "prompt_text": prompt_text,
            "llm_prompt_speech_token": speech_token,
            "flow_prompt_speech_token": speech_token,
            "prompt_speech_feat": speech_feat,
            "llm_embedding": embedding,
            "flow_embedding": embedding,
        }

    @staticmethod
    def _materialize_text(text_iter):
        """把句子迭代器一次性攒成整段文本（失败回退用；同时消费空/None 哨兵）。"""
        parts = []
        try:
            for text in text_iter:
                if text:
                    parts.append(str(text).strip())
        except Exception:
            pass
        return "".join(parts)

    def _next_sentence(self, text_iter, front):
        """从迭代器取下一句并转成 (1,N) 文本 token；无更多句子返回 None。
        迭代器在 LLM 推理线程内被拉取，本方法绝不向外抛异常（防推理线程死等）。"""
        while True:
            try:
                text = next(text_iter)
            except StopIteration:
                return None
            except Exception:
                log.warning("[tts] stream text_iter raised, stop", exc_info=True)
                return None
            if text is None:
                return None
            text = str(text).strip()
            if not text:
                continue
            try:
                norm = front.text_normalize(text, split=False, text_frontend=True)
                if not norm:
                    continue
                token, _ = front._extract_text_token(norm)
            except Exception:
                log.warning("[tts] stream sentence failed, skip", exc_info=True)
                continue
            if token is None or token.numel() == 0:
                continue
            return token

    def synthesize_streaming(self, text_iter, on_chunk=None):
        """边喂文本边出音频：句子迭代器逐句喂入 model.tts(stream=True)，底层
        Qwen2LM.inference_bistream 增量解码（模型要文本时才拉下一句），音频连续产出。
        - prompt 特征只算一次（_cached_prompt_input），句间无 1.5s 级缝隙。
        - 首句先预拉：空回复直接跳过，避免空生成器触发 CosyVoice 读出 prompt 文本。
        - 失败回退：_materialize_text 攒剩余句子整段合成，保可用性。"""
        front = self.model.frontend
        try:
            prompt_input = self._cached_prompt_input()
        except Exception:
            log.warning("[tts] prompt cache failed, fallback whole-reply", exc_info=True)
            rest = self._materialize_text(text_iter)
            if rest:
                self.synthesize(rest, on_chunk=on_chunk)
            return
        first = self._next_sentence(text_iter, front)
        if first is None:
            return  # 空回复：不合成，避免空生成器读出 prompt 文本

        def text_gen():
            yield first
            while True:
                nxt = self._next_sentence(text_iter, front)
                if nxt is None:
                    return
                yield nxt

        self._reset_stream_hop()
        try:
            gen = self.model.model.tts(text=text_gen(), **prompt_input, stream=True, speed=self.speed)
            for chunk in gen:
                tts_speech = chunk.get("tts_speech")
                if tts_speech is None:
                    continue
                wav = tts_speech.squeeze(0).float().cpu().numpy()
                if wav.size == 0:
                    continue
                if on_chunk:
                    on_chunk(numpy_to_wav_bytes(resample_np(wav, 24000, 16000), 16000))
        except Exception:
            log.warning("[tts] incremental stream failed, fallback whole-reply", exc_info=True)
            rest = self._materialize_text(text_iter)
            if rest:
                self.synthesize(rest, on_chunk=on_chunk)

    def synthesize(self, text, on_chunk=None):
        text = str(text or "").strip()
        if not text:
            return
        self._reset_stream_hop()
        # 整段一次连续流式
        front = self.model.frontend
        try:
            norm = front.text_normalize(text, split=False, text_frontend=True)
            norm_ref = front.text_normalize(self.prompt_text, split=False, text_frontend=True)
            gen = self.model.model.tts(**front.frontend_zero_shot(norm, norm_ref, self.ref_wav, self.model.sample_rate, ''), stream=True, speed=self.speed)
        except Exception:
            log.warning("[tts] direct stream failed", exc_info=True)
            if self.instruct and self.ref_wav:
                gen = self.model.inference_instruct2(
                    text, self.instruct, self.ref_wav, stream=True, speed=self.speed)
            elif self.ref_wav and self.prompt_text:
                gen = self.model.inference_zero_shot(
                    text, self.prompt_text, self.ref_wav, stream=True, speed=self.speed)
            else:
                gen = self.model.inference_sft(text, self.speaker, stream=True, speed=self.speed)
    
        acc = []
        for chunk in gen:
            tts_speech = chunk.get("tts_speech")
            if tts_speech is None:
                continue
            wav = tts_speech.squeeze(0).float().cpu().numpy()
            if wav.size == 0:
                continue
            if self.chunk_ms > 0:
                b = numpy_to_wav_bytes(resample_np(wav, 24000, 16000), 16000)
                if on_chunk:
                    on_chunk(b)
            else:
                acc.append(wav)
        if self.chunk_ms <= 0 and acc and on_chunk:
            full = np.concatenate(acc)
            on_chunk(numpy_to_wav_bytes(resample_np(full, 24000, 16000), 16000))

    def warmup(self):
        """预热：跑一次整段合成（synthesize）初始化 CUDA 内核，与正式回复同一代码路径，
        首轮 TTFA 不再冷启动。静默合成，不产出音频。失败不影响后续。"""
        try:
            self.synthesize("好的，我们就这样说好了。", on_chunk=None)
        except Exception:
            log.warning("[tts] warmup failed (first reply will be slower)", exc_info=True)


class TestTTS(TTSBase):
    """无模型联调：发一段 1 秒 220Hz 正弦波 WAV"""

    def __init__(self, seconds=1.0, **kwargs):
        self.secs = float(seconds)

    def synthesize(self, text, on_chunk=None):
        sr = 16000
        t = np.linspace(0, self.secs, int(sr * self.secs), endpoint=False)
        wav = 0.2 * np.sin(2 * np.pi * 220 * t)
        if on_chunk:
            on_chunk(numpy_to_wav_bytes(wav, sr))


def build_tts(cfg):
    c = dict(cfg.get("tts", {}))
    engine = c.pop("engine", "cosyvoice")
    if engine == "cosyvoice":
        return CosyVoice2TTS(**c)
    if engine == "test":
        return TestTTS(**c)
    raise ValueError("未知 TTS 引擎: " + engine)
