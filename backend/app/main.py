from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.db import init_db
from app.logging_setup import install_redact_filter
from app.services.channel_worker import (
    channel_worker_loop,
    reset_channel_queue,
)
from app.services.worker import (
    enqueue,
    ensure_production_deps,
    recover_leftover_tasks,
    reset_queue,
    worker_loop,
)

logger = logging.getLogger(__name__)

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    install_redact_filter(
        extra_secrets=[
            settings.YOUTUBE_API_KEY,
            settings.LLM_API_KEY,
        ]
    )
    logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    init_db()
    ensure_production_deps()
    leftover_queued = recover_leftover_tasks()
    reset_queue()
    reset_channel_queue()
    task = asyncio.create_task(worker_loop())
    channel_task = asyncio.create_task(channel_worker_loop())
    app.state.worker_task = task
    app.state.channel_worker_task = channel_task
    for tid in leftover_queued:
        await enqueue(tid)
    try:
        yield
    finally:
        task.cancel()
        channel_task.cancel()
        for t in (task, channel_task):
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title="KOL Video Analysis", lifespan=lifespan)
app.include_router(router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


if _FRONTEND.is_dir():
    @app.get("/")
    def _index():
        return FileResponse(_FRONTEND / "index.html")

    # Serve static assets without swallowing /api/*
    app.mount("/static", StaticFiles(directory=str(_FRONTEND)), name="frontend-static")

    @app.get("/result-adapter.js")
    def _adapter():
        return FileResponse(_FRONTEND / "result-adapter.js", media_type="application/javascript")

    @app.get("/analysis-session.js")
    def _analysis_session():
        return FileResponse(_FRONTEND / "analysis-session.js", media_type="application/javascript")

    @app.get("/channel-adapter.js")
    def _channel_adapter():
        return FileResponse(_FRONTEND / "channel-adapter.js", media_type="application/javascript")

    @app.get("/channel-session.js")
    def _channel_session():
        return FileResponse(_FRONTEND / "channel-session.js", media_type="application/javascript")


@app.get("/batch-session.js")
def batch_session_js():
    return FileResponse(_FRONTEND / "batch-session.js", media_type="application/javascript")

    @app.get("/index.html")
    def _index_html():
        return FileResponse(_FRONTEND / "index.html")
