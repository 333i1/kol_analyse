
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import init_db
from app import db as db_mod
from app.main import app
from app.models.analysis_task import AnalysisTask
from app.models.cost_ledger import CostLedgerEntry
from app.models.video import Video
from app.services.worker import WorkerDeps, set_deps
from tests.conftest import make_test_db_url
from tests.helpers import (
    COMMENTS_MOCK,
    CONTENT_MOCK,
    FakeTranscript,
    FakeWhisper,
    FakeYoutube,
)


def _fresh_app_client():
    init_db(make_test_db_url(), static_pool=False)
    yt = FakeYoutube()
    deps = WorkerDeps(
        youtube=yt,
        transcript=FakeTranscript(ok=True),
        whisper=FakeWhisper(ok=False, enabled=False),
        skip_audio=True,
        content_llm_mock=CONTENT_MOCK,
        comments_llm_mock=COMMENTS_MOCK,
    )
    set_deps(deps)
    return TestClient(app, raise_server_exceptions=True), yt


def test_invalid_url_400_zero_rows():
    client, yt = _fresh_app_client()
    with client:
        r = client.post("/api/v1/analyses", json={"url": "https://example.com/foo"})
        assert r.status_code == 400
        body = r.json()
        assert body["error"] == "invalid_url"
        assert body["message"] == "无法解析为合法 YouTube 视频，不创建任务"
        db = db_mod.SessionLocal()
        try:
            assert db.query(AnalysisTask).count() == 0
            assert db.query(Video).count() == 0
        finally:
            db.close()
        assert yt.videos_list_calls == 0
        assert yt.comment_calls == 0


def test_missing_url_invalid_json():
    client, _ = _fresh_app_client()
    with client:
        r = client.post("/api/v1/analyses", json={"foo": 1})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_json"
        r2 = client.post(
            "/api/v1/analyses",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        assert r2.status_code == 400
        db = db_mod.SessionLocal()
        try:
            assert db.query(AnalysisTask).count() == 0
        finally:
            db.close()


def test_healthz():
    client, _ = _fresh_app_client()
    with client:
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_202_then_poll_completed():
    client, yt = _fresh_app_client()
    with client:
        r = client.post(
            "/api/v1/analyses",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
        assert r.status_code == 202
        env = r.json()
        assert set(["task_id", "video_id", "status"]).issubset(env)
        assert env["status"] == "queued"
        tid = env["task_id"]
        # worker_loop is running via lifespan; poll
        import time

        result = None
        for _ in range(50):
            g = client.get(f"/api/v1/analyses/{tid}")
            assert g.status_code == 200
            body = g.json()
            if body["status"] in ("completed", "failed"):
                result = body
                break
            time.sleep(0.05)
        assert result is not None
        assert result["status"] == "completed"
        assert result["result"] is not None
        assert result["result"]["schema_version"] == "1.0.0"


def test_no_talents_endpoint():
    client, _ = _fresh_app_client()
    with client:
        r = client.get("/api/v1/talents")
        assert r.status_code in (404, 405)


def test_list_analyses_history():
    client, _ = _fresh_app_client()
    with client:
        empty = client.get("/api/v1/analyses")
        assert empty.status_code == 200
        assert empty.json() == {"items": [], "count": 0}

        r = client.post(
            "/api/v1/analyses",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
        assert r.status_code == 202
        tid = r.json()["task_id"]
        import time

        done = None
        for _ in range(50):
            g = client.get(f"/api/v1/analyses/{tid}")
            assert g.status_code == 200
            body = g.json()
            if body["status"] in ("completed", "failed"):
                done = body
                break
            time.sleep(0.05)
        assert done is not None
        assert done["status"] == "completed"

        db = db_mod.SessionLocal()
        try:
            fail = AnalysisTask(
                id="failed-hist-1",
                video_id="dQw4w9WgXcQ",
                status="failed",
                current_step=1,
                current_step_label="拉取元数据",
                cache_hit=0,
                budget_exceeded=0,
                billed=1,
                error_code="video_unavailable",
                error_message="视频不存在或不可公开访问",
                created_at="2099-01-01T00:00:00Z",
            )
            db.add(fail)
            db.commit()
        finally:
            db.close()

        lst = client.get("/api/v1/analyses")
        assert lst.status_code == 200
        payload = lst.json()
        assert payload["count"] >= 2
        items = payload["items"]
        assert items[0]["task_id"] == "failed-hist-1"
        assert items[0]["status"] == "failed"
        assert items[0]["summary"] == "视频不存在或不可公开访问"
        done_item = next(i for i in items if i["task_id"] == tid)
        assert done_item["status"] == "completed"
        assert done_item["canonical_url"]
        assert done_item["title"]
        assert isinstance(done_item["video_types"], list)
        assert "summary" in done_item

        missing = client.get("/api/v1/analyses/not-a-real-task")
        assert missing.status_code == 404
