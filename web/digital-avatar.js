/* ============================================================================
 * Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
 * Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
 * YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
 * SPDX-License-Identifier: GPL-3.0-or-later
 * ========================================================================== */
/* ============================================================================
 * digital-avatar.js — LiveTalking 数字人画面 + 声音
 *
 * 画面与声音都从 LiveTalking 的同一路 WebRTC 流回传：LiveTalking 把每个
 * Wav2Lip 视频帧和它对应的音频帧成对推给输出（process_frames →
 * push_audio_frame + push_video_frame），视频轨按 25fps、音频轨按 20ms
 * 实时排出，天然同轨对齐。浏览器只播这一路，声音与口型就靠 WebRTC 内建的
 * A/V 同步（RTP 时间戳）对齐，无需额外延迟补偿。
 * （旧方案「零度」正是这个思路：不再忽略音频轨，也停用 Realtime WS 直连播放。）
 *
 * 浏览器把拿到的 LiveTalking sessionid 通过 WS 控制消息发给后端（main.js 负责）。
 * ========================================================================== */
(function () {
  'use strict';

  var CFG = {
    offerUrl: '/lt/offer',          // 经 server.py 反代到 LiveTalking :8010
    reconnectMs: 3000,
    avatarIdKey: 'hexgf.avatarId',
  };

  var pc = null;
  var video = null;
  var audio = null;
  var sessionid = 0;
  var connecting = false;
  var probe = null;   // 旁路音频活动检测（驱动字幕跟随真实出声）
  var subtitleHideDelayMs = 1000;  // 静音持续多久才判定「已停声」（config.yaml avatar.subtitle_hide_delay_ms）

  function getVideo() {
    if (!video) {
      var layer = document.getElementById('av-layer');
      video = document.createElement('video');
      video.id = 'av-video';
      video.autoplay = true;
      video.playsInline = true;
      video.muted = true;           // 只出画面；声音走独立的 #av-audio
      video.setAttribute('muted', '');
      video.setAttribute('playsinline', '');
      layer.appendChild(video);
    }
    return video;
  }

  function getAudio() {
    if (!audio) {
      audio = document.createElement('audio');
      audio.id = 'av-audio';
      audio.autoplay = true;
      audio.playsInline = true;
      document.body.appendChild(audio);
    }
    return audio;
  }

  /**
   * 旁路声音活动检测：克隆数字人音频轨，喂给独立 AudioContext + Analyser，
   * 每 100ms 测一次 RMS，把「出声/静音」广播为 window 的 avatar-sound 事件。
   * 不改原 #av-audio 的播放路由（clone 共享解码，无额外出声），字幕靠它
   * 精确锚定数字人真实出声起点与静音终点，而不是字幕 delta 到达的瞬间。
   */
  function createSoundProbe(track) {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    var ctx;
    try { ctx = new Ctx(); } catch (e) { return null; }
    // 页面已有 sticky activation（用户点过开始对话）时这里能成功；
    // ensureAudioPlay 里的手势 resume 是双保险。
    if (ctx.state === 'suspended') ctx.resume().catch(function () {});
    var src = ctx.createMediaStreamSource(new MediaStream([track]));
    var analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    src.connect(analyser);
    // 图需连到 destination 才会被持续处理；gain=0 保持活跃但不产生任何声音
    var gain = ctx.createGain();
    gain.gain.value = 0;
    analyser.connect(gain);
    gain.connect(ctx.destination);
    var data = new Float32Array(analyser.fftSize);
    var speaking = false;
    var silentCount = 0;
    var timer = setInterval(function () {
      // context 未 resume（无用户手势前）时 analyser 数据恒 0，天然判静音
      analyser.getFloatTimeDomainData(data);
      var sum = 0;
      for (var i = 0; i < data.length; i++) sum += data[i] * data[i];
      var rms = Math.sqrt(sum / data.length);
      if (rms > 0.008) {
        silentCount = 0;
        if (!speaking) {
          speaking = true;
          window.dispatchEvent(new CustomEvent('avatar-sound', { detail: { speaking: true } }));
        }
      } else {
        silentCount += 1;
        // 连续静音满 delay（默认 1s，可配）才判定安静：消化句内停顿，
        // 避免「才说两三个字一停字幕就闪没」；字幕消失略迟于语音结束是可接受的。
        if (speaking && silentCount * 100 >= subtitleHideDelayMs) {
          speaking = false;
          window.dispatchEvent(new CustomEvent('avatar-sound', { detail: { speaking: false } }));
        }
      }
    }, 100);
    return {
      ctx: ctx,
      stop: function () {
        clearInterval(timer);
        try { src.disconnect(); } catch (e) {}
        try { ctx.close(); } catch (e) {}
      },
    };
  }

  function currentAvatarId() {
    return (localStorage.getItem(CFG.avatarIdKey) || '').trim();
  }

  function waitIce(p) {
    if (p.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise(function (res) {
      var t = setTimeout(res, 3000);
      p.addEventListener('icegatheringstatechange', function () {
        if (p.iceGatheringState === 'complete') { clearTimeout(t); res(); }
      });
    });
  }

  function connect() {
    if (pc || connecting) return;
    connecting = true;
    // 重连/换形象时清理旧探针，避免监听已死会话的流
    if (probe) { probe.stop(); probe = null; }
    try {
      var v = getVideo();
      pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
      pc.addTransceiver('video', { direction: 'recvonly' });
      pc.addTransceiver('audio', { direction: 'recvonly' });

      pc.ontrack = function (e) {
        if (e.track.kind === 'video') {
          v.srcObject = e.streams[0];
          v.play().catch(function () {});
          document.getElementById('av-layer').classList.add('live');
        } else if (e.track.kind === 'audio') {
          // 声音走同一条 WebRTC 轨（与视频成对推流，天然同步）。
          var a = getAudio();
          a.srcObject = new MediaStream([e.track]);
          a.play().catch(function () {});   // 无手势可能被拦，点击后 ensureAudioPlay() 重试
          // 旁路检测出声/静音 → 字幕跟随真实播放（不占原播放路由）
          if (probe) { probe.stop(); probe = null; }
          probe = createSoundProbe(e.track.clone());
        }
      };

      pc.onconnectionstatechange = function () {
        if (['failed', 'closed', 'disconnected'].indexOf(pc.connectionState) >= 0) {
          document.getElementById('av-layer').classList.remove('live');
          pc = null;
          sessionid = 0;
          setTimeout(connect, CFG.reconnectMs);
        }
      };

      var avatar = currentAvatarId();
      return pc.setLocalDescription(pc.createOffer())
        .then(function () { return waitIce(pc); })
        .then(function () {
          var body = { sdp: pc.localDescription.sdp, type: pc.localDescription.type };
          if (avatar) body.avatar = avatar;   // 指定形象 ID，否则 LiveTalking 用默认
          return fetch(CFG.offerUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
        })
        .then(function (r) {
          if (!r.ok) {
            return r.json().then(function (j) {
              throw new Error('数字人服务 ' + r.status + ': ' + ((j && j.msg) || ''));
            });
          }
          return r.json();
        })
        .then(function (ans) {
          sessionid = ans.sessionid;
          window.dispatchEvent(new CustomEvent('avatar-ready', { detail: { sessionid: sessionid } }));
          return pc.setRemoteDescription(ans);
        })
        .catch(function (err) {
          console.error('[avatar] 连接失败:', err);
          pc = null;
          sessionid = 0;
          // 选中的形象数据不完整（生成失败）→ 清掉本地记录回退默认形象重连，
          // 避免一直空屏。之后若重新生成了该形象，再手动填回去即可。
          if (/数据不完整/.test(err.message || '')) {
            localStorage.removeItem(CFG.avatarIdKey);
            console.warn('[avatar] 形象数据不完整，已回退默认形象，重新连接');
          }
          setTimeout(connect, CFG.reconnectMs);
        })
        .finally(function () { connecting = false; });
    } catch (err) {
      console.error('[avatar] 连接异常:', err);
      pc = null;
      sessionid = 0;
      connecting = false;
      setTimeout(connect, CFG.reconnectMs);
      return Promise.resolve();
    }
  }

  /** 在用户手势里补一次播放（浏览器 autoplay 策略：首次点击后允许出声）。
   *  同时 resume 探针 AudioContext —— analyser 需 running 才有数据。 */
  function ensureAudioPlay() {
    if (probe && probe.ctx && probe.ctx.state === 'suspended') {
      probe.ctx.resume().catch(function () {});
    }
    if (audio && audio.srcObject) {
      var p = audio.play();
      if (p && p.catch) p.catch(function () {});
    }
  }

  /** 改形象 ID 后重连 WebRTC：新 sessionid 经 avatar-ready 回传给后端。 */
  function restart() {
    if (pc) {
      try { pc.close(); } catch (e) {}
      pc = null;
    }
    sessionid = 0;
    connecting = false;
    connect();
  }

  function setSinkId(deviceId) {
    var a = getAudio();
    if (deviceId && typeof a.setSinkId === 'function') {
      a.setSinkId(deviceId).catch(function () {});
    }
  }

  /** config.yaml avatar.subtitle_hide_delay_ms → 探针静音判定阈值（ms）。 */
  function setSubtitleHideDelayMs(ms) {
    var v = Number(ms);
    if (Number.isFinite(v) && v > 0) subtitleHideDelayMs = v;
  }

  window.DigitalAvatar = {
    connect: connect,
    restart: restart,
    ensureAudioPlay: ensureAudioPlay,
    setSinkId: setSinkId,
    setSubtitleHideDelayMs: setSubtitleHideDelayMs,
    get sessionid() { return sessionid; },
    get audioEl() { return audio; },
  };
})();
