from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import db as db_mod
from app.models.raw_snapshot import RawSnapshot
from app.models.video import Video
from app.services.worker import _persist_raw_snapshot
from tests.helpers import VID


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_video(db: Session, created_at: str | None = None) -> None:
    ts = created_at or _now()
    if db.get(Video, VID) is None:
        db.add(
            Video(
                video_id=VID,
                platform="youtube",
                canonical_url=f"https://www.youtube.com/watch?v={VID}",
                created_at=ts,
                updated_at=ts,
            )
        )
        db.commit()


def test_persist_insert_only_same_video_multiple_checksums(engine):
    db = db_mod.SessionLocal()
    try:
        _seed_video(db)
        r1 = _persist_raw_snapshot(
            db,
            video_id=VID,
            metadata={"title": "one"},
            transcript_mode="captions",
            transcript_text="alpha",
            cues=[{"start": 0, "duration": 1, "text": "a"}],
            comments=[{"comment_id": "c1", "text": "hi"}],
            platform_comment_count=1,
            notes=["first"],
        )
        r2 = _persist_raw_snapshot(
            db,
            video_id=VID,
            metadata={"title": "two"},
            transcript_mode="speech_to_text",
            transcript_text="beta",
            cues=[{"start": 0, "duration": 1, "text": "b"}],
            comments=[{"comment_id": "c2", "text": "yo"}],
            platform_comment_count=2,
            notes=["second"],
        )
        rows = db.query(RawSnapshot).filter(RawSnapshot.video_id == VID).all()
        assert len(rows) == 2
        ids = {r.id for r in rows}
        checksums = {r.checksum for r in rows}
        assert r1.id in ids and r2.id in ids
        assert r1.id != r2.id
        assert r1.checksum != r2.checksum
        assert len(checksums) == 2
    finally:
        db.close()


def test_orm_update_raw_snapshot_rejected(engine):
    db = db_mod.SessionLocal()
    try:
        _seed_video(db)
        raw = _persist_raw_snapshot(
            db,
            video_id=VID,
            metadata={"title": "one"},
            transcript_mode="captions",
            transcript_text="alpha",
            cues=[],
            comments=[],
            platform_comment_count=None,
            notes=[],
        )
        raw.checksum = "must-not-write"
        with pytest.raises(RuntimeError, match="insert-only"):
            db.commit()
        db.rollback()
        fresh = db.get(RawSnapshot, raw.id)
        assert fresh is not None
        assert fresh.checksum != "must-not-write"
    finally:
        db.close()
