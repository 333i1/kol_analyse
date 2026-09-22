"""In-process asyncio worker. run_task(task_id) state machine queued→analyzing→completed|failed."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid

from sqlalchemy.orm import Session

from app import db as db_mod
from app.config import get_settings
from app.services.blogger_tier import classify_blogger_tier, parse_breaks
from app.services.video_meta_tags import (
    classify_format_kind,
    extract_paid_placement,
    infer_commercial,
    normalize_language,
)
from app.models.analysis_result import AnalysisResultRow
from app.models.analysis_task import AnalysisTask
from app.models.video import Video
from app.services.cache import (
    compute_replay_hash,
    get_fresh_raw,
    get_reusable_comments,
    prompt_version_combined,
)
from app.services.pipelines.content import run_content, run_content_from_snippet
from app.services.pipelines.comments import run_comments
from app.services.youtube_client import VideoUnavailable, YoutubeClientError, classify_youtube_client_error
from app.worker.assemble import (  # noqa: F401 — re-export for tests / callers
    SCHEMA_PATH,
    CTA_TYPES,
    REACTIONS,
    TONES,
    VIDEO_TYPES,
    _as_sentence,
    _clamp01,
    _degrade_to_failed_result,
    _failed_comments,
    _failed_content,
    _format_schema_error,
    _repair_comments,
    _repair_content,
    _repair_result,
    _schema_path,
    _validate_result_doc,
    build_result_document,
    format_count,
    format_duration,
    iso8601_duration_to_seconds,
    published_ago,
    validate_and_repair_result,
)
from app.worker.deps import (  # noqa: F401
    WorkerDeps,
    ensure_production_deps,
    get_deps,
    make_production_deps,
    set_deps,
)
from app.worker.fetch import (  # noqa: F401
    _apply_stt,
    _consume_comments_result,
    _consume_transcript_result,
    _fetch_caption_and_or_comments,
    _fetch_transcript_bundle,
    _persist_raw_snapshot,
    _safe_comments,
    _stt_would_run,
)
from app.worker.schedule import _run_pipeline_isolated, schedule_pipelines  # noqa: F401
from app.worker import transcript_choice as tc
from app.worker.auto_skip_stt import clear_auto_skip_stt, consume_auto_skip_stt
from app.worker.transcript_choice import (  # noqa: F401
    AWAITING_LABEL,
    AWAITING_TRANSCRIPT_CHOICE,
    SKIP_STT_NOTE,
    SKIP_STT_REASON,
    is_awaiting_transcript_choice,
    submit_transcript_choice,
)
from app.worker import util as worker_util
from app.worker.util import _now, call_with_timeout

logger = logging.getLogger(__name__)

STEP_LABELS = {
    0: "校验 URL",
    1: "拉取元数据",
    2: "字幕/评论",
    3: "内容拆解",
    4: "口碑聚合",
}

_run_lock = threading.Lock()

_queue: asyncio.Queue[str] | None = None
_worker_task: asyncio.Task | None = None

_force_ids: set[str] = set()
_skip_captions_ids: set[str] = set()
_cancelled_ids: set[str] = set()
_force_lock = threading.Lock()




def mark_skip_captions(task_id: str) -> None:
    with _force_lock:
        _skip_captions_ids.add(task_id)


def consume_skip_captions(task_id: str) -> bool:
    with _force_lock:
        try:
            _skip_captions_ids.remove(task_id)
            return True
        except KeyError:
            return False

def mark_force(task_id: str) -> None:
    with _force_lock:
        _force_ids.add(task_id)


def consume_force(task_id: str) -> bool:
    with _force_lock:
        try:
            _force_ids.remove(task_id)
            return True
        except KeyError:
            return False


def mark_cancelled(task_id: str) -> None:
    with _force_lock:
        _cancelled_ids.add(task_id)


def is_cancelled(task_id: str) -> bool:
    with _force_lock:
        return task_id in _cancelled_ids


def reset_queue() -> asyncio.Queue:
    global _queue
    _queue = asyncio.Queue()
    with _force_lock:
        _force_ids.clear()
        _skip_captions_ids.clear()
        _cancelled_ids.clear()
    tc.clear_pending()
    clear_auto_skip_stt()
    return _queue


def get_queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def worker_loop() -> None:
    q = get_queue()
    while True:
        task_id = await q.get()
        try:
            await asyncio.to_thread(run_task, task_id)
        except Exception:
            logger.exception("worker run_task crashed for %s", task_id)
        finally:
            q.task_done()


async def enqueue(task_id: str) -> None:
    await get_queue().put(task_id)


def recover_leftover_tasks() -> list[str]:
    """Startup recovery. Mark leftover analyzing failed/interrupted (do not re-run).

    Return leftover queued task ids for re-enqueue. Call BEFORE starting worker_loop.
    """
    if db_mod.SessionLocal is None:
        return []
    db = db_mod.SessionLocal()
    queued_ids: list[str] = []
    try:
        now = _now()
        analyzing = (
            db.query(AnalysisTask)
            .filter(AnalysisTask.status == "analyzing")
            .all()
        )
        for task in analyzing:
            task.status = "failed"
            task.error_code = "interrupted"
            if not task.error_message:
                task.error_message = "interrupted"
            task.completed_at = now
        queued = (
            db.query(AnalysisTask)
            .filter(AnalysisTask.status == "queued")
            .all()
        )
        queued_ids = [t.id for t in queued]
        db.commit()
        if analyzing:
            logger.info(
                "recovered %s leftover analyzing task(s) as interrupted",
                len(analyzing),
            )
        if queued_ids:
            logger.info("will re-enqueue %s leftover queued task(s)", len(queued_ids))
        return queued_ids
    except Exception:
        logger.exception("recover_leftover_tasks failed")
        try:
            db.rollback()
        except Exception:
            pass
        return queued_ids
    finally:
        db.close()


def _set_step(db: Session, task: AnalysisTask, step: int, label: str | None = None) -> None:
    task.current_step = step
    task.current_step_label = label if label is not None else STEP_LABELS.get(step, "")
    try:
        db.commit()
    except Exception:
        db.rollback()
        db.add(task)
        task.current_step = step
        task.current_step_label = label if label is not None else STEP_LABELS.get(step, "")
        db.commit()


def _mark_task_failed(task_id: str, db: Session, exc: BaseException) -> None:
    """Uncaught run_task error -> failed + in-flight cleared. Fresh session fallback."""
    err_code = "internal"
    err_msg = f"{type(exc).__name__}: {exc}".strip() or "internal"

    def _apply(session: Session) -> None:
        task = session.get(AnalysisTask, task_id)
        if task is None or task.status in ("completed", "failed"):
            return
        task.status = "failed"
        task.error_code = err_code
        task.error_message = err_msg
        task.completed_at = _now()
        session.commit()

    try:
        try:
            db.rollback()
        except Exception:
            pass
        _apply(db)
        return
    except Exception:
        logger.exception("mark-failed on existing session failed for %s", task_id)
    if db_mod.SessionLocal is None:
        return
    fresh = db_mod.SessionLocal()
    try:
        _apply(fresh)
    except Exception:
        logger.exception("mark-failed on fresh session failed for %s", task_id)
        try:
            fresh.rollback()
        except Exception:
            pass
    finally:
        fresh.close()


def _run_task_isolated(task_id: str, deps: WorkerDeps) -> None:
    """Own a Session on the timeout thread. Do not share the caller Session."""
    db = db_mod.SessionLocal()
    try:
        try:
            _run_task_db(db, task_id, deps)
        except Exception as e:
            if is_cancelled(task_id):
                return
            logger.exception("uncaught in run_task %s", task_id)
            _mark_task_failed(task_id, db, e)
    finally:
        db.close()


def run_task(task_id: str, deps: WorkerDeps | None = None) -> None:
    deps = deps or get_deps()
    if db_mod.SessionLocal is None:
        raise RuntimeError("DB not initialized")
    with _run_lock:
        try:
            call_with_timeout(_run_task_isolated, worker_util.RUN_TASK_TIMEOUT_S, task_id, deps)
        except TimeoutError as e:
            mark_cancelled(task_id)
            logger.exception("run_task timed out %s", task_id)
            db = db_mod.SessionLocal()
            try:
                _mark_task_failed(task_id, db, e)
            finally:
                db.close()


def _stop_if_cancelled(db: Session, task_id: str) -> bool:
    if is_cancelled(task_id):
        return True
    db.expire_all()
    task = db.get(AnalysisTask, task_id)
    return task is None or task.status == "failed"



def _enrich_commercial(content: dict, *, title: str, description: str, metadata: dict | None) -> dict:
    """Fill content_conclusion.commercial when empty, using API flag + title/desc clues."""
    if not isinstance(content, dict):
        return content
    cc = content.get("content_conclusion")
    if not isinstance(cc, dict):
        return content
    if cc.get("commercial") not in (None, ""):
        return content
    clue = infer_commercial(
        title=title or "",
        description=description or "",
        has_paid_product_placement=extract_paid_placement(metadata),
    )
    if clue:
        cc = dict(cc)
        cc["commercial"] = clue
        content = dict(content)
        content["content_conclusion"] = cc
    return content


def _store_completed_result(
    db: Session,
    *,
    task: AnalysisTask,
    raw,
    settings,
    title: str,
    channel: str,
    duration_seconds: int | None,
    published_at: str | None,
    view_count: int,
    like_count: int,
    fetched_n: int,
    platform_comment_count: int | None,
    is_partial: bool,
    notes: list[str],
    source_level: str,
    transcript_mode: str,
    transcript_status: str,
    comments_status: str,
    content: dict,
    comments_out: dict,
    subscriber_count: int | None = None,
    blogger_tier: str = "",
    channel_id: str | None = None,
    language: str = "",
    format_kind: str = "",
) -> None:
    result, overall, content_failed, comments_failed = build_result_document(
        task=task,
        settings=settings,
        title=title,
        channel=channel,
        duration_seconds=duration_seconds,
        published_at=published_at,
        view_count=view_count,
        like_count=like_count,
        fetched_n=fetched_n,
        platform_comment_count=platform_comment_count,
        is_partial=is_partial,
        notes=notes,
        source_level=source_level,
        transcript_mode=transcript_mode,
        transcript_status=transcript_status,
        comments_status=comments_status,
        content=content,
        comments_out=comments_out,
        subscriber_count=subscriber_count,
        blogger_tier=blogger_tier,
        channel_id=channel_id,
        language=language,
        format_kind=format_kind,
    )

    result, valid, schema_err, overall_override = validate_and_repair_result(result)
    if overall_override:
        overall = overall_override

    if not valid:
        task.status = "failed"
        task.error_code = "internal"
        task.error_message = f"result jsonschema validation failed: {schema_err}"
        task.completed_at = _now()
        db.commit()
        return

    overall = result.get("health", {}).get("overall_level") or overall

    if overall == "failed" and content_failed and comments_failed:
        pass

    pv = prompt_version_combined(settings)
    result_version = f"schema1.0.0+{settings.PROMPT_VERSION_CONTENT}+{settings.PROMPT_VERSION_COMMENTS}+{settings.LLM_MODEL}"
    row = AnalysisResultRow(
        id=str(uuid.uuid4()),
        video_id=task.video_id,
        raw_snapshot_id=raw.id,
        task_id=task.id,
        schema_version="1.0.0",
        result_version=result_version,
        prompt_version=pv,
        llm_model=settings.LLM_MODEL,
        replay_hash=compute_replay_hash(raw.id, pv, settings.LLM_MODEL),
        result_json=json.dumps(result, ensure_ascii=False),
        is_fixture=0,
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    task.result_id = row.id
    task.status = "completed" if overall != "failed" else "failed"
    if overall == "failed" and not (content_failed and comments_failed):
        task.status = "completed"
    if overall in ("ok", "degraded"):
        task.status = "completed"
    if overall == "failed":
        task.status = "failed" if content_failed and comments_failed else "completed"
        if content_failed and comments_failed:
            task.status = "failed"
    task.completed_at = _now()
    db.commit()


def _resume_after_transcript_choice(db: Session, task_id: str, deps: WorkerDeps, pause: tc.TranscriptPause) -> None:
    if _stop_if_cancelled(db, task_id):
        return
    task = db.get(AnalysisTask, task_id)
    if task is None or task.status in ("completed", "failed"):
        return
    settings = get_settings()
    notes = list(pause.notes)
    channel_id = None
    subscriber_count: int | None = None
    blogger_tier = ""
    # Best-effort: refresh tier on resume if channel id present in metadata
    try:
        meta = pause.metadata or {}
        sn = meta.get("snippet") or {}
        channel_id = str(sn.get("channelId") or "") or None
        if channel_id:
            ch = deps.youtube.channel_statistics(channel_id)
            subscriber_count = ch.get("subscriber_count")
            blogger_tier = classify_blogger_tier(
                subscriber_count,
                breaks=parse_breaks(getattr(get_settings(), "BLOGGER_TIER_BREAKS", None)),
            )
    except Exception as e:
        logger.warning("resume channel_statistics failed: %s", e)
    transcript_mode = pause.transcript_mode
    transcript_status = pause.transcript_status
    cues = list(pause.cues)
    transcript_text = pause.transcript_text
    source_level = pause.source_level
    raw = None
    if pause.raw_id:
        from app.models.raw_snapshot import RawSnapshot

        raw = db.get(RawSnapshot, pause.raw_id)

    comments_out = tc.wait_comments(pause)
    if _stop_if_cancelled(db, task_id):
        return

    if pause.skip:
        if SKIP_STT_NOTE not in notes:
            notes.append(SKIP_STT_NOTE)
        _set_step(db, task, 3)
        content = _run_pipeline_isolated(
            run_content_from_snippet,
            task_id=task.id,
            video_id=task.video_id,
            title=pause.title,
            description=pause.description,
            llm=deps.llm,
            mock_response=deps.content_llm_mock,
        )
        transcript_mode = "none"
        transcript_status = "failed"
        source_level = "degraded"
        cues = []
        transcript_text = ""
    else:
        _set_step(db, task, 2, "语音转录")
        transcript_mode, transcript_status, cues, transcript_text, src = _apply_stt(
            deps, task.video_id, notes
        )
        if src == "degraded":
            source_level = "degraded"
        _set_step(db, task, 3)
        content = _run_pipeline_isolated(
            run_content,
            task_id=task.id,
            video_id=task.video_id,
            title=pause.title,
            transcript_text=transcript_text,
            cues=cues,
            duration_seconds=pause.duration_seconds,
            llm=deps.llm,
            mock_response=deps.content_llm_mock,
        )

    raw = _persist_raw_snapshot(
        db,
        video_id=task.video_id,
        metadata=pause.metadata,
        transcript_mode=transcript_mode,
        transcript_text=transcript_text,
        cues=cues,
        comments=pause.comments,
        platform_comment_count=pause.platform_comment_count,
        notes=notes,
    )
    task.raw_snapshot_id = raw.id
    db.commit()
    if _stop_if_cancelled(db, task_id):
        return
    _set_step(db, task, 4)
    resume_lang = normalize_language(
        default_audio_language=((pause.metadata or {}).get("snippet") or {}).get("defaultAudioLanguage"),
        default_language=((pause.metadata or {}).get("snippet") or {}).get("defaultLanguage"),
        title=pause.title,
        description=pause.description or "",
    )
    resume_fmt = classify_format_kind(
        url=f"https://www.youtube.com/watch?v={task.video_id}",
        duration_seconds=pause.duration_seconds,
    )
    content = _enrich_commercial(
        content,
        title=pause.title,
        description=pause.description or "",
        metadata=pause.metadata,
    )
    _store_completed_result(
        db,
        task=task,
        raw=raw,
        settings=settings,
        title=pause.title,
        channel=pause.channel,
        duration_seconds=pause.duration_seconds,
        published_at=pause.published_at,
        view_count=pause.view_count,
        like_count=pause.like_count,
        fetched_n=pause.fetched_n,
        platform_comment_count=pause.platform_comment_count,
        is_partial=pause.is_partial,
        notes=notes,
        source_level=source_level,
        transcript_mode=transcript_mode,
        transcript_status=transcript_status,
        comments_status=pause.comments_status,
        content=content,
        comments_out=comments_out,
        subscriber_count=subscriber_count,
        blogger_tier=blogger_tier,
        channel_id=channel_id,
        language=resume_lang,
        format_kind=resume_fmt,
    )


def _run_task_db(db: Session, task_id: str, deps: WorkerDeps) -> None:
    pause = tc.get_pause(task_id)
    if pause is not None:
        if pause.skip is None:
            # Still waiting; worker picked us up with no choice yet.
            return
        try:
            _resume_after_transcript_choice(db, task_id, deps, pause)
        finally:
            tc.pop_pause(task_id)
        return

    if _stop_if_cancelled(db, task_id):
        return
    task = db.get(AnalysisTask, task_id)
    if task is None:
        logger.error("task %s not found", task_id)
        return
    settings = get_settings()
    force = consume_force(task_id)
    skip_captions = consume_skip_captions(task_id)
    task.status = "analyzing"
    task.started_at = _now()
    _set_step(db, task, 1)

    notes: list[str] = []
    source_level = "ok"
    transcript_mode = "none"
    transcript_status = "failed"
    cues: list[dict] = []
    transcript_text = ""
    comments: list[dict] = []
    comments_status = "failed"
    is_partial = False
    platform_comment_count: int | None = None
    metadata: dict = {}

    # --- metadata; reuse fresh snapshot so comments rerun can skip YouTube ---
    raw = None if force else get_fresh_raw(db, task.video_id)
    try:
        metadata = {}
        if raw is not None:
            try:
                loaded = json.loads(raw.metadata_json or "")
            except Exception:
                loaded = None
            if isinstance(loaded, dict) and loaded.get("snippet"):
                metadata = loaded
        if not metadata:
            metadata = deps.youtube.videos_list(task.video_id)
    except (VideoUnavailable, YoutubeClientError) as e:
        proxy = None
        try:
            proxy = deps.youtube._api_proxy()  # type: ignore[attr-defined]
        except Exception:
            fn = getattr(getattr(deps.youtube, "settings", None), "youtube_api_proxy_url", None)
            proxy = fn() if callable(fn) else None
        code, message = classify_youtube_client_error(e, proxy_url=proxy)
        task.status = "failed"
        task.error_code = code
        task.error_message = message
        task.completed_at = _now()
        db.commit()
        return
    except Exception as e:
        proxy = None
        try:
            proxy = deps.youtube._api_proxy()  # type: ignore[attr-defined]
        except Exception:
            fn = getattr(getattr(deps.youtube, "settings", None), "youtube_api_proxy_url", None)
            proxy = fn() if callable(fn) else None
        code, message = classify_youtube_client_error(e, proxy_url=proxy)
        task.status = "failed"
        task.error_code = code
        task.error_message = message
        task.completed_at = _now()
        db.commit()
        return

    if _stop_if_cancelled(db, task_id):
        return

    snippet = metadata.get("snippet") or {}
    stats = metadata.get("statistics") or {}
    details = metadata.get("contentDetails") or {}
    title = snippet.get("title") or task.video_id
    description = str(snippet.get("description") or "")
    channel = snippet.get("channelTitle") or "未知频道"
    channel_id = str(snippet.get("channelId") or "") or None
    subscriber_count: int | None = None
    blogger_tier = ""
    if channel_id:
        try:
            ch = deps.youtube.channel_statistics(channel_id)
            subscriber_count = ch.get("subscriber_count")
            blogger_tier = classify_blogger_tier(
                subscriber_count,
                breaks=parse_breaks(getattr(settings, "BLOGGER_TIER_BREAKS", None)),
            )
        except Exception as e:
            logger.warning("channel_statistics failed for %s: %s", channel_id, e)
            notes.append(f"频道订阅数未拿到，博主层级留空（{type(e).__name__}）")
    duration_seconds = iso8601_duration_to_seconds(details.get("duration"))
    published_at = snippet.get("publishedAt")
    language = normalize_language(
        default_audio_language=snippet.get("defaultAudioLanguage"),
        default_language=snippet.get("defaultLanguage"),
        title=title,
        description=description,
    )
    watch_url = f"https://www.youtube.com/watch?v={task.video_id}"
    format_kind = classify_format_kind(url=watch_url, duration_seconds=duration_seconds)
    view_count = int(stats.get("viewCount") or 0)
    like_count = int(stats.get("likeCount") or 0)
    platform_comment_count = int(stats.get("commentCount") or 0) if stats.get("commentCount") is not None else None

    video_row = db.get(Video, task.video_id)
    if video_row:
        video_row.title = title
        video_row.channel_name = channel
        video_row.duration_seconds = duration_seconds
        video_row.published_at = published_at
        video_row.updated_at = _now()
        db.commit()

    # --- step 2: transcript || comments, maybe reuse raw ---
    _set_step(db, task, 2)
    reuse_transcript = False
    reuse_comments = False
    if raw is not None:
        transcript_mode = raw.transcript_mode or "none"
        transcript_text = raw.transcript_text or ""
        cues = json.loads(raw.transcript_cues_json or "[]")
        comments = json.loads(raw.comments_json or "[]")
        platform_comment_count = raw.platform_comment_count
        notes = json.loads(raw.source_notes_json or "[]")
        if comments:
            comments_status = "ok"
            reuse_comments = True
        # Cached none/empty transcript must retry captions+STT (do not skip because comments cache exists).
        if transcript_mode != "none" and str(transcript_text).strip():
            reuse_transcript = True
            transcript_status = "ok" if transcript_mode == "captions" else "degraded"
            if transcript_mode == "speech_to_text":
                source_level = "degraded"

    captions_enabled = bool(get_settings().CAPTIONS_ENABLED) and not skip_captions
    need_transcript = not reuse_transcript and captions_enabled
    need_comments = not reuse_comments
    if not reuse_transcript and not captions_enabled:
        # D-036 / per-request skip_captions: no YouTube caption frontend.
        transcript_mode = "none"
        transcript_status = "failed"
        cues = []
        transcript_text = ""
        note = (
            "本任务跳过字幕（仅标题/简介+评论）"
            if skip_captions
            else "按产品策略未拉取字幕（仅标题/简介+评论）。"
        )
        if not any("未拉取字幕" in n or "跳过字幕" in n for n in notes):
            notes.append(note)
        source_level = "degraded"
    if need_transcript or need_comments:
        tr_bundle, cm = _fetch_caption_and_or_comments(
            deps, task.video_id, need_transcript, need_comments
        )
        if need_comments:
            comments, comments_status = _consume_comments_result(cm, notes)
        if need_transcript:
            transcript_mode, transcript_status, cues, transcript_text, src, tr_notes = (
                tr_bundle
            )
            notes.extend(tr_notes)
            if src == "degraded":
                source_level = "degraded"
        raw = _persist_raw_snapshot(
            db,
            video_id=task.video_id,
            metadata=metadata,
            transcript_mode=transcript_mode,
            transcript_text=transcript_text,
            cues=cues,
            comments=comments,
            platform_comment_count=platform_comment_count,
            notes=notes,
        )

    if _stop_if_cancelled(db, task_id):
        return

    task.raw_snapshot_id = raw.id
    db.commit()

    fetched_n = len(comments)
    if platform_comment_count and fetched_n < platform_comment_count:
        is_partial = True
        notes.append(f"评论抓取 {fetched_n} / 平台约 {platform_comment_count}")

    caption_miss = transcript_mode == "none" or not str(transcript_text).strip()
    if caption_miss and _stt_would_run(deps) and not skip_captions:
        # Single-video: pause for UI "是否语音转录".
        # Batch/channel: mark_auto_skip_stt(task) → consume here → skip STT, no second ask.
        pause = tc.TranscriptPause(
            task_id=task_id,
            video_id=task.video_id,
            comments=list(comments),
            comments_status=comments_status,
            notes=list(notes),
            metadata=metadata,
            raw_id=raw.id,
            title=title,
            description=description,
            channel=channel,
            duration_seconds=duration_seconds,
            published_at=published_at,
            view_count=view_count,
            like_count=like_count,
            fetched_n=fetched_n,
            platform_comment_count=platform_comment_count,
            is_partial=is_partial,
            source_level="degraded",
            transcript_mode="none",
            transcript_status="failed",
            force=force,
        )
        reused_comments = None if force else get_reusable_comments(db, task.video_id)
        tc.start_comments_job(pause, deps=deps, reused_comments=reused_comments)
        _set_step(db, task, 2, AWAITING_LABEL)
        tc.register_pause(pause)
        if consume_auto_skip_stt(task_id):
            # Batch / channel: honor upfront choice — skip STT, no second prompt.
            submit_transcript_choice(task_id, True)
            try:
                _resume_after_transcript_choice(db, task_id, deps, pause)
            finally:
                tc.pop_pause(task_id)
            return
        logger.info("task %s awaiting transcript_choice", task_id)
        return

    # --- content + comments ---
    # No transcript (skip_captions / CAPTIONS_ENABLED=false / caption miss without STT):
    # use title+description brief content path; do not call empty-transcript run_content.
    if caption_miss:
        if not any(("标题" in n and "简介" in n) or ("跳过字幕" in n) or ("未拉取字幕" in n) for n in notes):
            notes.append("无字幕：内容侧按标题/简介做简要结论，评论分析照常。")
        source_level = "degraded"
        transcript_mode = "none"
        transcript_status = "failed"
        cues = []
        transcript_text = ""
        _set_step(db, task, 3)
        content = _run_pipeline_isolated(
            run_content_from_snippet,
            task_id=task.id,
            video_id=task.video_id,
            title=title,
            description=description,
            llm=deps.llm,
            mock_response=deps.content_llm_mock,
        )
        reused_comments = None if force else get_reusable_comments(db, task.video_id)
        if reused_comments is not None:
            comments_out = reused_comments
            _set_step(db, task, 4)
        else:
            _set_step(db, task, 4)
            comments_out = _run_pipeline_isolated(
                run_comments,
                task_id=task.id,
                video_id=task.video_id,
                comments=comments,
                platform_total=platform_comment_count,
                llm=deps.llm,
                mock_response=deps.comments_llm_mock,
            )
        db.expire_all()
        task = db.get(AnalysisTask, task_id)
    else:
        content, comments_out, task = schedule_pipelines(
            db,
            task,
            task_id=task_id,
            title=title,
            transcript_text=transcript_text,
            cues=cues,
            duration_seconds=duration_seconds,
            comments=comments,
            platform_comment_count=platform_comment_count,
            deps=deps,
            set_step=_set_step,
            force=force,
        )
    if task is None:
        return
    if _stop_if_cancelled(db, task_id):
        return

    content = _enrich_commercial(
        content,
        title=title,
        description=description,
        metadata=metadata,
    )
    _store_completed_result(
        db,
        task=task,
        raw=raw,
        settings=settings,
        title=title,
        channel=channel,
        duration_seconds=duration_seconds,
        published_at=published_at,
        view_count=view_count,
        like_count=like_count,
        fetched_n=fetched_n,
        platform_comment_count=platform_comment_count,
        is_partial=is_partial,
        notes=notes,
        source_level=source_level,
        transcript_mode=transcript_mode,
        transcript_status=transcript_status,
        comments_status=comments_status,
        content=content,
        comments_out=comments_out,
        subscriber_count=subscriber_count,
        blogger_tier=blogger_tier,
        channel_id=channel_id,
        language=language,
        format_kind=format_kind,
    )
