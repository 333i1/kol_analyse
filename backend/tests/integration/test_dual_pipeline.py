
from __future__ import annotations

import json
import time
from pathlib import Path

import jsonschema
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
from app.services.worker import WorkerDeps, set_deps
from tests.conftest import make_test_db_url
from tests.helpers import (
    COMMENTS_MOCK,
    CONTENT_MOCK,
    FakeTranscript,
    FakeWhisper,
    FakeYoutube,
)

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "02-施工"
        / "schema"
        / "video-analysis-result.schema.json"
    ).read_text(encoding="utf-8")
)


def test_dual_pipeline_schema_valid_five_zone():
    init_db(make_test_db_url(), static_pool=False)
    deps = WorkerDeps(
        youtube=FakeYoutube(),
        transcript=FakeTranscript(ok=True),
        whisper=FakeWhisper(enabled=False),
        skip_audio=True,
        content_llm_mock=CONTENT_MOCK,
        comments_llm_mock=COMMENTS_MOCK,
    )
    set_deps(deps)
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/analyses",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
        assert r.status_code == 202
        tid = r.json()["task_id"]
        body = None
        for _ in range(80):
            g = client.get(f"/api/v1/analyses/{tid}")
            body = g.json()
            if body["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert body["status"] == "completed"
        result = body["result"]
        jsonschema.validate(result, SCHEMA)
        assert result["result_version"].startswith("schema1.0.0+content-v1+comments-v1.2+")
        segs = result["content_analysis"]["segments"]
        assert [s["kind"] for s in segs] == ["开场", "展开", "收束"]
        assert "selling_points" not in json.dumps(result)
        assert result["content_analysis"]["content_conclusion"]["commercial"] is None
        for s in segs:
            if s["kind"] != "收束":
                assert s["cta_type"] is None
        assert result["is_fixture"] is False


def test_comment_themes_list_assembled_schema_valid():
    init_db(make_test_db_url(), static_pool=False)
    mock = {
        "items": COMMENTS_MOCK["items"],
        "comment_conclusion": {
            "doing": "提问报错为主",
            "themes": ["火候", "节奏"],
            "alignment": "视频教步骤，评论要排障",
            "confidence": 1.2,
        },
    }
    deps = WorkerDeps(
        youtube=FakeYoutube(),
        transcript=FakeTranscript(ok=True),
        whisper=FakeWhisper(enabled=False),
        skip_audio=True,
        content_llm_mock=CONTENT_MOCK,
        comments_llm_mock=mock,
    )
    set_deps(deps)
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/analyses",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
        assert r.status_code == 202
        tid = r.json()["task_id"]
        body = None
        for _ in range(80):
            body = client.get(f"/api/v1/analyses/{tid}").json()
            if body["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert body["status"] == "completed"
        result = body["result"]
        assert result is not None
        jsonschema.validate(result, SCHEMA)
        themes = result["comment_analysis"]["comment_conclusion"]["themes"]
        assert isinstance(themes, str)
        assert "火候" in themes
        assert 0.0 <= result["comment_analysis"]["comment_conclusion"]["confidence"] <= 1.0

