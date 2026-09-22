from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import db as db_mod
from app.db import init_db
from app.main import app
from app.models.analysis_task import AnalysisTask
from app.models.cost_ledger import CostLedgerEntry
from app.models.video import Video
from app.services.budget import BudgetExceededError
from app.services.llm_client import LLMClient
from app.services.worker import WorkerDeps, _run_pipeline_isolated, set_deps
from tests.conftest import make_test_db_url
from tests.helpers import (
    COMMENTS_MOCK,
    CONTENT_MOCK,
    FakeTranscript,
    FakeWhisper,
    FakeYoutube,
    VID,
)


def _settings(**kwargs):
    base = dict(
        LLM_API_KEY="test-key",
        LLM_BASE_URL="https://example.invalid/v1",
        LLM_MODEL="glm-test",
        LLM_TIMEOUT=90,
        LLM_TEMPERATURE=0.2,
        LLM_INTER_CALL_DELAY_S=1.5,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _ok_resp(payload: dict):
    class Resp:
        status_code = 200
        text = "{}"

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(payload, ensure_ascii=False),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }

    return Resp()


def _seed_task(task_id: str = "task-t069"):
    db = db_mod.SessionLocal()
    try:
        db.add(
            Video(
                video_id=VID,
                platform="youtube",
                canonical_url=f"https://www.youtube.com/watch?v={VID}",
                created_at="2026-09-01T00:00:00Z",
                updated_at="2026-09-01T00:00:00Z",
            )
        )
        db.flush()
        t = AnalysisTask(
            id=task_id,
            video_id=VID,
            status="analyzing",
            current_step=3,
            current_step_label="内容拆解",
            created_at="2026-09-01T00:00:00Z",
        )
        db.add(t)
        db.commit()
        return t
    finally:
        db.close()


def test_file_sqlite_wal_and_busy_timeout():
    init_db(make_test_db_url(), static_pool=False)
    engine = db_mod.get_engine()
    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        timeout = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert str(mode).lower() == "wal"
    assert int(timeout) == 5000


def test_parallel_pipelines_use_distinct_sessions():
    init_db(make_test_db_url(), static_pool=False)
    seen: list[int] = []
    barrier = threading.Barrier(2)

    def _fn(*, db, name: str):
        seen.append(id(db))
        barrier.wait(timeout=2)
        return name

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_run_pipeline_isolated, _fn, name="content")
        f2 = pool.submit(_run_pipeline_isolated, _fn, name="comments")
        assert {f1.result(), f2.result()} == {"content", "comments"}
    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_parallel_call_count_not_both_first(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("app.services.llm_client.budget_mod.check_or_stop", lambda *a, **k: None)
    monkeypatch.setattr("app.services.llm_client.budget_mod.estimate_llm", lambda *a, **k: 0.0)
    monkeypatch.setattr("app.services.llm_client.budget_mod.record", lambda *a, **k: None)
    monkeypatch.setattr("app.services.llm_client.time.sleep", lambda s: sleeps.append(s))

    barrier = threading.Barrier(2)

    class Http:
        def post(self, url, headers=None, json=None):
            barrier.wait(timeout=2)
            return _ok_resp({"hello": 1})

    client = LLMClient(settings=_settings(), http=Http())
    errors: list[BaseException] = []

    def _go(stage: str) -> None:
        try:
            client.complete(
                db=None,
                task_id="t",
                video_id=VID,
                stage=stage,
                system_prompt="s",
                user_prompt="u",
            )
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    t1 = threading.Thread(target=_go, args=("llm_content",))
    t2 = threading.Thread(target=_go, args=("llm_comments",))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not errors
    assert client.call_count == 2
    assert sleeps == [1.5]


def test_parallel_budget_not_both_first(monkeypatch):
    init_db(make_test_db_url(), static_pool=False)
    _seed_task()
    monkeypatch.setattr("app.services.llm_client.budget_mod.estimate_llm", lambda *a, **k: 0.06)

    class Http:
        def post(self, url, headers=None, json=None):
            time.sleep(0.05)
            return _ok_resp({"hello": 1})

    llm = LLMClient(settings=_settings(), http=Http())
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def _go(stage: str) -> None:
        sess = db_mod.SessionLocal()
        try:
            try:
                llm.complete(
                    db=sess,
                    task_id="task-t069",
                    video_id=VID,
                    stage=stage,
                    system_prompt="s",
                    user_prompt="u",
                )
                outcomes.append("ok")
            except BudgetExceededError:
                outcomes.append("exceeded")
        except BaseException as e:  # pragma: no cover
            errors.append(e)
        finally:
            sess.close()

    t1 = threading.Thread(target=_go, args=("llm_content",))
    t2 = threading.Thread(target=_go, args=("llm_comments",))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not errors, errors
    assert outcomes.count("ok") == 1
    assert outcomes.count("exceeded") == 1
    db = db_mod.SessionLocal()
    try:
        rows = db.query(CostLedgerEntry).filter(CostLedgerEntry.task_id == "task-t069").all()
        assert len(rows) == 1
    finally:
        db.close()


class _SlowStageHttp:
    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def post(self, url, headers=None, json=None):
        time.sleep(0.08)
        with self._lock:
            self.calls += 1
        user = ""
        for m in (json or {}).get("messages") or []:
            if m.get("role") == "user":
                user = m.get("content") or ""
        payload = COMMENTS_MOCK if "评论列表" in user else CONTENT_MOCK
        return _ok_resp(payload)


def test_dual_pipeline_parallel_no_database_locked():
    """T069: content+comments billed in parallel must not raise database is locked."""
    init_db(make_test_db_url(), static_pool=False)
    http = _SlowStageHttp()
    llm = LLMClient(settings=_settings(), http=http)
    set_deps(
        WorkerDeps(
            youtube=FakeYoutube(),
            transcript=FakeTranscript(ok=True),
            whisper=FakeWhisper(enabled=False),
            llm=llm,
            skip_audio=True,
            content_llm_mock=None,
            comments_llm_mock=None,
        )
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(
            "/api/v1/analyses",
            json={"url": f"https://www.youtube.com/watch?v={VID}"},
        )
        assert r.status_code == 202
        tid = r.json()["task_id"]
        body = None
        for _ in range(120):
            body = client.get(f"/api/v1/analyses/{tid}").json()
            if body["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert body is not None
        assert body["status"] == "completed", body
        err = (body.get("error_message") or "") + (body.get("error_code") or "")
        assert "database is locked" not in err.lower()
        assert "locked" not in err.lower()
    assert http.calls == 2
    assert llm.call_count == 2
    db = db_mod.SessionLocal()
    try:
        rows = db.query(CostLedgerEntry).filter(CostLedgerEntry.task_id == tid).all()
        stages = sorted(r.stage for r in rows)
        assert stages == ["llm_comments", "llm_content"]
        task = db.get(AnalysisTask, tid)
        assert task is not None
        assert task.status == "completed"
        assert not (task.error_message or "")
    finally:
        db.close()
