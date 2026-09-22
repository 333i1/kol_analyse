
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.db import init_db
from app import db as db_mod
from app.main import app
from app.models.analysis_task import AnalysisTask
from app.services.llm_client import LLMClient, LLMError
from app.services.worker import WorkerDeps, set_deps
from tests.conftest import make_test_db_url
from tests.helpers import (
    COMMENTS_MOCK,
    CONTENT_MOCK,
    FakeTranscript,
    FakeWhisper,
    FakeYoutube,
)


class CountingLLM(LLMClient):
    def complete(self, **kwargs):
        raise AssertionError("LLM should not be called when mock_response is set")


class StageLLM:
    """Record LLM stages; optionally fail content and/or comments."""

    def __init__(self, *, fail_comments: bool = False, fail_content: bool = False, allow_content: bool = False):
        self.stages: list[str] = []
        self.fail_comments = fail_comments
        self.fail_content = fail_content
        self.allow_content = allow_content

    def complete(self, **kwargs):
        stage = kwargs["stage"]
        self.stages.append(stage)
        if stage == "llm_content":
            if self.fail_content:
                raise LLMError("ReadTimeout")
            if not self.allow_content:
                raise AssertionError("llm_content must not be called")
            return CONTENT_MOCK
        if stage == "llm_comments":
            if self.fail_comments:
                raise LLMError("ReadTimeout")
            return COMMENTS_MOCK
        raise AssertionError(f"unexpected stage {stage}")


def _wait(client, tid):
    for _ in range(80):
        body = client.get(f"/api/v1/analyses/{tid}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_second_post_is_cache_hit():
    init_db(make_test_db_url(), static_pool=False)
    llm = CountingLLM()
    deps = WorkerDeps(
        youtube=FakeYoutube(),
        transcript=FakeTranscript(ok=True),
        whisper=FakeWhisper(enabled=False),
        llm=llm,
        skip_audio=True,
        content_llm_mock=CONTENT_MOCK,
        comments_llm_mock=COMMENTS_MOCK,
    )
    set_deps(deps)
    with TestClient(app) as client:
        r1 = client.post(
            "/api/v1/analyses",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
        assert r1.status_code == 202
        _wait(client, r1.json()["task_id"])
        db = db_mod.SessionLocal()
        try:
            billed_before = db.query(AnalysisTask).filter(AnalysisTask.billed == 1).count()
        finally:
            db.close()
        llm.call_count = 0
        r2 = client.post(
            "/api/v1/analyses", json={"url": "https://youtu.be/dQw4w9WgXcQ"}
        )
        assert r2.status_code == 200
        assert r2.json()["status"] == "cache_hit"
        assert r2.json()["cache_hit"] is True
        assert r2.json()["result"] is not None
        db = db_mod.SessionLocal()
        try:
            billed_after = db.query(AnalysisTask).filter(AnalysisTask.billed == 1).count()
        finally:
            db.close()
        assert billed_after == billed_before
        # GET completed does not call LLM
        g = client.get(f"/api/v1/analyses/{r1.json()['task_id']}")
        assert g.status_code == 200
        assert llm.call_count == 0


def test_degraded_comments_not_full_cache_hit_reruns_comments_only():
    init_db(make_test_db_url(), static_pool=False)
    llm = StageLLM(fail_comments=True)
    yt = FakeYoutube()
    deps = WorkerDeps(
        youtube=yt,
        transcript=FakeTranscript(ok=True),
        whisper=FakeWhisper(enabled=False),
        llm=llm,
        skip_audio=True,
        content_llm_mock=CONTENT_MOCK,
        comments_llm_mock=None,
    )
    set_deps(deps)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    with TestClient(app) as client:
        r1 = client.post("/api/v1/analyses", json={"url": url})
        assert r1.status_code == 202
        body1 = _wait(client, r1.json()["task_id"])
        assert body1["status"] == "completed"
        assert body1["result"]["content_analysis"]["pipeline_status"] == "ok"
        assert body1["result"]["comment_analysis"]["pipeline_status"] == "failed"
        assert "ReadTimeout" in body1["result"]["comment_analysis"]["noise"]["label"]
        comments_calls_after_first = yt.comment_calls

        llm.stages.clear()
        llm.fail_comments = False
        deps.content_llm_mock = None

        r2 = client.post("/api/v1/analyses", json={"url": url})
        assert r2.status_code == 202
        assert r2.json()["cache_hit"] is False
        body2 = _wait(client, r2.json()["task_id"])
        assert body2["status"] == "completed"
        assert body2["cache_hit"] is False
        assert body2["result"]["task"]["cache_hit"] is False
        assert body2["result"]["comment_analysis"]["pipeline_status"] == "ok"
        assert body2["result"]["content_analysis"]["pipeline_status"] == "ok"
        assert (
            body2["result"]["content_analysis"]["content_conclusion"]["thesis"]
            == body1["result"]["content_analysis"]["content_conclusion"]["thesis"]
        )
        assert "llm_content" not in llm.stages
        assert "llm_comments" in llm.stages
        assert yt.comment_calls == comments_calls_after_first


def test_degraded_content_not_full_cache_hit_reruns_content_only():
    init_db(make_test_db_url(), static_pool=False)
    llm = StageLLM(fail_content=True, allow_content=True)
    yt = FakeYoutube()
    deps = WorkerDeps(
        youtube=yt,
        transcript=FakeTranscript(ok=True),
        whisper=FakeWhisper(enabled=False),
        llm=llm,
        skip_audio=True,
        content_llm_mock=None,
        comments_llm_mock=None,
    )
    set_deps(deps)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    with TestClient(app) as client:
        r1 = client.post("/api/v1/analyses", json={"url": url})
        assert r1.status_code == 202
        body1 = _wait(client, r1.json()["task_id"])
        assert body1["status"] == "completed"
        assert body1["result"]["content_analysis"]["pipeline_status"] == "failed"
        assert body1["result"]["comment_analysis"]["pipeline_status"] == "ok"
        comments_calls_after_first = yt.comment_calls

        llm.stages.clear()
        llm.fail_content = False

        r2 = client.post("/api/v1/analyses", json={"url": url})
        assert r2.status_code == 202
        assert r2.json()["cache_hit"] is False
        body2 = _wait(client, r2.json()["task_id"])
        assert body2["status"] == "completed"
        assert body2["cache_hit"] is False
        assert body2["result"]["task"]["cache_hit"] is False
        assert body2["result"]["content_analysis"]["pipeline_status"] == "ok"
        assert body2["result"]["comment_analysis"]["pipeline_status"] == "ok"
        assert (
            body2["result"]["comment_analysis"]["comment_conclusion"]["doing"]
            == body1["result"]["comment_analysis"]["comment_conclusion"]["doing"]
        )
        assert "llm_content" in llm.stages
        assert "llm_comments" not in llm.stages
        assert yt.comment_calls == comments_calls_after_first


def test_dual_fail_second_post_reruns_both_pipelines():
    init_db(make_test_db_url(), static_pool=False)
    llm = StageLLM(fail_content=True, fail_comments=True, allow_content=True)
    deps = WorkerDeps(
        youtube=FakeYoutube(),
        transcript=FakeTranscript(ok=True),
        whisper=FakeWhisper(enabled=False),
        llm=llm,
        skip_audio=True,
        content_llm_mock=None,
        comments_llm_mock=None,
    )
    set_deps(deps)
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    with TestClient(app) as client:
        r1 = client.post("/api/v1/analyses", json={"url": url})
        assert r1.status_code == 202
        body1 = _wait(client, r1.json()["task_id"])
        assert body1["status"] == "failed"
        assert body1["result"]["content_analysis"]["pipeline_status"] == "failed"
        assert body1["result"]["comment_analysis"]["pipeline_status"] == "failed"

        llm.stages.clear()
        llm.fail_content = False
        llm.fail_comments = False

        r2 = client.post("/api/v1/analyses", json={"url": url})
        assert r2.status_code == 202
        assert r2.json()["cache_hit"] is False
        body2 = _wait(client, r2.json()["task_id"])
        assert body2["status"] == "completed"
        assert body2["cache_hit"] is False
        assert body2["result"]["content_analysis"]["pipeline_status"] == "ok"
        assert body2["result"]["comment_analysis"]["pipeline_status"] == "ok"
        assert "llm_content" in llm.stages
        assert "llm_comments" in llm.stages
