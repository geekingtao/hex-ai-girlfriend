# -*- coding: utf-8 -*-
# =============================================================================
#  Hex AI Girlfriend — 本地实时数字人语音对话 (Realtime AI Digital Girlfriend)
#  Copyright (C) 2026 Hex-电脑课堂 (Hex Computer Classroom)
#  YouTube: https://www.youtube.com/channel/UC2s3nBn_v4So9-8GQORMDhA
#  SPDX-License-Identifier: GPL-3.0-or-later
# =============================================================================
# Web 服务：静态前端 + OpenAI-Realtime 风格 WebSocket + LiveTalking 反代
# 路由顺序很重要：/api/* 与 /lt/* 先注册，最后 mount StaticFiles 到 "/"。
import argparse
import os

import httpx
import uvicorn
import yaml
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from orchestrator import HexOrchestrator

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根（src/ 的上一级）
WEB_DIR = os.path.join(BASE_DIR, "web")

orch = None
avatar_base = "http://127.0.0.1:8010"

# __PART2__

def create_app(cfg):
    global orch, avatar_base
    avatar_base = cfg.get("avatar", {}).get("base_url", avatar_base)

    @asynccontextmanager
    async def lifespan(app):
        global orch
        orch = HexOrchestrator(cfg)
        yield

    app = FastAPI(title="realtime-voice-chat", lifespan=lifespan)

    @app.get("/api/health")
    async def health():
        return {"ok": orch is not None}

    # main.js 首次加载 GET api/config，用服务端人设预填 Instructions
    @app.get("/api/config")
    async def api_config():
        persona = orch._effective_persona() if orch else ""
        av = cfg.get("avatar", {})
        return {"search": False, "lb": False, "allowDirect": True,
                "s2sUrl": "", "startupGreeting": "", "persona": persona,
                "subtitleHideDelayMs": int(av.get("subtitle_hide_delay_ms", 1000))}

# __PART3__

    # LiveTalking 反代：数字人 WebRTC /lt/offer 与口型 /humanaudio 都经这里
    @app.api_route("/lt/{path:path}", methods=["POST", "GET", "PUT", "DELETE"])
    async def lt_proxy(path: str, request: Request):
        url = avatar_base + "/" + path
        body = await request.body()
        headers = {}
        ct = request.headers.get("content-type")
        if ct:
            headers["Content-Type"] = ct
# __PART4__
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.request(request.method, url, content=body, headers=headers)
# __PART5__
        except Exception as e:
            return Response(content=str(e).encode("utf-8"), status_code=502)
# __PART6__
        mt = resp.headers.get("content-type")
        return Response(content=resp.content, status_code=resp.status_code, media_type=mt)
# __PART7__
    @app.websocket("/v1/realtime")
    async def realtime_ws(ws: WebSocket):
        global orch
        if orch is None:
            await ws.close()
            return
        await ws.accept()
        await orch.run_ws(ws)
# __PART8__
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def main():
    parser = argparse.ArgumentParser(description="realtime-voice-chat")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    app = create_app(cfg)
    host = args.host or cfg.get("server", {}).get("host", "0.0.0.0")
    port = args.port or cfg.get("server", {}).get("port", 7860)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
