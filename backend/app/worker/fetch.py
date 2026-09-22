"""Caption / comments fetch, STT fallback, raw snapshot persist. No dual-pipeline LLM here."""
from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.models.raw_snapshot import RawSnapshot
from app.services.audio_fetch import cleanup as cleanup_audio
from app.worker.deps import WorkerDeps
from app.services.transcript_client import sanitize_caption_failure_message
from app.worker import util as worker_util
from app.worker.util import _now, call_with_timeout


def _safe_comments(deps: WorkerDeps, video_id: str):
    try:
        items = deps.youtube.comment_threads(video_id)
        return items, None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"


def _stt_would_run(deps: WorkerDeps) -> bool:
    """True iff the next step after a caption miss would call whisper.transcribe."""
    try:
        return bool(deps.whisper.enabled())
    except Exception:
        return False


def _fetch_transcript_bundle(deps: WorkerDeps, video_id: str):
    """Caption cascade (human / yt-dlp / auto) only. Does NOT call STT."""
    extra_notes: list[str] = []
    try:
        tr = deps.transcript.fetch(video_id)
    except Exception as e:
        tr = e
    mode, status, cues, text, src = _consume_transcript_result(tr, extra_notes)
    return mode, status, cues, text, src, extra_notes


def _apply_stt(
    deps: WorkerDeps, video_id: str, notes: list[str]
) -> tuple[str, str, list[dict], str, str]:
    """Captions already failed. Try STT. Returns mode, status, cues, text, source_level."""
    audio_path = None
    try:
        if deps.whisper.enabled() and not deps.skip_audio:
            # production: real audio file, never transcribe("")
            audio_path = call_with_timeout(
                deps.audio_fetch_fn, worker_util.YDL_AUDIO_TIMEOUT_S, video_id
            )
            stt = call_with_timeout(
                deps.whisper.transcribe,
                worker_util.WHISPER_TRANSCRIBE_TIMEOUT_S,
                audio_path,
            )
        elif deps.whisper.enabled() and deps.skip_audio:
            # tests inject FakeWhisper that doesn't need audio
            stt = call_with_timeout(
                deps.whisper.transcribe, worker_util.WHISPER_TRANSCRIBE_TIMEOUT_S, ""
            )
        else:
            stt = None
        if stt is not None and stt.ok:
            cues = [{"start": c.start, "duration": c.duration, "text": c.text} for c in stt.cues]
            notes.append("字幕接口失败，已降级语音转录")
            return "speech_to_text", "degraded", cues, stt.text, "degraded"
        if stt is not None and not stt.ok:
            notes.append(stt.error_msg)
        return "none", "failed", [], "", "degraded"
    except Exception as e:
        notes.append(f"{type(e).__name__}: {e}")
        return "none", "failed", [], "", "degraded"
    finally:
        cleanup_audio(audio_path)


def _consume_transcript_result(tr, notes: list[str], deps: WorkerDeps | None = None, video_id: str | None = None):
    """Map caption fetch result. Caption miss does NOT auto-call STT (Phase 12)."""
    if isinstance(tr, Exception):
        tr_ok = False
        tr_err = f"{type(tr).__name__}: {tr}"
        tr_cues: list[dict] = []
        tr_text = ""
    else:
        tr_ok = tr.ok
        tr_err = tr.error_msg
        tr_cues = [{"start": c.start, "duration": c.duration, "text": c.text} for c in tr.cues]
        tr_text = tr.text
    if tr_ok:
        if getattr(tr, "is_generated", None) is True:
            notes.append("自动字幕（YouTube ASR）")
            return "captions", "ok", tr_cues, tr_text, "degraded"
        if getattr(tr, "is_generated", None) is False:
            notes.append("人工字幕")
        else:
            notes.append("字幕经公开接口获取")
        return "captions", "ok", tr_cues, tr_text, "ok"
    friendly = sanitize_caption_failure_message(tr_err or "字幕接口失败")
    notes.append(friendly)
    return "none", "failed", [], "", "degraded"


def _consume_comments_result(cm, notes: list[str]):
    if isinstance(cm, Exception):
        notes.append(f"评论抓取失败: {type(cm).__name__}: {cm}")
        return [], "failed"
    comments, comments_err = cm
    if comments_err:
        notes.append(comments_err)
    return comments, ("ok" if comments else "failed")


def _fetch_caption_and_or_comments(
    deps: WorkerDeps, video_id: str, need_transcript: bool, need_comments: bool
):
    """Thread A: captions only. Thread B: comments. STT is a later, user-gated step."""
    tr_bundle = None
    cm = None
    if need_transcript and need_comments:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_tr = pool.submit(_fetch_transcript_bundle, deps, video_id)
            fut_cm = pool.submit(_safe_comments, deps, video_id)
            tr_bundle = fut_tr.result()
            cm = fut_cm.result()
        return tr_bundle, cm
    if need_transcript:
        tr_bundle = _fetch_transcript_bundle(deps, video_id)
    if need_comments:
        cm = _safe_comments(deps, video_id)
    return tr_bundle, cm


def _persist_raw_snapshot(
    db: Session,
    *,
    video_id: str,
    metadata: dict,
    transcript_mode: str,
    transcript_text: str,
    cues: list[dict],
    comments: list[dict],
    platform_comment_count: int | None,
    notes: list[str],
) -> RawSnapshot:
    """Always INSERT. Never UPDATE an existing raw_snapshots row (T003 / T072)."""
    payload_norm = json.dumps(
        {"meta": metadata, "cues": cues, "comments": comments, "mode": transcript_mode},
        ensure_ascii=False,
        sort_keys=True,
    )
    checksum = hashlib.sha256(payload_norm.encode("utf-8")).hexdigest()
    raw = RawSnapshot(
        id=str(uuid.uuid4()),
        video_id=video_id,
        fetched_at=_now(),
        metadata_json=json.dumps(metadata, ensure_ascii=False),
        transcript_mode=transcript_mode,
        transcript_text=transcript_text or None,
        transcript_cues_json=json.dumps(cues, ensure_ascii=False),
        comments_json=json.dumps(comments, ensure_ascii=False),
        platform_comment_count=platform_comment_count,
        source_notes_json=json.dumps(notes, ensure_ascii=False),
        checksum=checksum,
    )
    db.add(raw)
    db.commit()
    return raw
