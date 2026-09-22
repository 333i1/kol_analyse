from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import db as db_mod
from app.db import init_db
from app.main import app
from app.models.analysis_task import AnalysisTask
from app.models.video import Video
from app.services import worker as worker_mod
from app.services.cache import get_in_flight
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


class CountingLLM:
    """Record billed complete() calls; return canned pipeline JSON."""

    def __init__(self):
        self.call_count = 0
        self.stages: list[str] = []
        self._lock = threading.Lock()

    def complete(self, **kwargs):
        with self._lock:
            self.call_count += 1
            self.stages.append(kwargs.get("stage") or "")
        if kwargs.get("stage") == "llm_comments":
            return COMMENTS_MOCK
        return CONTENT_MOCK


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _insert_video_and_task(*, status: str, task_id: str | None = None) -> str:
    now = _now()
    tid = task_id or str(uuid.uuid4())
    db = _session()
    try:
        if db.get(Video, VID) is None:
            db.add(
                Video(
                    video_id=VID,
                    platform="youtube",
                    canonical_url=URL,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.flush()
        analyzing = status == "analyzing"
        db.add(
            AnalysisTask(
                id=tid,
                video_id=VID,
                status=status,
                current_step=1 if analyzing else 0,
                current_step_label="拉取元数据" if analyzing else "校验 URL",
                cache_hit=0,
                budget_exceeded=0,
                billed=1,
                created_at=now,
                started_at=now if analyzing else None,
            )
        )
        db.commit()
        return tid
    finally:
        db.close()


def _deps(llm=None, youtube=None) -> WorkerDeps:
    return WorkerDeps(
        youtube=youtube or FakeYoutube(),
        transcript=FakeTranscript(ok=True),
        whisper=FakeWhisper(enabled=False),
        llm=llm or CountingLLM(),
        skip_audio=True,
        content_llm_mock=None,
        comments_llm_mock=None,
    )


def test_uncaught_exception_in_run_task_marks_failed_and_clears_inflight():
    """07-A.1: uncaught run_task error → failed + clear in-flight + same URL can POST again."""
    init_db(make_test_db_url(), static_pool=False)
    llm = CountingLLM()
    set_deps(_deps(llm))
    boom = threading.Event()
    orig_set_step = worker_mod._set_step

    def _boom_after_analyzing_committed(db, task, step):
        orig_set_step(db, task, step)
        if step == 1:
            boom.set()
            raise RuntimeError("uncaught boom in run_task")

    worker_mod._set_step = _boom_after_analyzing_committed
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post("/api/v1/analyses", json={"url": URL})
            assert r.status_code == 202
            tid = r.json()["task_id"]
            assert boom.wait(timeout=8), "run_task never reached analyzing"
            time.sleep(0.15)
            db = _session()
            try:
                row = db.get(AnalysisTask, tid)
                assert row is not None
                assert row.status == "failed"
                assert row.error_code
                assert get_in_flight(db, VID) is None
            finally:
                db.close()

            worker_mod._set_step = orig_set_step
            r2 = client.post("/api/v1/analyses", json={"url": URL})
            assert r2.status_code in (200, 202)
            body2 = r2.json()
            if r2.status_code == 200:
                assert body2["status"] == "cache_hit"
            else:
                done = _wait(client, body2["task_id"], ticks=120)
                assert done["status"] in ("completed", "failed")
    finally:
        worker_mod._set_step = orig_set_step


def test_startup_recovery_leftover_analyzing_interrupted_no_llm():
    """07-A.2: leftover analyzing on lifespan → failed + interrupted, LLM 0."""
    init_db(make_test_db_url(), static_pool=False)
    tid = _insert_video_and_task(status="analyzing")
    llm = CountingLLM()
    yt = FakeYoutube()
    set_deps(
        WorkerDeps(
            youtube=yt,
            transcript=FakeTranscript(ok=True),
            whisper=FakeWhisper(enabled=False),
            llm=llm,
            skip_audio=True,
            content_llm_mock=CONTENT_MOCK,
            comments_llm_mock=COMMENTS_MOCK,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/healthz").status_code == 200
        db = _session()
        try:
            row = db.get(AnalysisTask, tid)
            assert row is not None
            assert row.status == "failed"
            assert row.error_code == "interrupted"
        finally:
            db.close()
        assert llm.call_count == 0
        assert yt.videos_list_calls == 0


def test_startup_recovery_leftover_queued_reaches_terminal():
    """07-A.3: leftover queued on lifespan → eventually completed|failed, not stuck queued."""
    init_db(make_test_db_url(), static_pool=False)
    tid = _insert_video_and_task(status="queued")
    set_deps(_deps())
    with TestClient(app, raise_server_exceptions=False) as client:
        body = None
        for _ in range(120):
            body = client.get(f"/api/v1/analyses/{tid}").json()
            if body["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert body is not None
        assert body["status"] in ("completed", "failed"), body


def test_concurrent_same_url_at_most_one_billed_analyzing():
    """07-A.4: two POSTs of the same URL → at most one billed analyzing / one LLM bill."""
    import app.api.routes as routes_mod

    init_db(make_test_db_url(), static_pool=False)
    llm = CountingLLM()
    set_deps(_deps(llm))
    orig_gif = routes_mod.get_in_flight
    barrier = threading.Barrier(2)

    def _racing_get_in_flight(db, video_id):
        # Widen the check-then-insert window so both POSTs can observe empty in-flight.
        try:
            barrier.wait(timeout=2.0)
        except threading.BrokenBarrierError:
            pass
        return orig_gif(db, video_id)

    routes_mod.get_in_flight = _racing_get_in_flight
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            responses: list = [None, None]
            errors: list = []

            def _post(idx: int) -> None:
                try:
                    responses[idx] = client.post("/api/v1/analyses", json={"url": URL})
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            threads = [threading.Thread(target=_post, args=(i,)) for i in (0, 1)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            assert not errors
            r1, r2 = responses
            assert r1 is not None and r2 is not None
            assert r1.status_code == 202
            assert r2.status_code == 202
            tids = {r1.json()["task_id"], r2.json()["task_id"]}
            for tid in tids:
                done = _wait(client, tid, ticks=120)
                assert done["status"] in ("completed", "failed")
            db = _session()
            try:
                rows = db.query(AnalysisTask).filter(AnalysisTask.video_id == VID).all()
                billed_entered_analyzing = [
                    t
                    for t in rows
                    if t.billed == 1
                    and (t.started_at is not None or t.status == "analyzing")
                ]
                assert len(billed_entered_analyzing) <= 1, [
                    (t.id, t.status, t.started_at, t.billed) for t in rows
                ]
            finally:
                db.close()
            billed_stages = [s for s in llm.stages if s in ("llm_content", "llm_comments")]
            assert llm.call_count <= 2
            assert len(billed_stages) <= 2
    finally:
        routes_mod.get_in_flight = orig_gif
