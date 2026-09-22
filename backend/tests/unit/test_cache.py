import json
from datetime import datetime, timedelta, timezone

from app import db as db_mod
from app.models.analysis_result import AnalysisResultRow
from app.models.analysis_task import AnalysisTask
from app.models.raw_snapshot import RawSnapshot
from app.models.video import Video
from app.services.cache import (
    compute_replay_hash,
    get_fresh,
    get_reusable_comments,
    get_reusable_content,
    prompt_version_combined,
)
from app.services.url_parser import parse_youtube_video_id


VID = "dQw4w9WgXcQ"


def _seed(db, *, created_at: str, status="completed", billed=1, result=None, prompt_version=None):
    if result is None:
        result = {
            "content_analysis": {"pipeline_status": "ok"},
            "comment_analysis": {"pipeline_status": "ok"},
        }
    pv = prompt_version if prompt_version is not None else prompt_version_combined()
    db.add(
        Video(
            video_id=VID,
            platform="youtube",
            canonical_url=f"https://www.youtube.com/watch?v={VID}",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    db.flush()
    raw = RawSnapshot(
        id="raw1",
        video_id=VID,
        fetched_at=created_at,
        metadata_json="{}",
        transcript_mode="captions",
        transcript_cues_json="[]",
        comments_json="[]",
        source_notes_json="[]",
        checksum="abc",
    )
    db.add(raw)
    db.flush()
    task = AnalysisTask(
        id="task1",
        video_id=VID,
        status=status,
        current_step=4,
        current_step_label="口碑聚合",
        billed=billed,
        raw_snapshot_id="raw1",
        created_at=created_at,
        completed_at=created_at,
    )
    db.add(task)
    db.flush()
    res = AnalysisResultRow(
        id="res1",
        video_id=VID,
        raw_snapshot_id="raw1",
        task_id="task1",
        result_version=f"schema1.0.0+{pv}+gpt-4o-mini",
        prompt_version=pv,
        llm_model="gpt-4o-mini",
        replay_hash=compute_replay_hash("raw1", pv, "gpt-4o-mini"),
        result_json=json.dumps(result),
        created_at=created_at,
    )
    db.add(res)
    db.flush()
    task.result_id = "res1"
    db.commit()
    return task, res


def test_replay_hash_formula():
    h = compute_replay_hash("raw1", "content-v1+comments-v1", "gpt-4o-mini")
    import hashlib

    expect = hashlib.sha256(b"raw1:content-v1+comments-v1:gpt-4o-mini").hexdigest()
    assert h == expect


def test_fresh_hit(engine):
    db = db_mod.SessionLocal()
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _seed(db, created_at=now)
        hit = get_fresh(db, VID)
        assert hit is not None
    finally:
        db.close()


def test_fresh_miss_when_comments_failed(engine):
    db = db_mod.SessionLocal()
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _seed(
            db,
            created_at=now,
            result={
                "content_analysis": {"pipeline_status": "ok", "skip_reason": None},
                "comment_analysis": {
                    "pipeline_status": "failed",
                    "noise": {"label": "噪音已过滤 0 条（约 0%）；LLM失败: ReadTimeout"},
                },
            },
        )
        assert get_fresh(db, VID) is None
        reused = get_reusable_content(db, VID)
        assert reused is not None
        assert reused["pipeline_status"] == "ok"
        assert get_reusable_comments(db, VID) is None
    finally:
        db.close()


def test_reusable_content_none_when_content_failed(engine):
    db = db_mod.SessionLocal()
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _seed(
            db,
            created_at=now,
            result={
                "content_analysis": {"pipeline_status": "failed", "skip_reason": "LLM失败"},
                "comment_analysis": {"pipeline_status": "ok"},
            },
        )
        assert get_fresh(db, VID) is None
        assert get_reusable_content(db, VID) is None
        reused = get_reusable_comments(db, VID)
        assert reused is not None
        assert reused["pipeline_status"] == "ok"
    finally:
        db.close()


def test_dual_fail_not_reusable(engine):
    db = db_mod.SessionLocal()
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _seed(
            db,
            created_at=now,
            status="failed",
            result={
                "content_analysis": {"pipeline_status": "failed", "skip_reason": "LLM失败"},
                "comment_analysis": {
                    "pipeline_status": "failed",
                    "noise": {"label": "噪音已过滤 0 条（约 0%）；LLM失败: ReadTimeout"},
                },
            },
        )
        assert get_fresh(db, VID) is None
        assert get_reusable_content(db, VID) is None
        assert get_reusable_comments(db, VID) is None
    finally:
        db.close()


def test_stale_comments_prompt_not_reused(engine):
    """comments-v1.1 cache must not be served after comments-v1.2 bump; content may reuse."""
    db = db_mod.SessionLocal()
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _seed(db, created_at=now, prompt_version="content-v1+comments-v1.1")
        assert get_fresh(db, VID) is None
        assert get_reusable_comments(db, VID) is None
        reused = get_reusable_content(db, VID)
        assert reused is not None
        assert reused["pipeline_status"] == "ok"
    finally:
        db.close()


def test_matching_comments_prompt_reused(engine):
    db = db_mod.SessionLocal()
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _seed(db, created_at=now)  # current combined, includes comments-v1.2
        assert get_fresh(db, VID) is not None
        reused = get_reusable_comments(db, VID)
        assert reused is not None
        assert reused["pipeline_status"] == "ok"
    finally:
        db.close()


def test_watch_and_short_same_key():
    a = parse_youtube_video_id(f"https://youtu.be/{VID}")
    b = parse_youtube_video_id(f"https://www.youtube.com/watch?v={VID}")
    assert a == b == VID
