\
"""SC-C02 / C03 / C06 style fan-out tests with mocks."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app import db as db_mod
from app.db import init_db
from app.models.analysis_result import AnalysisResultRow
from app.models.analysis_task import AnalysisTask
from app.models.raw_snapshot import RawSnapshot
from app.models.video import Video
from app.services.cache import compute_replay_hash, get_fresh, prompt_version_combined
from app.services.channel_aggregate import aggregate_channel_brief
from app.services.channel_sampler import SampleVideo, sample_recent_and_hot
from app.services.channel_worker import create_channel_task_row, run_channel_task
from tests.conftest import make_test_db_url


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session():
    init_db(make_test_db_url(), static_pool=True)
    assert db_mod.SessionLocal is not None
    return db_mod.SessionLocal()


def _seed_fresh(db, video_id: str, *, comments_ok=True):
    now = _now()
    if db.get(Video, video_id) is None:
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
    raw_id = str(uuid.uuid4())
    db.add(
        RawSnapshot(
            id=raw_id,
            video_id=video_id,
            fetched_at=now,
            metadata_json="{}",
            transcript_mode="captions",
            transcript_text="x",
            transcript_cues_json="[]",
            comments_json="[]",
            platform_comment_count=20,
            source_notes_json="[]",
            checksum=video_id,
        )
    )
    db.flush()
    result = {
        "schema_version": "1.0.0",
        "result_version": "fixture",
        "video": {"video_id": video_id, "title": video_id},
        "task": {"status": "completed"},
        "health": {"notes": []},
        "metrics": {
            "view_count": 1000,
            "like_count": 10,
            "comment_count": 20,
            "duration_seconds": 100,
        },
        "content_analysis": {
            "pipeline_status": "ok",
            "content_conclusion": {"video_types": ["测评"], "tone": "冷静", "thesis": "论题"},
            "segments": [
                {"segment_kind": "开场", "commercial": None, "cta_type": None, "ad_overlay": False},
                {"segment_kind": "展开", "commercial": None, "cta_type": None, "ad_overlay": False},
                {"segment_kind": "收束", "commercial": None, "cta_type": None, "ad_overlay": False},
            ],
        },
        "comment_analysis": {
            "pipeline_status": "ok" if comments_ok else "failed",
            "sentiment": {"positive": 50, "neutral": 40, "negative": 10},
            "open_themes": ["价格 / 性价比"],
            "reaction_types": [],
            "negative_examples": [],
            "positive_examples": [],
            "human_review_items": [],
        },
        "contrast_summary": {"contrast_sentence": "内容与评论对照句"},
    }
    rid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    pv = prompt_version_combined()
    db.add(
        AnalysisResultRow(
            id=rid,
            video_id=video_id,
            task_id=tid,
            raw_snapshot_id=raw_id,
            result_json=json.dumps(result, ensure_ascii=False),
            result_version="fixture",
            prompt_version=pv,
            llm_model="test",
            replay_hash=compute_replay_hash(raw_id, pv, "test"),
            schema_version="1.0.0",
            is_fixture=0,
            created_at=now,
        )
    )
    db.add(
        AnalysisTask(
            id=tid,
            video_id=video_id,
            status="completed",
            current_step=4,
            current_step_label="口碑聚合",
            cache_hit=0,
            budget_exceeded=0,
            billed=1,
            result_id=rid,
            created_at=now,
            completed_at=now,
        )
    )
    db.commit()
    return tid


class FakeChannelClient:
    def fetch_sample_candidates(self, *, channel_id=None, handle=None, window_size=50):
        ch = {
            "channel_id": "UC" + "b" * 22,
            "title": "Demo",
            "handle": "@Demo",
            "subscriber_count": 100,
            "view_count": 1000,
            "video_count": 10,
            "uploads_playlist_id": "UUxxx",
        }
        samples = [
            SampleVideo(video_id=f"id{i:09d}", view_count=100 * (i + 1))
            for i in range(5)
        ]
        return ch, samples


def test_sc_c02_all_cache_hit_no_bill():
    db = _session()
    try:
        for i in range(5):
            _seed_fresh(db, f"id{i:09d}")
        row = create_channel_task_row(db, "https://www.youtube.com/@Demo")
        billed = {"n": 0}

        def enqueue_child(_vid):
            billed["n"] += 1

        task = run_channel_task(
            db, row.id, channel_client=FakeChannelClient(), enqueue_child=enqueue_child
        )
        assert billed["n"] == 0
        assert task.status in ("completed", "degraded")
        brief = json.loads(task.result_json)
        assert brief["metrics"]["sample_count_used"] == 5
    finally:
        db.close()


def test_sc_c03_failed_comments_excluded():
    db = _session()
    try:
        for i, ok in enumerate([True, True, False, True, True]):
            _seed_fresh(db, f"id{i:09d}", comments_ok=ok)
        samples = [SampleVideo(video_id=f"id{i:09d}", view_count=100) for i in range(5)]
        sampled = sample_recent_and_hot(samples, window_size=5)
        from sqlalchemy import select

        kids = []
        for cohort, group in (("recent", sampled.recent), ("hot", sampled.hot)):
            for v in group:
                row = db.scalar(
                    select(AnalysisResultRow)
                    .where(AnalysisResultRow.video_id == v.video_id)
                    .order_by(AnalysisResultRow.created_at.desc())
                )
                assert row is not None
                kids.append(
                    {
                        "video_id": v.video_id,
                        "cohort": cohort,
                        "view_count": 100,
                        "comment_count": 20,
                        "pipeline_status": "cache_hit",
                        "result": json.loads(row.result_json),
                    }
                )
        brief = aggregate_channel_brief(
            channel={"channel_id": "UC" + "b" * 22, "title": "Demo"},
            children=kids,
        )
        assert brief["audience_habits"]["weighted_video_count"] == 4
        assert any("评论管线失败" in n for n in brief["health"]["notes"])
    finally:
        db.close()


def test_sc_c06_partial_k_of_5():
    db = _session()
    try:
        _seed_fresh(db, "id000000000")
        _seed_fresh(db, "id000000001")
        row = create_channel_task_row(db, "https://www.youtube.com/@Demo")
        task = run_channel_task(db, row.id, channel_client=FakeChannelClient())
        brief = json.loads(task.result_json)
        assert brief["metrics"]["sample_count_used"] == 2
        assert task.status == "analyzing"
        statuses = [v["pipeline_status"] for v in brief["videos"]]
        assert "queued" in statuses
        assert "cache_hit" in statuses
    finally:
        db.close()
