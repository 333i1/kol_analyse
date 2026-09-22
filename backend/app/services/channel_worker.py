"""Channel parent-task runner (D11). Fan-out reuses v1 cache; misses create child analyses."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app import db as db_mod
from app.models.analysis_result import AnalysisResultRow
from app.models.analysis_task import AnalysisTask
from app.models.channel_analysis_task import ChannelAnalysisTask
from app.models.video import Video
from app.services.cache import get_fresh, get_in_flight
from app.services.channel_aggregate import aggregate_channel_brief
from app.services.channel_client import ChannelClient
from app.services.channel_sampler import SampleVideo, sample_recent_and_hot
from app.services.channel_url_parser import parse_youtube_channel_ref
from app.services.youtube_client import YoutubeClientError
from app.worker.auto_skip_stt import mark_auto_skip_stt

logger = logging.getLogger(__name__)

STEP0 = "校验 URL"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_video(db: Session, video_id: str) -> None:
    if db.get(Video, video_id) is not None:
        return
    now = _now()
    db.add(
        Video(
            video_id=video_id,
            platform="youtube",
            canonical_url=f"https://www.youtube.com/watch?v={video_id}",
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()


def _ensure_child_task(db: Session, video_id: str) -> AnalysisTask:
    """Return in-flight or new queued AnalysisTask for video_id (no enqueue here)."""
    inflight = get_in_flight(db, video_id)
    if inflight is not None:
        return inflight
    _ensure_video(db, video_id)
    now = _now()
    task = AnalysisTask(
        id=str(uuid.uuid4()),
        video_id=video_id,
        status="queued",
        current_step=0,
        current_step_label=STEP0,
        cache_hit=0,
        budget_exceeded=0,
        billed=1,
        created_at=now,
    )
    db.add(task)
    db.flush()
    mark_auto_skip_stt(task.id)
    return task


def _child_from_sample(
    db: Session,
    v: SampleVideo,
    cohort: str,
    *,
    create_missing: bool,
) -> tuple[dict[str, Any], str | None]:
    """Build one aggregate child dict. Returns (child, new_task_id_to_enqueue|None)."""
    fresh = get_fresh(db, v.video_id)
    if fresh is not None:
        row, atask = fresh
        result = json.loads(row.result_json)
        return (
            {
                "video_id": v.video_id,
                "cohort": cohort,
                "title": (result.get("video") or {}).get("title"),
                "view_count": v.view_count,
                "published_at": v.published_at,
                "pipeline_status": "cache_hit",
                "child_task_id": atask.id,
                "notes": ["v1 cache_hit"],
                "result": result,
                "comment_count": (result.get("metrics") or {}).get("comment_count"),
                "like_count": (result.get("metrics") or {}).get("like_count"),
                "duration_seconds": (result.get("metrics") or {}).get("duration_seconds"),
            },
            None,
        )

    # Prefer latest completed even if not dual-ok fresh (partial)
    from sqlalchemy import select

    latest = db.execute(
        select(AnalysisResultRow, AnalysisTask)
        .join(AnalysisTask, AnalysisTask.result_id == AnalysisResultRow.id)
        .where(AnalysisResultRow.video_id == v.video_id)
        .where(AnalysisTask.status.in_(("completed", "failed")))
        .order_by(AnalysisResultRow.created_at.desc())
        .limit(1)
    ).first()
    if latest is not None:
        row, atask = latest[0], latest[1]
        result = None
        try:
            result = json.loads(row.result_json)
        except Exception:
            result = None
        status = "failed" if atask.status == "failed" else "completed"
        return (
            {
                "video_id": v.video_id,
                "cohort": cohort,
                "title": ((result or {}).get("video") or {}).get("title"),
                "view_count": v.view_count,
                "published_at": v.published_at,
                "pipeline_status": status,
                "child_task_id": atask.id,
                "notes": [],
                "result": result,
                "comment_count": ((result or {}).get("metrics") or {}).get("comment_count"),
                "like_count": ((result or {}).get("metrics") or {}).get("like_count"),
                "duration_seconds": ((result or {}).get("metrics") or {}).get("duration_seconds"),
            },
            None,
        )

    inflight = get_in_flight(db, v.video_id)
    if inflight is not None:
        return (
            {
                "video_id": v.video_id,
                "cohort": cohort,
                "title": None,
                "view_count": v.view_count,
                "published_at": v.published_at,
                "pipeline_status": inflight.status if inflight.status in ("queued", "analyzing") else "analyzing",
                "child_task_id": inflight.id,
                "notes": [],
                "result": None,
                "comment_count": None,
                "like_count": None,
                "duration_seconds": None,
            },
            None,
        )

    enqueue_id = None
    child_task_id = None
    status = "queued"
    if create_missing:
        task = _ensure_child_task(db, v.video_id)
        child_task_id = task.id
        enqueue_id = task.id if task.status == "queued" else None
        status = task.status
    return (
        {
            "video_id": v.video_id,
            "cohort": cohort,
            "title": None,
            "view_count": v.view_count,
            "published_at": v.published_at,
            "pipeline_status": status,
            "child_task_id": child_task_id,
            "notes": [],
            "result": None,
            "comment_count": None,
            "like_count": None,
            "duration_seconds": None,
        },
        enqueue_id,
    )


def _persist_brief(task: ChannelAnalysisTask, brief: dict[str, Any], children: list[dict]) -> None:
    all_done = all(
        c.get("pipeline_status") in ("cache_hit", "completed", "degraded", "failed")
        for c in children
    )
    any_fail = any(c.get("pipeline_status") == "failed" for c in children)
    any_pending = any(c.get("pipeline_status") in ("queued", "analyzing") for c in children)
    if any_pending:
        task.status = "analyzing"
    elif any_fail:
        task.status = "degraded"
    elif all_done:
        task.status = "completed"
    else:
        task.status = "analyzing"
    persist = {k: v for k, v in brief.items() if not str(k).startswith("_")}
    task.result_json = json.dumps(persist, ensure_ascii=False)
    task.updated_at = _now()


def run_channel_task(
    db: Session,
    task_id: str,
    *,
    channel_client: ChannelClient | None = None,
    window_size: int = 50,
    enqueue_child: Callable[[str], None] | None = None,
    create_missing: bool = True,
) -> ChannelAnalysisTask:
    """Resolve → sample → cache-hit or create child tasks → aggregate → persist.

    `enqueue_child` receives **analysis task ids** (not video ids) to put on the v1 queue.
    """
    task = db.get(ChannelAnalysisTask, task_id)
    if task is None:
        raise KeyError(task_id)

    task.status = "analyzing"
    task.updated_at = _now()
    db.commit()

    ref = parse_youtube_channel_ref(task.channel_url)
    if ref is None:
        task.status = "failed"
        task.error_code = "invalid_channel_url"
        task.error_message = "无法解析为合法 YouTube 频道，不创建任务"
        task.updated_at = _now()
        db.commit()
        return task

    client = channel_client or ChannelClient()
    try:
        channel, samples = client.fetch_sample_candidates(
            channel_id=ref.channel_id,
            handle=ref.handle,
            window_size=window_size,
        )
    except YoutubeClientError as e:
        task.status = "failed"
        task.error_code = "channel_fetch_failed"
        task.error_message = str(e)[:500]
        task.updated_at = _now()
        db.commit()
        return task

    sampled = sample_recent_and_hot(samples, window_size=window_size)
    if not sampled.video_ids:
        task.status = "failed"
        task.error_code = "empty_sample"
        task.error_message = "uploads 为空，无法抽样"
        task.updated_at = _now()
        db.commit()
        return task

    task.channel_id = channel.get("channel_id")
    task.handle = (channel.get("handle") or "").lstrip("@") or ref.handle
    task.sample_video_ids_json = json.dumps(sampled.video_ids, ensure_ascii=False)

    children: list[dict[str, Any]] = []
    child_ids: list[str | None] = []
    to_enqueue: list[str] = []

    for v in sampled.recent:
        child, eid = _child_from_sample(db, v, "recent", create_missing=create_missing)
        children.append(child)
        child_ids.append(child.get("child_task_id"))
        if eid:
            to_enqueue.append(eid)
    for v in sampled.hot:
        child, eid = _child_from_sample(db, v, "hot", create_missing=create_missing)
        children.append(child)
        child_ids.append(child.get("child_task_id"))
        if eid:
            to_enqueue.append(eid)

    task.child_task_ids_json = json.dumps(child_ids, ensure_ascii=False)
    db.commit()

    for tid in to_enqueue:
        if enqueue_child is not None:
            try:
                enqueue_child(tid)
            except Exception:
                logger.exception("enqueue_child failed for %s", tid)

    brief = aggregate_channel_brief(
        channel={**channel, "canonical_url": task.channel_url},
        children=children,
        sample_meta={"window_size": window_size, "gap_notes": sampled.gap_notes},
        ops={
            "verdict": task.ops_verdict,
            "note": task.ops_note,
            "updated_at": None,
        },
    )
    brief["_debug_would_bill"] = len(to_enqueue)
    _persist_brief(task, brief, children)
    db.commit()
    db.refresh(task)
    task._debug_brief = brief  # type: ignore[attr-defined]
    return task


def refresh_channel_task(db: Session, task_id: str) -> ChannelAnalysisTask:
    """Re-read child analysis rows and re-aggregate without re-fetching YouTube."""
    task = db.get(ChannelAnalysisTask, task_id)
    if task is None:
        raise KeyError(task_id)
    if task.status in ("failed",) and not task.result_json:
        return task
    if not task.sample_video_ids_json or not task.result_json:
        return task

    try:
        prev = json.loads(task.result_json)
    except Exception:
        return task

    video_ids = json.loads(task.sample_video_ids_json)
    # recover cohort from previous videos rows
    cohort_map = {
        row.get("video_id"): row.get("cohort") or "recent"
        for row in (prev.get("videos") or [])
        if isinstance(row, dict)
    }
    view_map = {
        row.get("video_id"): row.get("view_count")
        for row in (prev.get("videos") or [])
        if isinstance(row, dict)
    }

    children: list[dict[str, Any]] = []
    for vid in video_ids:
        sample = SampleVideo(video_id=vid, view_count=int(view_map.get(vid) or 0))
        child, _ = _child_from_sample(
            db, sample, cohort_map.get(vid) or "recent", create_missing=False
        )
        children.append(child)

    channel = prev.get("channel") or {
        "channel_id": task.channel_id or "",
        "title": "",
        "handle": task.handle,
    }
    sample_meta = prev.get("sample") or {}
    brief = aggregate_channel_brief(
        channel=channel,
        children=children,
        sample_meta={
            "window_size": sample_meta.get("window_size") or 50,
            "gap_notes": sample_meta.get("gap_notes") or [],
        },
        ops={
            "verdict": task.ops_verdict,
            "note": task.ops_note,
            "updated_at": task.updated_at,
        },
        result_version=prev.get("result_version") or "channel-brief-v1",
    )
    _persist_brief(task, brief, children)
    db.commit()
    db.refresh(task)
    return task


def create_channel_task_row(db: Session, url: str) -> ChannelAnalysisTask:
    now = _now()
    ref = parse_youtube_channel_ref(url)
    if ref is None:
        raise ValueError("invalid_channel_url")
    row = ChannelAnalysisTask(
        id=str(uuid.uuid4()),
        channel_id=ref.channel_id,
        channel_url=url.strip(),
        handle=ref.handle,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

# --- channel asyncio queue (parent jobs; separate from v1 analysis queue) ---
_channel_queue: asyncio.Queue[str] | None = None


def reset_channel_queue() -> asyncio.Queue:
    global _channel_queue
    _channel_queue = asyncio.Queue()
    return _channel_queue


def get_channel_queue() -> asyncio.Queue:
    global _channel_queue
    if _channel_queue is None:
        _channel_queue = asyncio.Queue()
    return _channel_queue


async def enqueue_channel(task_id: str) -> None:
    await get_channel_queue().put(task_id)


def _run_channel_job(task_id: str) -> list[str]:
    """Open a Session, run_channel_task, return child analysis ids to enqueue."""
    if db_mod.SessionLocal is None:
        raise RuntimeError("DB not initialized")
    db = db_mod.SessionLocal()
    pending: list[str] = []
    try:
        def _enqueue_child(analysis_task_id: str) -> None:
            pending.append(analysis_task_id)

        run_channel_task(db, task_id, enqueue_child=_enqueue_child)
        return pending
    except Exception:
        logger.exception("channel job crashed for %s", task_id)
        try:
            db.rollback()
        except Exception:
            pass
        try:
            task = db.get(ChannelAnalysisTask, task_id)
            if task is not None and task.status in ("queued", "analyzing") and not task.result_json:
                task.status = "failed"
                task.error_code = "internal"
                task.error_message = "channel worker crashed"
                task.updated_at = _now()
                db.commit()
        except Exception:
            logger.exception("failed to mark channel task %s failed", task_id)
        return []
    finally:
        db.close()


async def channel_worker_loop() -> None:
    """Drain channel parent queue; fan-out children onto the v1 analysis queue."""
    from app.services.worker import enqueue

    q = get_channel_queue()
    while True:
        task_id = await q.get()
        try:
            pending = await asyncio.to_thread(_run_channel_job, task_id)
            for child_id in pending:
                await enqueue(child_id)
        except Exception:
            logger.exception("channel_worker_loop crashed for %s", task_id)
        finally:
            q.task_done()

