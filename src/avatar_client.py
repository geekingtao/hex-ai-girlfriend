# -*- coding: utf-8 -*-
# =============================================================================
#  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
#  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
#  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
#  SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
# 数字人客户端：LiveTalking HTTP 接口封装
import httpx


class AvatarClient:
    def __init__(self, base_url='http://127.0.0.1:8010'):
        self.base = base_url.rstrip('/')

    def human_audio(self, wav_bytes, sessionid):
        files = {'file': ('seg.wav', wav_bytes, 'audio/wav')}
        data = {'sessionid': str(sessionid)}
        with httpx.Client(timeout=120) as c:
            return c.post(self.base + '/humanaudio', files=files, data=data)
# c2
    def interrupt(self, sessionid):
        with httpx.Client(timeout=30) as c:
            try:
                c.post(self.base + '/interrupt_talk', json={'sessionid': sessionid})
            except Exception:
                pass
            try:
                hp = dict(text="", type="echo", interrupt=True, sessionid=sessionid)
                c.post(self.base + "/human", json=hp)
            except Exception:
                pass
