# -*- coding: utf-8 -*-
# =============================================================================
#  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
#  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
#  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
#  SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
# 编排层：OpenAI Realtime 风格 WS 协议后端（本地引擎，可热插拔）
# 声音路径（generate-then-play 整段式）：LLM 全文到齐 → tts.synthesize() 整段合成
#   → 完整 WAV 一次 WS response.output_audio.delta + 一次 POST LiveTalking
#   /humanaudio（口型，视频轨静音）。chunk_ms=0 时整段攒齐后单次回调，
#   无句间缝隙、无逐块 HTTP 往返（数字人批量渲染器天然适配整段输入）。
# 线程模型：WS 循环在 event loop 线程；STT/LLM 走 asyncio.to_thread，TTS 走独立
#           daemon 线程；跨线程发 WS 统一走 _send_sync。
import asyncio
import base64
import json
import logging
import os
import queue
import threading
import time
import traceback
import uuid
from collections import deque

log = logging.getLogger("hexgf.orchestrator")

import numpy as np

from audio_utils import pcm16_to_float32, wav_to_pcm16_bytes
from vad import SileroVAD, EndpointDetector
from stt import build_stt
from llm import build_llm
from tts import build_tts 
from avatar_client import AvatarClient

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根（src/ 的上一级）

# 数字人出声窗口余量（ms）：覆盖 LiveTalking 管线延迟 + 输出缓冲尾音。用于判定
# 「数字人正在出声」→ 用户开口即打断（即使整段式下 busy 早已释放）。宁长勿短：
# 仅在窗口内多调一次 /interrupt_talk（冲空队列，无害），窗口太短才会漏打断。
_AVATAR_TAIL_MS = 800.0


class _AbortError(Exception):
    """回复被用户打断：TTS/LLM 线程收到后尽快收尾。"""


class HexOrchestrator:
    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._stt = None
        self._llm = None
        self._tts = None

        vc = cfg.get("vad", {})
        sr = int(vc.get("sample_rate", 16000))
        self.sr = sr
        self.frame_ms = 512.0 / sr * 1000.0
        self.vad = SileroVAD(device=vc.get("device", "cpu"), sample_rate=sr)
        self.detector = EndpointDetector(
            threshold=float(vc.get("threshold", 0.5)),
            min_speech_ms=int(vc.get("min_speech_ms", 500)),
            min_silence_ms=int(vc.get("min_silence_ms", 1200)),
            pad_ms=int(vc.get("pad_ms", 300)),
            sr=sr,
        )
        self.min_turn_secs = float(vc.get("min_turn_secs", 0.2))

        ac = cfg.get("avatar", {})
        self.avatar = AvatarClient(ac.get("base_url", "http://127.0.0.1:8010"))
        # 数字人开口后短暂屏蔽 VAD（防回声自触发；窗口起点=音频推给 LiveTalking 时刻）
        self._ignore_self_ms = float(ac.get("ignore_self_ms", 600))

        self.persona = self._load_persona()
        self.greeting = str(cfg.get("server", {}).get("greeting", "") or "").strip()
        self._mem_turns = int(cfg.get("memory", {}).get("turns", 4))

        self.reset_session()
        self._warmup()

    # ------------------------------------------------------------ 人设

    def _load_persona(self) -> str:
        pc = self.cfg.get("persona", {})
        text = ""
        path = os.path.join(BASE_DIR, str(pc.get("file", "persona.md")))
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read().strip()
        except OSError:
            pass
        if not text:
            text = "你是她的女朋友，请用口语、简短、有情绪的中文回答。"
        if pc.get("no_think"):
            text += "\n\n/no_think"
        return text

    def _effective_persona(self) -> str:
        return self._persona_override or self.persona

    def _ensure(self, kind):
        with self._lock:
            if kind == "stt":
                if self._stt is None:
                    self._stt = build_stt(self.cfg)
                return self._stt
            if kind == "llm":
                if self._llm is None:
                    self._llm = build_llm(self.cfg)
                return self._llm
            if kind == "tts":
                if self._tts is None:
                    self._tts = build_tts(self.cfg)
                return self._tts
            raise KeyError(kind)

    def _warmup(self):
        """后台预加载三个引擎，避免首轮冷启动慢。"""

        def build():
            try:
                self._ensure("stt")
                self._ensure("llm")
                tts = self._ensure("tts")
                if hasattr(tts, "warmup"):
                    tts.warmup()
            except Exception:
                traceback.print_exc()

        threading.Thread(target=build, name="warmup", daemon=True).start()

    # ------------------------------------------------------------ 会话状态

    def reset_session(self):
        self.ws = None
        self.loop = None
        # sessionid 保持粘性，不随 WS 重连清空：avatar 在同一页面跨 WS 重连时
        # WebRTC 仍是同一个 LiveTalking 会话，旧 sessionid 继续有效。否则每次
        # 「重新开始对话」都清空 → 新 sessionid 到达前所有 TTS 分块被丢弃（无口型）。
        # 前端每次连上都会重发 sessionid 来刷新它；连接失败清成 None 由浏览器处理。
        self.sessionid = getattr(self, "sessionid", None)
        self.busy = False
        self.abort = False
        self._session_id = "sess_" + uuid.uuid4().hex[:8]
        self._greeted = False
        self._persona_override = None
        self._active_response_id = None
        self._current_item_id = None
        self._active_item_id = None
        self._turn_seg = None
        self._turn_pending = False
        self._vad_buf = np.zeros(0, dtype=np.float32)
        self._audio_clock_ms = 0.0
        self._reply_text = {}
        self._vad_ignore = False
        self._ignore_vad_until_ms = 0.0
        self._avatar_playing_until_ms = 0.0
        self._history = deque(maxlen=max(2, self._mem_turns * 2))
        self.detector.reset()
        # 数字人消费队列：换会话换新队列+线程，旧线程以 None 哨兵退出
        old_q = self._avatar_q if hasattr(self, "_avatar_q") else None
        if old_q is not None:
            old_q.put(None)
        self._avatar_q = queue.Queue(maxsize=64)
        self._avatar_ready = threading.Event()
        t = threading.Thread(target=self._avatar_loop, name="avatar-q", daemon=True)
        self._avatar_worker = t
        t.start()

    def _avatar_loop(self):
        """消费完整回复 WAV → LiveTalking /humanaudio（口型）。视频轨静音，音频不走这里。"""
        self._avatar_ready.wait(timeout=10.0)
        while True:
            wav = self._avatar_q.get()
            if wav is None:
                return
            sid = self.sessionid
            if not sid:
                continue
            try:
                self.avatar.human_audio(wav, sid)
            except Exception:
                traceback.print_exc()

    # ------------------------------------------------------------ 收发

    async def _send(self, obj):
        ws = self.ws
        if ws is None:
            return
        try:
            await ws.send_text(json.dumps(obj))
        except Exception:
            pass

    def _send_sync(self, obj):
        """跨线程发 WS：loop 线程内只调度不等待；worker 线程等 3s 让消息落盘。"""
        loop = self.loop
        if loop is None or not loop.is_running():
            return
        fut = asyncio.run_coroutine_threadsafe(self._send(obj), loop)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 不在 loop 线程：等一小段，保证顺序 + 暴露异常
            try:
                fut.result(3.0)
            except Exception:
                pass

    async def run_ws(self, ws):
        self.reset_session()
        self.ws = ws
        self.loop = asyncio.get_running_loop()
        await self._send({"type": "session.created", "session": {"id": self._session_id}})
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                if isinstance(event, dict):
                    self._handle_event(event)
        except Exception:
            pass
        finally:
            self.abort = True

    def _handle_event(self, event):
        t = event.get("type")
        if t == "session.update":
            s = event.get("session")
            if isinstance(s, dict):
                inst = s.get("instructions")
                if isinstance(inst, str) and inst.strip():
                    self._persona_override = inst.strip()
            self._send_sync({"type": "session.updated", "session": {"id": self._session_id}})
            self._maybe_greet()
        elif t == "input_audio_buffer.append":
            audio = event.get("audio")
            if audio:
                self.feed_audio(audio)
        elif t in ("input_audio_buffer.clear", "response.cancel"):
            self.abort = True
        elif t == "sessionid":
            sid = event.get("sessionid")
            if sid:
                self.sessionid = sid
                self._avatar_ready.set()
                log.info("[hexgf] sessionid set: %s", sid)
        elif t == "sessionid.request":
            # 后端无 sessionid（或想确认时）→ 请浏览器回传当前 avatar sessionid
            sid = self.sessionid
            if sid:
                self._avatar_ready.set()
                self._send_sync({"type": "sessionid", "sessionid": sid})

    # ------------------------------------------------------------ VAD / 音频

    def feed_audio(self, b64):
        try:
            data = base64.b64decode(b64)
        except Exception:
            return
        audio = pcm16_to_float32(data)
        buf = np.concatenate([self._vad_buf, audio])
        win = 512
        n = len(buf)
        n_win = n // win
        for i in range(0, n_win * win, win):
            frame = buf[i:i + win]
            prob = self.vad.speech_prob(frame)
            self._audio_clock_ms += self.frame_ms
            for kind, payload in self.detector.feed(prob, frame):
                if kind == "speech_started":
                    self._on_speech_started()
                elif kind == "speech_ended":
                    self._on_speech_ended(payload)
        self._vad_buf = buf[n_win * win:]  # 不足一窗的尾部留给下一条消息

    def _on_speech_started(self):
        if self._audio_clock_ms < self._ignore_vad_until_ms:
            # 数字人刚开口 / 上段语音刚落音的余音回声：整段忽略，防自触发
            self._vad_ignore = True
            return
        self._current_item_id = "msg_" + uuid.uuid4().hex
        self.abort = True  # 打断进行中的回复
        self._ignore_vad_until_ms = self._audio_clock_ms + self._ignore_self_ms
        sid = self.sessionid
        if sid and (self.busy or self._audio_clock_ms < self._avatar_playing_until_ms):
            # 回复生成中 或 数字人正在出声（整段式下音频已推给 LiveTalking、busy 早已释放）
            # → 立刻让 LiveTalking 停口型/停声，别让上一句盖住用户的插话
            threading.Thread(target=lambda: self.avatar.interrupt(sid), daemon=True).start()

        audio_start_ms = max(0, self._audio_clock_ms - len(self.detector.pre) * self.frame_ms)
        self._send_sync({
            "type": "input_audio_buffer.speech_started",
            "item_id": self._current_item_id,
            "audio_start_ms": round(audio_start_ms),
        })

    def _on_speech_ended(self, seg):
        if self._vad_ignore:
            self._vad_ignore = False
            return
        if len(seg) / self.sr < self.min_turn_secs:
            return  # 太短，当成噪声/误触发
        self._send_sync({
            "type": "input_audio_buffer.speech_stopped",
            "item_id": self._current_item_id,
            "audio_end_ms": round(self._audio_clock_ms),
        })
        self._turn_seg = seg
        self._turn_pending = True
        self.loop.create_task(self._drain_turn())

    # ===== turn pipeline =====

    async def _drain_turn(self):
        """单回合管线：busy 串行化。上一回合没收尾就等它退（用户抢话不能丢）。"""
        if not self._turn_pending:
            return
        for _ in range(200):  # ≤10s 等上一回合收尾
            if not self.busy:
                break
            await asyncio.sleep(0.05)
        else:
            self._turn_pending = False
            return  # 上一回合卡死，丢本轮
        if not self._turn_pending:
            return
        self._turn_pending = False
        self.abort = False
        self.busy = True
        try:
            seg = self._turn_seg
            self._turn_seg = None
            text = await asyncio.to_thread(self._stt_transcribe, seg)
            # 识别结果进日志（./start-all.sh logs 跟随编排层日志时可见 STT）
            log.info("[hexgf] stt: %s", text or "(空)")
            if self.abort or not text:
                return
            self._push_history("user", text)
            item = {"id": self._current_item_id, "type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": text}]}
            self._send_sync({"type": "conversation.item.created", "item": item})
            self._send_sync({
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": self._current_item_id,
                "transcript": text,
            })
            await self._run_reply()
        finally:
            self.busy = False

    # ===== reply pipeline =====

    async def _run_reply(self, messages=None):
        """驱动一次完整回复：LLM 流式收全文 → TTS 整段合成 → WS 音频 + 口型 → 收尾事件。"""
        self.abort = False
        self._reply_text = {}
        self._sent_transcript = ""  # 字幕增量进度：_release_transcript 按音频时间轴推进
        self._active_response_id = "resp_" + uuid.uuid4().hex
        self._active_item_id = "msg_" + uuid.uuid4().hex
        self._tts_q = queue.Queue(maxsize=32)
        self._tts_done = threading.Event()
        threading.Thread(target=self._tts_worker, name="tts-q", daemon=True).start()

        self._send_sync({"type": "response.created", "response": {"id": self._active_response_id}})
        self._send_sync({
            "type": "response.output_item.added",
            "item": {
                "id": self._active_item_id,
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
                "content": [{"type": "output_audio", "transcript": ""}],
            },
        })
        self._send_sync({
            "type": "response.output_audio_transcript.delta",
            "response_id": self._active_response_id,
            "delta": "",
        })

        try:
            if messages is None:
                messages = self._build_messages()
            await asyncio.to_thread(self._stream_llm_tts, messages)
        finally:
            if not self.abort:
                self._push_history("assistant", self._reply_text.get(self._active_item_id, ""))
            await self._finish_turn()

    def _stream_llm_tts(self, messages):
        """worker 线程：跑 LLM 流式生成，token 只累积（_on_llm_token）。
        整段式：LLM 收尾后把全文一次性入 TTS 队列，TTS 整段合成。"""
        text = None
        try:
            llm = self._ensure("llm")
            text = llm.generate(messages, on_token=self._on_llm_token)
        except _AbortError:
            text = None
        except Exception:
            traceback.print_exc()
            text = None
        finally:
            # 以返回值为准回填（个别引擎 on_token 未覆盖全部 token 时兜底）
            if text:
                self._reply_text[self._active_item_id] = text
            full = "" if self.abort else (text or self._reply_text.get(self._active_item_id, ""))
            try:
                # 整段全文一次入队；空回复/中止放 None 唤醒 worker 收尾
                self._tts_q.put(full or None)
            except Exception:
                pass
        return text

    def _on_llm_token(self, token):
        """LLM 回调（worker 线程）：只累积全文，不再逐句切分入 TTS 队列。
        整段式：等 LLM 收尾后一次性整段合成（_stream_llm_tts finally 入队）。"""
        if self.abort:
            raise _AbortError()
        self._reply_text[self._active_item_id] = (
            self._reply_text.get(self._active_item_id, "") + token)

    def _release_transcript(self, transcript, rid):
        """释放字幕增量：transcript 为目标全文，delta = 自上次释放以来的新增文本。
        整段式下整段调用一次即发全文（幂等，_sent_transcript 保证只发一次）。"""
        if self.abort or rid != self._active_response_id:
            return
        prev = self._sent_transcript or ""
        if transcript and transcript.startswith(prev) and len(transcript) > len(prev):
            delta = transcript[len(prev):]
            self._sent_transcript = transcript
            log.info("[tts-q] subtitle +%r", delta)
            self._send_sync({
                "type": "response.output_audio_transcript.delta",
                "response_id": rid,
                "delta": delta,
            })

    def _tts_worker(self):
        """TTS 消费线程（generate-then-play 整段式）：局部捕获 q/rid，避免回合切换
        被替换。等 LLM 全文到齐 → tts.synthesize() 整段合成；chunk_ms=0 时整段攒齐后
        单次 on_chunk → 完整 WAV 一次 WS delta + 一次 POST /humanaudio。
        无句间缝隙、无逐块 HTTP 往返；keep-frame 仍作 WebRTC 空队列安全网。"""
        q = self._tts_q
        rid = self._active_response_id
        try:
            tts = self._ensure("tts")
        except Exception:
            traceback.print_exc()
            self._tts_done.set()
            return
        text = q.get()  # 整段全文；None = 空回复/中止
        if not text or self.abort or rid != self._active_response_id:
            self._tts_done.set()
            return
        try:
            tts.synthesize(text, on_chunk=lambda wav: self._on_tts_chunk(wav, rid))
        except Exception:
            traceback.print_exc()
        finally:
            self._tts_done.set()

    def _on_tts_chunk(self, wav, rid):
        if self.abort or rid != self._active_response_id:
            return
        try:
            pcm = wav_to_pcm16_bytes(wav)
        except Exception:
            return
        # 数字人即将出声：记录出声窗口（打断判定用）+ 短暂屏蔽 VAD（防回声自触发）。
        # len(pcm)/32 = 毫秒（16kHz×2B = 32B/ms）；余量覆盖 LiveTalking 管线延迟+输出缓冲尾音
        self._avatar_playing_until_ms = self._audio_clock_ms + len(pcm) / 32.0 + _AVATAR_TAIL_MS
        self._ignore_vad_until_ms = self._audio_clock_ms + self._ignore_self_ms
        # 整段式：音频就绪时一次性释放全字幕（_release_transcript 幂等，整段只发一次）
        self._release_transcript(self._reply_text.get(self._active_item_id, ""), rid)
        log.info("[tts-q] reply audio %.2fs (%d bytes) -> ws+avatar", len(pcm) / 32000.0, len(pcm))
        self._send_sync({
            "type": "response.output_audio.delta",
            "response_id": rid,
            "delta": base64.b64encode(pcm).decode("ascii"),
        })
        sid = self.sessionid
        if sid:
            try:
                self._avatar_q.put_nowait(wav)
            except queue.Full:
                pass
        else:
            log.info("[hexgf] tts chunk dropped: no sessionid")
            self._request_sessionid()

    def _request_sessionid(self):
        """无 sessionid 时让浏览器回传当前 avatar sessionid（节流，≤1 次/秒）。
        浏览器可能已连好 avatar（页面加载即连）只是没把 sessionid 发过来。"""
        now = time.monotonic()
        last = getattr(self, "_last_sid_req", 0.0)
        if now - last < 1.0:
            return
        self._last_sid_req = now
        self._send_sync({"type": "sessionid.request"})

    async def _finish_turn(self):
        """等 TTS 线程放完本回合音频，发收尾事件（output_audio.done / response.done）。"""
        wait_s = 2.0 if self.abort else 30.0
        await asyncio.to_thread(self._tts_done.wait, wait_s)
        self._tts_q = None
        rid = self._active_response_id
        transcript = self._reply_text.get(self._active_item_id, "")
        status = "cancelled" if self.abort else "completed"
        self._send_sync({"type": "response.output_audio.done", "response_id": rid})
        self._send_sync({
            "type": "response.done",
            "response": {
                "id": rid,
                "status": status,
                "output": [{
                    "id": self._active_item_id,
                    "type": "message",
                    "role": "assistant",
                    "status": status,
                    "content": [{"type": "output_audio", "transcript": transcript}],
                }],
            },
        })

    # ===== 问候 / 记忆 / STT =====

    def _maybe_greet(self):
        if self._greeted or not self.greeting:
            return
        self._greeted = True
        if self.loop is None:
            return
        self.loop.create_task(self._greet_async())

    async def _greet_async(self):
        if self.busy:
            return
        # 等 sessionid（最多 4s）再开口：开场白也要有口型。前端每次连上都会回传，
        # 同页重连是瞬时到达，新开页等 offer 完成；没等到则照常说话。
        if not self.sessionid:
            await asyncio.to_thread(self._avatar_ready.wait, 4.0)
            if not self.sessionid:
                log.warning("[hexgf] greet timeout: no sessionid after 4s — greeting without lip sync")
        self.busy = True
        try:
            self.abort = False
            await self._run_reply(self._greeting_messages())
        finally:
            self.busy = False

    def _greeting_messages(self):
        return [
            {"role": "system", "content": self._effective_persona()},
            {"role": "user", "content": self.greeting},
        ]

    def _build_messages(self):
        msgs = [{"role": "system", "content": self._effective_persona()}]
        for role, content in self._history:
            msgs.append({"role": role, "content": content})
        return msgs

    def _push_history(self, role, content):
        content = str(content or "").strip()
        if content:
            self._history.append((role, content))

    def _stt_transcribe(self, seg):
        try:
            stt = self._ensure("stt")
            return stt.transcribe(seg)
        except Exception:
            traceback.print_exc()
            return ""
