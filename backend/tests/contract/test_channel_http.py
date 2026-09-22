from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
from app.models.channel_analysis_task import ChannelAnalysisTask
from tests.conftest import make_test_db_url


def test_invalid_channel_url_no_insert():
    init_db(make_test_db_url(), static_pool=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/v1/channel-analyses", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_channel_url"
        assert "不创建任务" in r.json()["message"]


def test_post_channel_returns_202_queued_without_sampling():
    """POST must return 202 immediately; sampling runs in channel_worker, not request."""
    init_db(make_test_db_url(), static_pool=True)
    with patch("app.api.routes.enqueue_channel", new_callable=AsyncMock) as enq:
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/api/v1/channel-analyses",
                json={"url": "https://www.youtube.com/@DemoChannel"},
            )
            assert r.status_code == 202
            body = r.json()
            assert body.get("task_id")
            assert body.get("status") == "queued"
            assert body.get("result") is None
            assert body.get("current_step") == 0
            assert body.get("current_step_label") == "解析频道"
            enq.assert_awaited_once_with(body["task_id"])

            # Row exists as queued; no sample yet
            from app import db as db_mod

            db = db_mod.SessionLocal()
            try:
                row = db.get(ChannelAnalysisTask, body["task_id"])
                assert row is not None
                assert row.status == "queued"
                assert row.result_json is None
                assert row.sample_video_ids_json is None
            finally:
                db.close()
