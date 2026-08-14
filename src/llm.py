# -*- coding: utf-8 -*-
# =============================================================================
#  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
#  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
#  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
#  SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
# LLM 大模型：可插拔接口，默认 llama.cpp（OpenAI 兼容 /v1 流式）
import json
import subprocess
from urllib.parse import urlsplit, urlunsplit

import httpx


def _tcp_reachable(host, port, timeout=1.0):
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wsl_default_gateway():
    # WSL2 NAT 模式下 Windows 主机即默认网关，取不到返回空串
    try:
        out = subprocess.check_output(
            ["ip", "route", "show", "default"],
            text=True, timeout=2, stderr=subprocess.DEVNULL)
    except Exception:
        return ""
    for line in out.splitlines():
        parts = line.split()
        if "via" in parts:
            idx = parts.index("via")
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return ""


class LLMBase:
    def generate(self, messages, on_token=None):
        raise NotImplementedError


class LlamaCppLLM(LLMBase):
    def __init__(self, base_url="http://127.0.0.1:8099/v1", api_key="",
                 model="", temperature=1.0, top_p=0.95, max_tokens=256, **kwargs):
        base_url = self._adapt_base_url(base_url)
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": "Bearer " + api_key} if api_key else {}
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens

    @staticmethod
    def _adapt_base_url(base_url):
        # WSL2 NAT 下 127.0.0.1 连不到 Windows 侧 llama-server 时，回退到默认网关（Windows 主机 IP）
        parts = urlsplit(base_url)
        if parts.hostname not in ("127.0.0.1", "localhost"):
            return base_url
        try:
            port = parts.port or 80
        except ValueError:
            port = 80
        if _tcp_reachable(parts.hostname, port):
            return base_url
        gateway = _wsl_default_gateway()
        if not gateway:
            return base_url
        netloc = gateway if parts.port is None else "{}:{}".format(gateway, parts.port)
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    def generate(self, messages, on_token=None):
        payload = {
            "messages": messages,
            "stream": True,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }
        if self.model:
            payload["model"] = self.model

        out = []
        with httpx.stream("POST", self.url, json=payload,
                          headers=self.headers, timeout=None) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                if delta:
                    out.append(delta)
                    if on_token:
                        on_token(delta)
        return "".join(out)


class TestLLM(LLMBase):
    def __init__(self, reply="hello there", **kwargs):
        self.reply = reply

    def generate(self, messages, on_token=None):
        import time
        for ch in self.reply:
            time.sleep(0.01)
            if on_token:
                on_token(ch)
        return self.reply


def build_llm(cfg):
    c = dict(cfg.get("llm", {}))
    engine = c.pop("engine", "llamacpp")
    if engine == "llamacpp":
        return LlamaCppLLM(**c)
    if engine == "test":
        return TestLLM(**c)
    raise ValueError("未知 LLM 引擎: " + engine)
