from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from app import db as db_mod
from app.db import init_db
from app.main import app
from app.models.analysis_result import AnalysisResultRow
from app.models.analysis_task import AnalysisTask
from app.models.raw_snapshot import RawSnapshot
from app.schemas.http import AnalyzeRequest
from app.services.worker import WorkerDeps, set_deps
from tests.conftest import make_test_db_url
from tests.helpers import (
    COMMENTS_MOCK,
    CONTENT_MOCK,
    FakeTranscript,
    FakeWhisper,
    FakeYoutube,
    VID,
)

URL = f"https://www.youtube.com/watch?v={VID}"


def _deps() -> WorkerDeps:
    return WorkerDeps(
        youtube=FakeYoutube(),
        transcript=FakeTranscript(ok=True),
        whisper=FakeWhisper(enabled=False),
        skip_audio=True,
        content_llm_mock=CONTENT_MOCK,
        comments_llm_mock=COMMENTS_MOCK,
    )


def _wait(client, tid, *, ticks: int = 80):
    body = None
    for _ in range(ticks):
        body = client.get(f"/api/v1/analyses/{tid}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"timeout waiting for {tid}: {body}")


def _session():
    assert db_mod.SessionLocal is not None
    return db_mod.SessionLocal()


def test_analyze_request_force_optional_default_false():
    req = AnalyzeRequest(url=URL)
    assert req.force is False
    forced = AnalyzeRequest(url=URL, force=True)
    assert forced.force is True


def test_force_true_bypasses_fresh_cache_new_raw_and_result():
    init_db(make_test_db_url(), static_pool=False)
    set_deps(_deps())
    with TestClient(app) as client:
        r1 = client.post("/api/v1/analyses", json={"url": URL})
        assert r1.status_code == 202
        body1 = _wait(client, r1.json()["task_id"])
        assert body1["status"] == "completed"

        db = _session()
        try:
            raw_before = db.query(RawSnapshot).count()
            res_before = db.query(AnalysisResultRow).count()
            billed_before = db.query(AnalysisTask).filter(AnalysisTask.billed == 1).count()
        finally:
            db.close()
        assert raw_before == 1
        assert res_before == 1

        r_hit = client.post("/api/v1/analyses", json={"url": URL})
        assert r_hit.status_code == 200
        assert r_hit.json()["status"] == "cache_hit"

        r2 = client.post("/api/v1/analyses", json={"url": URL, "force": True})
        assert r2.status_code == 202
        assert r2.json()["cache_hit"] is False
        assert r2.json()["task_id"] != r1.json()["task_id"]
        body2 = _wait(client, r2.json()["task_id"])
        assert body2["status"] == "completed"
        assert body2["cache_hit"] is False

        db = _session()
        try:
            raws = db.query(RawSnapshot).filter(RawSnapshot.video_id == VID).all()
            results = db.query(AnalysisResultRow).filter(AnalysisResultRow.video_id == VID).all()
            billed_after = db.query(AnalysisTask).filter(AnalysisTask.billed == 1).count()
            checksums = {r.checksum for r in raws}
        finally:
            db.close()
        assert len(raws) == 2
        assert len(results) == 2
        assert billed_after == billed_before + 1
        assert len(checksums) >= 1


def test_ttl_expired_without_force_is_not_cache_hit_inserts_raw():
    init_db(make_test_db_url(), static_pool=False)
    set_deps(_deps())
    with TestClient(app) as client:
        r1 = client.post("/api/v1/analyses", json={"url": URL})
        assert r1.status_code == 202
        body1 = _wait(client, r1.json()["task_id"])
        assert body1["status"] == "completed"

        old = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        db = _session()
        try:
            db.execute(text("UPDATE analysis_results SET created_at = :t"), {"t": old})
            db.execute(text("UPDATE raw_snapshots SET fetched_at = :t"), {"t": old})
            db.commit()
        finally:
            db.close()

        r2 = client.post("/api/v1/analyses", json={"url": URL})
        assert r2.status_code == 202
        assert r2.json()["cache_hit"] is False
        body2 = _wait(client, r2.json()["task_id"])
        assert body2["status"] == "completed"

        db = _session()
        try:
            raws = db.query(RawSnapshot).filter(RawSnapshot.video_id == VID).all()
        finally:
            db.close()
        assert len(raws) == 2
        assert len({r.checksum for r in raws}) >= 1


def test_history_list_unchanged_after_force():
    init_db(make_test_db_url(), static_pool=False)
    set_deps(_deps())
    with TestClient(app) as client:
        r1 = client.post("/api/v1/analyses", json={"url": URL})
        tid1 = r1.json()["task_id"]
        _wait(client, tid1)
        lst1 = client.get("/api/v1/analyses")
        assert lst1.status_code == 200
        payload1 = lst1.json()
        assert set(payload1) == {"items", "count"}
        assert payload1["count"] == 1
        keys = set(payload1["items"][0])
        assert {
            "task_id",
            "video_id",
            "canonical_url",
            "title",
            "summary",
            "video_types",
            "status",
            "created_at",
            "error",
        } <= keys

        r2 = client.post("/api/v1/analyses", json={"url": URL, "force": True})
        _wait(client, r2.json()["task_id"])
        lst2 = client.get("/api/v1/analyses")
        payload2 = lst2.json()
        assert set(payload2) == {"items", "count"}
        assert payload2["count"] == 2
        for item in payload2["items"]:
            assert {
                "task_id",
                "video_id",
                "canonical_url",
                "title",
                "summary",
                "video_types",
                "status",
                "created_at",
                "error",
            } <= set(item)


def test_no_refresh_route():
    init_db(make_test_db_url(), static_pool=False)
    set_deps(_deps())
    with TestClient(app) as client:
        r = client.post("/api/v1/analyses", json={"url": URL})
        tid = r.json()["task_id"]
        _wait(client, tid)
        refresh = client.post(f"/api/v1/analyses/{tid}/refresh")
        assert refresh.status_code in (404, 405)
