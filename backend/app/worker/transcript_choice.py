"""In-process pause for operator STT choice. Lost on process kill (acceptable)."""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.worker.schedule import _run_pipeline_isolated
from app.services.pipelines.comments import run_comments
from app.worker.deps import WorkerDeps

SKIP_STT_REASON = "user_skipped_stt"
SKIP_STT_NOTE = "运营选择跳过语音转录，内容仅基于标题/简介"
AWAITING_TRANSCRIPT_CHOICE = "transcript_choice"
AWAITING_LABEL = "等待是否语音转录"

_pending_lock = threading.Lock()
_pending: dict[str, "TranscriptPause"] = {}
_bg_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tc-comments")


@dataclass
class TranscriptPause:
    task_id: str
    video_id: str
    event: threading.Event = field(default_factory=threading.Event)
    skip: bool | None = None
    comments_future: Future | None = None
    comments_out: dict[str, Any] | None = None
    comments: list = field(default_factory=list)
    comments_status: str = "failed"
    notes: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    raw_id: str | None = None
    title: str = ""
    description: str = ""
    channel: str = ""
    duration_seconds: int | None = None
    published_at: str | None = None
    view_count: int = 0
    like_count: int = 0
    fetched_n: int = 0
    platform_comment_count: int | None = None
    is_partial: bool = False
    source_level: str = "degraded"
    transcript_mode: str = "none"
    transcript_status: str = "failed"
    force: bool = False
    cues: list = field(default_factory=list)
    transcript_text: str = ""


def clear_pending() -> None:
    with _pending_lock:
        _pending.clear()


def is_awaiting_transcript_choice(task_id: str) -> bool:
    with _pending_lock:
        pause = _pending.get(task_id)
        return pause is not None and pause.skip is None


def get_pause(task_id: str) -> TranscriptPause | None:
    with _pending_lock:
        return _pending.get(task_id)


def register_pause(pause: TranscriptPause) -> None:
    with _pending_lock:
        _pending[pause.task_id] = pause


def pop_pause(task_id: str) -> TranscriptPause | None:
    with _pending_lock:
        return _pending.pop(task_id, None)


def submit_transcript_choice(task_id: str, skip: bool) -> str:
    """Record operator choice. Returns 'ok' | 'conflict' (not awaiting)."""
    with _pending_lock:
        pause = _pending.get(task_id)
        if pause is None or pause.skip is not None:
            return "conflict"
        pause.skip = bool(skip)
        pause.event.set()
        return "ok"


def start_comments_job(
    pause: TranscriptPause,
    *,
    deps: WorkerDeps,
    reused_comments: dict[str, Any] | None,
) -> None:
    if reused_comments is not None:
        pause.comments_out = reused_comments
        return

    def _run():
        return _run_pipeline_isolated(
            run_comments,
            task_id=pause.task_id,
            video_id=pause.video_id,
            comments=pause.comments,
            platform_total=pause.platform_comment_count,
            llm=deps.llm,
            mock_response=deps.comments_llm_mock,
        )

    pause.comments_future = _bg_pool.submit(_run)


def wait_comments(pause: TranscriptPause) -> dict[str, Any]:
    if pause.comments_out is not None:
        return pause.comments_out
    if pause.comments_future is not None:
        pause.comments_out = pause.comments_future.result()
        return pause.comments_out
    return {
        "pipeline_status": "failed",
        "low_confidence_items": [],
        "human_review_items": [],
        "noise": {
            "filtered_count": 0,
            "filtered_ratio": 0.0,
            "label": "噪音已过滤 0 条（约 0%）",
        },
        "reply_heat": {"reply_count": None, "included_in_sentiment": False},
    }
