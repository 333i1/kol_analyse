"""Fresh-by-videoId cache and replay_hash."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.analysis_result import AnalysisResultRow
from app.models.analysis_task import AnalysisTask
from app.models.raw_snapshot import RawSnapshot


def prompt_version_combined(settings=None) -> str:
    s = settings or get_settings()
    return f"{s.PROMPT_VERSION_CONTENT}+{s.PROMPT_VERSION_COMMENTS}"


def split_prompt_version(prompt_version: str) -> tuple[str, str]:
    """Split stored combined prompt_version into (content, comments)."""
    text = prompt_version or ""
    if "+" not in text:
        return text, ""
    content, comments = text.split("+", 1)
    return content, comments


def _content_prompt_matches(row: AnalysisResultRow, settings=None) -> bool:
    s = settings or get_settings()
    stored_content, _stored_comments = split_prompt_version(row.prompt_version)
    return stored_content == s.PROMPT_VERSION_CONTENT


def _comments_prompt_matches(row: AnalysisResultRow, settings=None) -> bool:
    s = settings or get_settings()
    _stored_content, stored_comments = split_prompt_version(row.prompt_version)
    return stored_comments == s.PROMPT_VERSION_COMMENTS


def compute_replay_hash(raw_snapshot_id: str, prompt_version: str, llm_model: str) -> str:
    payload = f"{raw_snapshot_id}:{prompt_version}:{llm_model}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_iso(ts: str) -> datetime:
    text = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def cutoff_iso(now: datetime | None = None, hours: int | None = None) -> str:
    s = get_settings()
    now = now or datetime.now(timezone.utc)
    hours = s.ANALYSIS_FRESH_TTL_HOURS if hours is None else hours
    cut = now - timedelta(hours=hours)
    return cut.strftime("%Y-%m-%dT%H:%M:%SZ")


def _result_dict(row: AnalysisResultRow) -> dict | None:
    try:
        data = json.loads(row.result_json or "")
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _pipeline_status(result: dict | None, key: str) -> str | None:
    if not isinstance(result, dict):
        return None
    block = result.get(key)
    if not isinstance(block, dict):
        return None
    status = block.get("pipeline_status")
    return status if isinstance(status, str) else None


def _latest_completed(db: Session, video_id: str) -> tuple[AnalysisResultRow, AnalysisTask] | None:
    cutoff = cutoff_iso()
    stmt = (
        select(AnalysisResultRow, AnalysisTask)
        .join(AnalysisTask, AnalysisTask.result_id == AnalysisResultRow.id)
        .where(AnalysisResultRow.video_id == video_id)
        .where(AnalysisTask.status == "completed")
        .where(AnalysisTask.billed == 1)
        .where(AnalysisResultRow.created_at >= cutoff)
        .order_by(AnalysisResultRow.created_at.desc())
        .limit(1)
    )
    row = db.execute(stmt).first()
    if row is None:
        return None
    return row[0], row[1]


def get_fresh(db: Session, video_id: str) -> tuple[AnalysisResultRow, AnalysisTask] | None:
    """Full 24h cache hit only when both pipelines succeeded and prompt_version matches."""
    found = _latest_completed(db, video_id)
    if found is None:
        return None
    row, task = found
    if row.prompt_version != prompt_version_combined():
        return None
    data = _result_dict(row)
    if (
        _pipeline_status(data, "content_analysis") == "ok"
        and _pipeline_status(data, "comment_analysis") == "ok"
    ):
        return row, task
    return None


def get_reusable_content(db: Session, video_id: str) -> dict | None:
    """Return cached content_analysis when that pipeline is ok (comments may have failed)."""
    found = _latest_completed(db, video_id)
    if found is None:
        return None
    row, _task = found
    if not _content_prompt_matches(row):
        return None
    data = _result_dict(row)
    if _pipeline_status(data, "content_analysis") != "ok":
        return None
    content = data.get("content_analysis")
    if not isinstance(content, dict):
        return None
    return copy.deepcopy(content)


def get_reusable_comments(db: Session, video_id: str) -> dict | None:
    """Return cached comment_analysis when that pipeline is ok (content may have failed)."""
    found = _latest_completed(db, video_id)
    if found is None:
        return None
    row, _task = found
    if not _comments_prompt_matches(row):
        return None
    data = _result_dict(row)
    if _pipeline_status(data, "comment_analysis") != "ok":
        return None
    comments = data.get("comment_analysis")
    if not isinstance(comments, dict):
        return None
    return copy.deepcopy(comments)


def get_in_flight(db: Session, video_id: str) -> AnalysisTask | None:
    stmt = (
        select(AnalysisTask)
        .where(AnalysisTask.video_id == video_id)
        .where(AnalysisTask.status.in_(("queued", "analyzing")))
        .order_by(AnalysisTask.created_at.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def get_fresh_raw(db: Session, video_id: str) -> RawSnapshot | None:
    cutoff = cutoff_iso()
    stmt = (
        select(RawSnapshot)
        .where(RawSnapshot.video_id == video_id)
        .where(RawSnapshot.fetched_at >= cutoff)
        .order_by(RawSnapshot.fetched_at.desc())
        .limit(1)
    )
    return db.scalar(stmt)
