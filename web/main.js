/* ============================================================================
 * Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
 * Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
 * YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
 * SPDX-License-Identifier: GPL-3.0-or-later
 * ========================================================================== */
/* ============================================================================
 * main.js — AI 赛博女友 前端入口（复用 hf-realtime-voice 的 orb UI + 音频管线）
 *
 * 连接：同源 ws(s)://<host>/v1/realtime，OpenAI Realtime 风格协议。
 * 采集：AudioWorklet 采集 → WS → 后端(STT/LLM/TTS)。
 * 声音（沿用旧方案的口型同步思路）：后端把每个 TTS 分块喂给 LiveTalking
 *       /humanaudio → Wav2Lip 合成口型 → 声音+画面经【同一条 WebRTC 流】回传
 *       （digital-avatar.js），RTP 时间戳天然同轨对齐；response.output_audio.delta
 *       照常接收但仅用于转写/状态，不再直放，避免与口型不同步。
 * 浏览器把 avatar 的 LiveTalking sessionid 通过控制消息回传后端。
 * ========================================================================== */
import { S2sWsRealtimeClient } from "./ws/s2s-ws-client.js";
import { $, truncateError } from "./ui/dom.js";
import { ChatView } from "./ui/chat.js";

const DEFAULT_INSTRUCTIONS =
  "You are a friendly voice assistant. Keep replies short, warm, and spoken.";

const STORAGE = {
  instructions: "hexgf.instructions",
  noiseGate: "hexgf.noiseGate",
  audioInputId: "hexgf.audio.inputId",
  audioOutputId: "hexgf.audio.outputId",
  subtitles: "hexgf.subtitles",
  avatarId: "hexgf.avatarId",
};

/* ── 噪音门限（与 hf 相同的 dB 轴：最左=关） ─────────────────────────────── */
const GATE_OFF_DB = -66;
const GATE_MAX_DB = -3;
const GATE_DEFAULT_DB = -50;

function gateParams(db) {
  return { enabled: db > GATE_OFF_DB, thresholdDb: db };
}
function loadGateThreshold() {
  const stored = localStorage.getItem(STORAGE.noiseGate);
  if (stored === null || stored === "") return GATE_DEFAULT_DB;
  const raw = Number(stored);
  if (!Number.isFinite(raw)) return GATE_DEFAULT_DB;
  return Math.min(GATE_MAX_DB, Math.max(GATE_OFF_DB, Math.round(raw)));
}
function clampDb(db) {
  return Math.min(GATE_MAX_DB, Math.max(GATE_OFF_DB, Math.round(db)));
}

function loadSettings() {
  return {
    instructions: localStorage.getItem(STORAGE.instructions) || DEFAULT_INSTRUCTIONS,
    noiseGate: loadGateThreshold(),
    audioInputId: localStorage.getItem(STORAGE.audioInputId) || "",
    audioOutputId: localStorage.getItem(STORAGE.audioOutputId) || "",
    subtitles: localStorage.getItem(STORAGE.subtitles) !== "0",
    avatarId: localStorage.getItem(STORAGE.avatarId) || "",
  };
}
function saveSettings(s) {
  localStorage.setItem(STORAGE.instructions, s.instructions);
  localStorage.setItem(STORAGE.noiseGate, String(s.noiseGate));
  localStorage.setItem(STORAGE.audioInputId, s.audioInputId || "");
  localStorage.setItem(STORAGE.audioOutputId, s.audioOutputId || "");
  localStorage.setItem(STORAGE.subtitles, s.subtitles ? "1" : "0");
  localStorage.setItem(STORAGE.avatarId, s.avatarId || "");
}

/* ── 显示字幕开关 ───────────────────────────────────────────────────────── */
function applySubtitles(enabled) {
  document.body.classList.toggle("no-subtitles", !enabled);
}

/* ── 状态机 ─────────────────────────────────────────────────────────────── */
const STATE_VIEWS = {
  idle: { caption: "点击开始对话", disabled: false },
  connecting: { caption: "连接中…", disabled: true },
  listening: { caption: "", disabled: false },
  "user-speaking": { caption: "", disabled: false },
  processing: { caption: "", disabled: false },
  "ai-speaking": { caption: "", disabled: false },
  error: { caption: "点击重试", disabled: false },
};
const STATE_CLASS = {
  idle: "state-idle",
  connecting: "state-connecting",
  listening: "state-listening",
  "user-speaking": "state-user-speaking",
  processing: "state-processing",
  "ai-speaking": "state-ai-speaking",
  error: "state-error",
};
const LIVE_STATES = new Set(["listening", "user-speaking", "processing", "ai-speaking"]);

const circleBtn = $("#main-circle");
const circleCaption = $("#circle-caption");
const orbWrap = $(".orb-wrap");
const micBtn = $("#mic-btn");
const stopBtn = $("#stop-btn");
const settingsBtn = $("#settings-btn");
const settingsModal = $("#settings-modal");
const settingsForm = settingsModal.querySelector("form");
const inputAudioInput = $("#audio-input");
const inputAudioOutput = $("#audio-output");
const audioOutputHint = $("#audio-output-hint");
const inputInstructions = $("#instructions");
const inputNoiseGate = $("#noise-gate");
const inputSubtitles = $("#subtitles-toggle");
const inputAvatarId = $("#avatar-id");
const gateValue = $("#gate-value");
const gateMeterFill = $("#gate-meter-fill");
const micGate = $("#mic-gate");
const mgaArc = document.querySelector("#mic-gate-arc");
const mgaTrack = document.querySelector("#mga-track");
const mgaFill = document.querySelector("#mga-fill");
const mgaHit = document.querySelector("#mga-hit");
const mgaHandle = document.querySelector("#mga-handle");
const restartBtn = $("#restart-conversation");
const restartHint = $("#restart-hint");

let currentState = "idle";
let settings = loadSettings();
let client = null;
let micStream = null;
let micMuted = false;
let userAudioReplaying = false;

const chat = new ChatView({
  onUserAudioPlaybackChange(playing) {
    userAudioReplaying = playing;
    syncMicMuteState();
  },
});

function syncMicMuteState() {
  const muted = micMuted || userAudioReplaying;
  for (const t of micStream?.getAudioTracks() ?? []) t.enabled = !muted;
  client?.setMuted(muted);
}

function setState(next) {
  currentState = next;
  const view = STATE_VIEWS[next] || STATE_VIEWS.idle;
  circleBtn.disabled = view.disabled;
  circleBtn.className = "circle " + (STATE_CLASS[next] || STATE_CLASS.idle);
  if (next !== "error") setCaption(view.caption);
  const live = LIVE_STATES.has(next);
  orbWrap.classList.toggle("live", live);
  micBtn.setAttribute("aria-hidden", live ? "false" : "true");
  stopBtn.setAttribute("aria-hidden", live ? "false" : "true");
  micBtn.tabIndex = live ? 0 : -1;
  stopBtn.tabIndex = live ? 0 : -1;
  updateRestartAvailability();
}

function setCaption(text, kind) {
  const trimmed = String(text || "").trim();
  circleCaption.textContent = trimmed;
  circleCaption.className = "circle-caption" + (kind ? " " + kind : "") + (trimmed ? "" : " empty");
}

function updateRestartAvailability() {
  const blocked = currentState === "connecting";
  restartBtn.disabled = blocked;
  restartHint.textContent = LIVE_STATES.has(currentState)
    ? "重连并使用上面的人设。"
    : "用人设开始一段对话。";
}

/* ── 噪音门限径向弧（mic 按钮周围的刻度弧） ──────────────────────────────── */
const ARC_R = 40;
const ARC_SPAN_DEG = 200;
const ARC_START_DEG = 180 - ARC_SPAN_DEG / 2;

function arcPoint(f, r) {
  const deg = ARC_START_DEG + f * (ARC_SPAN_DEG || 1);
  const rad = (deg * Math.PI) / 180;
  return { x: 50 + (r || ARC_R) * Math.cos(rad), y: 50 + (r || ARC_R) * Math.sin(rad) };
}
function fullArcD() {
  const a = arcPoint(0);
  const b = arcPoint(1);
  const largeArc = ARC_SPAN_DEG > 180 ? 1 : 0;
  return "M " + a.x + " " + a.y + " A " + ARC_R + " " + ARC_R + " 0 " + largeArc + " 1 " + b.x + " " + b.y;
}
function dbToFraction(db) {
  const c = Math.min(GATE_MAX_DB, Math.max(GATE_OFF_DB, db));
  return (c - GATE_OFF_DB) / (GATE_MAX_DB - GATE_OFF_DB);
}
function fractionToDb(f) {
  const c = Math.min(1, Math.max(0, f));
  return Math.round(GATE_OFF_DB + c * (GATE_MAX_DB - GATE_OFF_DB));
}
function initGateArc() {
  const d = fullArcD();
  mgaTrack.setAttribute("d", d);
  mgaFill.setAttribute("d", d);
  mgaHit.setAttribute("d", d);
  mgaFill.setAttribute("pathLength", "100");
  mgaFill.style.strokeDasharray = "100 100";
  mgaFill.style.strokeDashoffset = "100";
  renderGateHandle();
}
function renderGateHandle() {
  const off = settings.noiseGate <= GATE_OFF_DB;
  const p = arcPoint(dbToFraction(settings.noiseGate));
  mgaHandle.setAttribute("cx", String(p.x));
  mgaHandle.setAttribute("cy", String(p.y));
  micGate.classList.toggle("gate-off", off);
}
function paintInputLevel(rms) {
  const db = rms > 0 ? 20 * Math.log10(rms) : GATE_OFF_DB;
  const f = dbToFraction(db);
  mgaFill.style.strokeDashoffset = String(100 * (1 - f));
  if (settingsModal.open) gateMeterFill.style.width = f * 100 + "%";
  const enabled = settings.noiseGate > GATE_OFF_DB;
  micGate.classList.toggle("gate-open", enabled && f >= dbToFraction(settings.noiseGate));
}
function setGateThreshold(db) {
  settings.noiseGate = clampDb(db);
  const off = settings.noiseGate <= GATE_OFF_DB;
  inputNoiseGate.value = String(settings.noiseGate);
  gateValue.textContent = off ? "关" : settings.noiseGate + " dB";
  renderGateHandle();
  localStorage.setItem(STORAGE.noiseGate, String(settings.noiseGate));
  if (client && LIVE_STATES.has(currentState)) client.setNoiseGate(gateParams(settings.noiseGate));
}
function syncGateUi() {
  inputNoiseGate.value = String(settings.noiseGate);
  gateValue.textContent = settings.noiseGate <= GATE_OFF_DB ? "关" : settings.noiseGate + " dB";
  renderGateHandle();
}
function gatePointerToDb(e) {
  const rect = mgaArc.getBoundingClientRect();
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  let deg = (Math.atan2(e.clientY - cy, e.clientX - cx) * 180) / Math.PI;
  if (deg < 0) deg += 360;
  return fractionToDb((deg - ARC_START_DEG) / ARC_SPAN_DEG);
}
let gateDragging = false;
mgaHit.addEventListener("pointerdown", (e) => {
  gateDragging = true;
  mgaHit.setPointerCapture(e.pointerId);
  setGateThreshold(gatePointerToDb(e));
});
mgaHit.addEventListener("pointermove", (e) => {
  if (gateDragging) setGateThreshold(gatePointerToDb(e));
});
const endGateDrag = (e) => {
  if (!gateDragging) return;
  gateDragging = false;
  try { mgaHit.releasePointerCapture(e.pointerId); } catch (err) {}
};
mgaHit.addEventListener("pointerup", endGateDrag);
mgaHit.addEventListener("pointercancel", endGateDrag);

/* ── 连接/采集 ───────────────────────────────────────────────────────────── */
const MIC_CONSTRAINTS_BASE = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
function micConstraints() {
  const audio = Object.assign({}, MIC_CONSTRAINTS_BASE);
  if (settings.audioInputId) audio.deviceId = { ideal: settings.audioInputId };
  return { audio };
}
function createResumedAudioContext() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx({ latencyHint: "interactive" });
    if (ctx.state === "suspended") void ctx.resume().catch(() => {});
    return ctx;
  } catch (err) {
    console.warn("[main] AudioContext init failed:", err);
    return null;
  }
}
async function primeMicPermission() {
  try {
    const s = await navigator.mediaDevices.getUserMedia(micConstraints());
    for (const t of s.getTracks()) t.stop();
  } catch (err) {
    throw new Error("麦克风权限被拒绝" + (err instanceof Error ? ": " + err.message : ""));
  }
}
async function acquireMicStream() {
  micStream = await navigator.mediaDevices.getUserMedia(micConstraints());
  return micStream;
}

function wireClientEvents(c) {
  c.addEventListener("status", (e) => onClientStatus(e.detail.status));
  c.addEventListener("transcript", (e) => chat.onTranscript(e.detail));
  c.addEventListener("user-turn-started", (e) => chat.onUserTurnStarted(e.detail));
  c.addEventListener("user-turn-stopped", (e) => chat.onUserTurnStopped(e.detail));
  c.addEventListener("user-audio", (e) => chat.onUserAudio(e.detail));
  c.addEventListener("response-finished", (e) => chat.onResponseFinished(e.detail));
  c.addEventListener("error", (e) => { void onFatalError(e.detail.error); });
  c.addEventListener("server-error", (e) => {
    const msg = e.detail.error instanceof Error ? e.detail.error.message : String(e.detail.error);
    console.warn("[main] server error (non-fatal):", msg);
  });
  c.addEventListener("input-level", (e) => paintInputLevel(e.detail.rms));
  // orchestrator 反向要 sessionid：把当前 avatar 的 LiveTalking sessionid 回传
  c.addEventListener("sessionid-request", () => {
    const sid = window.DigitalAvatar?.sessionid;
    if (sid) c.sendControl({ type: "sessionid", sessionid: sid });
  });
}

function onClientStatus(status) {
  switch (status) {
    case "connecting":
    case "creating-session":
      setState("connecting");
      break;
    case "connected":
      setState("listening");
      break;
    case "user-speaking":
      setState("user-speaking");
      break;
    case "processing":
      setState("processing");
      break;
    case "ai-speaking":
      setState("ai-speaking");
      break;
    case "error":
      setState("error");
      break;
  }
}

async function doStart(audioContext) {
  chat.clear();
  chat.reset();
  setState("connecting");
  setCaption("连接中…", "muted");

  if (!audioContext) audioContext = createResumedAudioContext();
  try {
    await primeMicPermission();
  } catch (err) {
    if (audioContext) void audioContext.close().catch(() => {});
    throw err;
  }

  const directUrl = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/v1/realtime";
  const c = new S2sWsRealtimeClient({
    directUrl,
    voice: "",
    instructions: settings.instructions,
    acquireMic: acquireMicStream,
    noiseGate: gateParams(settings.noiseGate),
    audioOutputId: settings.audioOutputId || "",
    audioFromAvatar: true,
    ...(audioContext ? { audioContext } : {}),
  });
  client = c;
  c.setMuted(micMuted || userAudioReplaying);
  wireClientEvents(c);

  try {
    await c.connect();
  } catch (err) {
    if (audioContext) void audioContext.close().catch(() => {});
    throw err;
  }
  // 数字人：画面+声音都从 LiveTalking 同一条 WebRTC 流回传（同轨，天然对口型）。
  if (window.DigitalAvatar) {
    // 同一页面多次「开始对话」时，avatar WebRTC 已连过（pc 在），不再触发
    // avatar-ready，而 orchestrator 每次新 WS 连接都会清掉 sessionid →
    // 口型音频不再喂。每次连上后都主动把当前 avatar sessionid 回传一次。
    const sid = window.DigitalAvatar.sessionid;
    if (sid) client.sendControl({ type: "sessionid", sessionid: sid });
    void window.DigitalAvatar.connect();
    // 仍处于用户手势内：补一次播放，确保首段助手声音不被 autoplay 拦下
    window.DigitalAvatar.ensureAudioPlay?.();
  }
}

async function teardown() {
  chat.reset({ dismiss: true });
  if (client) {
    try { await client.close(); } catch (err) { console.warn("[main] close:", err); }
    client = null;
  }
  if (micStream) {
    for (const t of micStream.getTracks()) t.stop();
    micStream = null;
  }
  micMuted = false;
  micBtn.classList.remove("muted");
  setState("idle");
}

async function onFatalError(err) {
  console.error("[main] fatal:", err);
  const msg = err instanceof Error ? err.message : String(err);
  try { await teardown(); } catch (e) {}
  setState("error");
  setCaption(truncateError(msg), "error");
}
async function handleStartError(err) {
  await onFatalError(err);
}

/* ── 设置面板 ───────────────────────────────────────────────────────────── */
function openSettings() {
  inputInstructions.value = settings.instructions;
  inputSubtitles.checked = settings.subtitles;
  inputAvatarId.value = settings.avatarId;
  syncGateUi();
  updateRestartAvailability();
  void refreshAudioDeviceLists();
  settingsModal.showModal();
}
function readSettingsFromForm() {
  return {
    instructions: inputInstructions.value.trim() || DEFAULT_INSTRUCTIONS,
    noiseGate: clampDb(Number(inputNoiseGate.value)),
    audioInputId: inputAudioInput.value || "",
    audioOutputId: inputAudioOutput.value || "",
    subtitles: inputSubtitles.checked,
    avatarId: inputAvatarId.value.trim(),
  };
}
function supportsAudioOutputSelection() {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  return typeof Ctx?.prototype?.setSinkId === "function";
}
function fillDeviceSelect(select, devices, selectedId, fallbackLabel) {
  const prev = selectedId || select.value || "";
  select.replaceChildren();
  const def = document.createElement("option");
  def.value = "";
  def.textContent = "系统默认";
  select.appendChild(def);
  devices.forEach((d, i) => {
    const opt = document.createElement("option");
    opt.value = d.deviceId;
    opt.textContent = d.label || fallbackLabel + " " + (i + 1);
    select.appendChild(opt);
  });
  if (prev && ![...select.options].some((o) => o.value === prev)) {
    const missing = document.createElement("option");
    missing.value = prev;
    missing.textContent = fallbackLabel + "（已保存，未找到）";
    select.appendChild(missing);
  }
  select.value = prev;
  if (select.value !== prev) select.value = "";
}
async function refreshAudioDeviceLists() {
  const canPick = supportsAudioOutputSelection();
  inputAudioOutput.disabled = !canPick;
  audioOutputHint.textContent = canPick
    ? "助手语音播放到哪里，连接后可即时切换"
    : "扬声器切换需要 Chrome/Edge（AudioContext.setSinkId）";
  let devices = [];
  try { devices = await navigator.mediaDevices.enumerateDevices(); } catch (err) { console.warn("[main] devices:", err); }
  fillDeviceSelect(inputAudioInput, devices.filter((d) => d.kind === "audioinput"), settings.audioInputId, "麦克风");
  fillDeviceSelect(inputAudioOutput, devices.filter((d) => d.kind === "audiooutput"), settings.audioOutputId, "扬声器");
}
if (navigator.mediaDevices?.addEventListener) {
  navigator.mediaDevices.addEventListener("devicechange", () => {
    if (settingsModal.open) void refreshAudioDeviceLists();
  });
}

settingsBtn.addEventListener("click", openSettings);
inputNoiseGate.addEventListener("input", () => setGateThreshold(clampDb(Number(inputNoiseGate.value))));
inputSubtitles.addEventListener("change", () => {
  settings = { ...settings, subtitles: inputSubtitles.checked };
  saveSettings(settings);
  applySubtitles(settings.subtitles);
});
settingsForm.addEventListener("submit", (event) => {
  const submitter = event.submitter;
  if (submitter?.value !== "save") return;
  const prevAvatar = settings.avatarId;
  settings = readSettingsFromForm();
  saveSettings(settings);
  // 数字人 ID 改了：重连 avatar WebRTC（新 sessionid 经 avatar-ready 回传后端）
  if (settings.avatarId !== prevAvatar) window.DigitalAvatar?.restart();
  if (client && LIVE_STATES.has(currentState)) {
    client.updateSession({ instructions: settings.instructions });
    void client.setAudioOutputDevice(settings.audioOutputId);
    window.DigitalAvatar?.setSinkId(settings.audioOutputId);
  }
});
restartBtn.addEventListener("click", async () => {
  const prevAvatar = settings.avatarId;
  settings = readSettingsFromForm();
  saveSettings(settings);
  settingsModal.close();
  // 数字人 ID 改了：先重连 avatar（restart 会读新 localStorage 的 ID 并重新走
  // offer；doStart 里 connect() 因 connecting 在会直接跳过，不重复连）。
  if (settings.avatarId !== prevAvatar) window.DigitalAvatar?.restart();
  const audioContext = createResumedAudioContext();
  try {
    if (client) await teardown();
    await doStart(audioContext);
  } catch (err) {
    await handleStartError(err);
  }
});
inputAudioInput.addEventListener("change", () => {
  settings.audioInputId = inputAudioInput.value || "";
  saveSettings(settings);
});
inputAudioOutput.addEventListener("change", () => {
  settings.audioOutputId = inputAudioOutput.value || "";
  saveSettings(settings);
  if (client && $("audio")?.setSinkId) void client.setAudioOutputDevice(settings.audioOutputId);
  // 声音走数字人同轨：把扬声器切到 avatar 的 <audio> 元素上
  window.DigitalAvatar?.setSinkId(settings.audioOutputId);
});

/* ── 主按钮 ─────────────────────────────────────────────────────────────── */
circleBtn.addEventListener("click", async () => {
  if (currentState !== "idle" && currentState !== "error") return;
  try {
    await doStart();
  } catch (err) {
    await handleStartError(err);
  }
});
micBtn.addEventListener("click", () => {
  if (!micStream || !client) return;
  micMuted = !micMuted;
  syncMicMuteState();
  micBtn.classList.toggle("muted", micMuted);
  micBtn.setAttribute("aria-label", micMuted ? "取消静音" : "静音");
});
stopBtn.addEventListener("click", () => { void teardown(); });
$("#fullscreen-btn").addEventListener("click", () => {
  if (!document.fullscreenElement) {
    (document.documentElement.requestFullscreen || function () {}).call(document.documentElement);
  } else {
    (document.exitFullscreen || function () {}).call(document);
  }
});

/* ── 数字人 sessionid 回传后端（口型同步用） ─────────────────────────────── */
window.addEventListener("avatar-ready", (e) => {
  const sid = e.detail && e.detail.sessionid;
  if (sid && client) client.sendControl({ type: "sessionid", sessionid: sid });
});

/* ── 数字人真实出声 → 字幕跟随（digital-avatar.js 旁路检测） ────────────── */
window.addEventListener("avatar-sound", (e) => {
  chat.onAvatarSound(!!e.detail?.speaking);
});

/* ── 初始化 ─────────────────────────────────────────────────────────────── */
async function fetchConfig() {
  try {
    const res = await fetch("api/config");
    if (res.ok) {
      const json = await res.json();
      const persona = (json.persona || "").trim();
      // 服务端配置：字幕静音判定延迟（config.yaml avatar.subtitle_hide_delay_ms）
      if (typeof json.subtitleHideDelayMs === "number") {
        window.DigitalAvatar?.setSubtitleHideDelayMs(json.subtitleHideDelayMs);
      }
      // 首次使用：用服务端人设预填 Instructions
      if (persona && localStorage.getItem(STORAGE.instructions) === null) {
        settings.instructions = persona;
        saveSettings(settings);
        inputInstructions.value = persona;
      }
    }
  } catch (e) {
    console.warn("[main] config:", e);
  }
}

setState("idle");
applySubtitles(settings.subtitles);
// 页面加载即连数字人：让 LiveTalking sessionid 在点「开始对话」前就绪，
// 口型不依赖那次点击触发的 WS 连接时序（doStart 里还会再补发一次 sessionid）。
void window.DigitalAvatar?.connect();
chat.renderEmptyState();
initGateArc();
void fetchConfig();

requestAnimationFrame(() => {
  document.body.classList.remove("booting");
});
