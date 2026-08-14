// @ts-check
/* ============================================================================
 * Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
 * Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
 * YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
 * SPDX-License-Identifier: GPL-3.0-or-later
 * ========================================================================== */
/**
 * AudioWorkletProcessor that plays back Float32 mono samples received from
 * the main thread, upsampling whatever incoming rate the server uses
 * (typically 24 kHz PCM16) to the AudioContext rate (typically 48 kHz).
 *
 * Lifecycle / messaging:
 *
 *   main -> worklet:
 *     { kind: "config", inputRate: 24000, delayMs: 600 }  one-shot at startup
 *     { kind: "audio", samples: Float32Array }            (transferable) per chunk
 *     { kind: "clear" }                                 wipe queue (barge-in)
 *
 *   worklet -> main:
 *     { kind: "stats", queuedMs, played }               every ~250 ms
 *     { kind: "underrun" }                              every time the queue
 *                                                      runs dry mid-playback
 *
 * Underrun strategy: output silence. We do NOT hold the last sample (that
 * tends to produce audible clicks/buzzes when long gaps appear between
 * TTS chunks). A short ramp-out + ramp-in at boundaries would be nicer but
 * the server's 30 ms cadence makes underruns visible only at end of turn.
 *
 * Recovery: after an underrun we enter `_stalled`; the next arriving audio
 * resumes playback immediately (fade-in), instead of re-requiring the full
 * delayMs buffer — the old rule froze playback after every underrun.
 */

const STATS_INTERVAL_FRAMES = 12000;
const FADE_FRAMES = 32;

class AudioPlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._inputRate = 24000;
    this._stepRatio = this._inputRate / sampleRate;
    // 口型同步延迟：缓冲 delaySamples 后再开播，抵消 Wav2Lip 视频管线滞后
    this._delaySamples = 0;
    this._queue = [];
    this._readIdx = 0;
    this._fracPos = 0;
    this._playing = false;
    this._stalled = false; // underrun 后的停滞态：来数据立即恢复，不再要求攒满 delayMs
    this._framesSinceStats = 0;
    this._totalPlayed = 0;
    this._fadeIn = 0;
    this._fadeOut = 0;
    this._lastSample = 0;

    this.port.onmessage = (e) => {
      const data = e.data;
      if (!data || typeof data !== "object") return;
      switch (data.kind) {
        case "config":
          if (typeof data.inputRate === "number" && data.inputRate > 0) {
            this._inputRate = data.inputRate;
            this._stepRatio = this._inputRate / sampleRate;
          }
          if (typeof data.delayMs === "number" && data.delayMs >= 0) {
            this._delaySamples = Math.round((data.delayMs / 1000) * this._inputRate);
            // 延迟已调小且尚未开播：直接追上当前缓冲（停滞态同样立即恢复）
            if (!this._playing && (this._stalled || this._queuedSamples() >= this._delaySamples)) {
              this._playing = true;
              this._fadeIn = FADE_FRAMES;
              this._fadeOut = 0;
            }
          }
          break;
        case "audio":
          if (data.samples instanceof Float32Array && data.samples.length > 0) {
            this._queue.push(data.samples);
            // 停滞态：收到任意新数据立即恢复（fade-in 防爆音），不再攒满 delayMs
            if (!this._playing && (this._stalled || this._queuedSamples() >= this._delaySamples)) {
              this._playing = true;
              this._fadeIn = FADE_FRAMES;
              this._fadeOut = 0;
            }
          }
          break;
        case "clear":
          this._queue.length = 0;
          this._readIdx = 0;
          this._fracPos = 0;
          this._stalled = false;
          this._fadeOut = FADE_FRAMES;
          break;
      }
    };
  }

  _queuedSamples() {
    let total = -this._readIdx;
    for (const buf of this._queue) total += buf.length;
    return Math.max(0, total);
  }

  /** Linear-interp read at the current fractional position. */
  _readInterpolated() {
    if (this._queue.length === 0) return null;
    const head = this._queue[0];
    const idx = this._readIdx;
    const frac = this._fracPos;

    let a = head[idx];
    let b;
    if (idx + 1 < head.length) {
      b = head[idx + 1];
    } else if (this._queue.length > 1) {
      b = this._queue[1][0];
    } else {
      b = a;
    }
    return a + (b - a) * frac;
  }

  /** Advance the read position by `stepRatio`; pop consumed buffers. */
  _advance() {
    this._fracPos += this._stepRatio;
    while (this._fracPos >= 1) {
      this._fracPos -= 1;
      this._readIdx += 1;
    }
    while (this._queue.length > 0 && this._readIdx >= this._queue[0].length) {
      this._readIdx -= this._queue[0].length;
      this._queue.shift();
    }
  }

  process(_, outputs) {
    const channels = outputs[0];
    if (!channels || channels.length === 0) return true;
    const out = channels[0];
    const stereo = channels.length > 1 ? channels[1] : null;

    for (let i = 0; i < out.length; i++) {
      let sample = 0;

      if (this._playing) {
        const v = this._readInterpolated();
        if (v === null) {
          // Underrun: try to ramp out cleanly to avoid clicks.
          sample = this._lastSample * Math.max(0, 1 - 1 / FADE_FRAMES);
          this._lastSample = sample;
          if (Math.abs(sample) < 1e-4) {
            this._playing = false;
            this._lastSample = 0;
            this._stalled = true; // 停滞：来数据即恢复，避免每次 underrun 硬冻结
            this.port.postMessage({ kind: "underrun" });
          }
        } else {
          sample = v;
          this._lastSample = v;
          this._advance();
        }

        if (this._fadeIn > 0) {
          const gain = 1 - this._fadeIn / FADE_FRAMES;
          sample *= gain;
          this._fadeIn -= 1;
        }
        if (this._fadeOut > 0) {
          const gain = this._fadeOut / FADE_FRAMES;
          sample *= gain;
          this._fadeOut -= 1;
          if (this._fadeOut === 0) {
            this._playing = false;
            this._lastSample = 0;
          }
        }

        this._totalPlayed += 1;
      }

      out[i] = sample;
      if (stereo) stereo[i] = sample;
    }

    this._framesSinceStats += out.length;
    if (this._framesSinceStats >= STATS_INTERVAL_FRAMES) {
      this._framesSinceStats = 0;
      const queuedSamples = this._queuedSamples();
      const queuedMs = (queuedSamples / this._inputRate) * 1000;
      this.port.postMessage({ kind: "stats", queuedMs, played: this._totalPlayed });
    }

    return true;
  }
}

registerProcessor("audio-playback", AudioPlaybackProcessor);
