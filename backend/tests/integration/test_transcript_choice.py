
from __future__ import annotations

import time

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
    VID,
)

URL = f"https://www.youtube.com/watch?v={VID}"
SKIP_NOTE = "运营选择跳过语音转录，内容仅基于标题/简介"


def _wait_status(client, tid, *, ticks: int = 80):
    body = None
    for _ in range(ticks):
        body = client.get(f"/api/v1/analyses/{tid}").json()
        if body.get("awaiting") == "transcript_choice":
            return body
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"timeout waiting for {tid}: {body}")


def _wait_done(client, tid, *, ticks: int = 80):
    body = None
    for _ in range(ticks):
        body = client.get(f"/api/v1/analyses/{tid}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"timeout waiting done for {tid}: {body}")


def _deps(*, transcript_ok=False, whisper_ok=True, whisper_enabled=True, skip_audio=False, youtube=None, whisper=None):
    return WorkerDeps(
        youtube=youtube or FakeYoutube(),
        transcript=FakeTranscript(ok=transcript_ok, error="ParseError: boom"),
        whisper=whisper or FakeWhisper(ok=whisper_ok, enabled=whisper_enabled),
        skip_audio=skip_audio,
        content_llm_mock=CONTENT_MOCK,
        comments_llm_mock=COMMENTS_MOCK,
    )


def test_captions_fail_whisper_enabled_pauses_without_transcribe():
    init_db(make_test_db_url(), static_pool=False)
    whisper = FakeWhisper(ok=True, enabled=True)
    set_deps(_deps(transcript_ok=False, whisper=whisper, skip_audio=False))
    with TestClient(app) as client:
        r = client.post("/api/v1/analyses", json={"url": URL})
        assert r.status_code == 202
        tid = r.json()["task_id"]
        body = _wait_status(client, tid)
        assert body["status"] == "analyzing"
        assert body.get("awaiting") == "transcript_choice"
        assert whisper.transcribe_calls == 0
        assert "等待" in (body.get("current_step_label") or "")


def test_post_skip_true_empty_segments_note():
    init_db(make_test_db_url(), static_pool=False)
    whisper = FakeWhisper(ok=True, enabled=True)
    set_deps(_deps(transcript_ok=False, whisper=whisper, skip_audio=True))
    with TestClient(app) as client:
        r = client.post("/api/v1/analyses", json={"url": URL})
        tid = r.json()["task_id"]
        paused = _wait_status(client, tid)
        assert paused.get("awaiting") == "transcript_choice"
        assert whisper.transcribe_calls == 0
        choice = client.post(
            f"/api/v1/analyses/{tid}/transcript-choice",
            json={"skip": True},
        )
        assert choice.status_code in (200, 202)
        done = _wait_done(client, tid)
        assert done["status"] == "completed"
        ca = done["result"]["content_analysis"]
        assert ca["segments"] == []
        assert ca.get("content_conclusion")
        assert ca["content_conclusion"]["commercial"] is None
        assert ca.get("skip_reason") == "user_skipped_stt"
        assert ca["pipeline_status"] == "degraded"
        notes = done["result"]["health"]["notes"]
        assert any("跳过" in n for n in notes)
        assert SKIP_NOTE in notes
        assert done["result"]["health"]["transcript"]["mode"] == "none"
        assert done["result"]["health"]["transcript"]["status"] in ("failed", "degraded")
        assert whisper.transcribe_calls == 0


def test_post_skip_false_calls_transcribe_three_segments():
    init_db(make_test_db_url(), static_pool=False)
    whisper = FakeWhisper(ok=True, enabled=True)
    set_deps(_deps(transcript_ok=False, whisper=whisper, skip_audio=True))
    with TestClient(app) as client:
        r = client.post("/api/v1/analyses", json={"url": URL})
        tid = r.json()["task_id"]
        paused = _wait_status(client, tid)
        assert paused.get("awaiting") == "transcript_choice"
        assert whisper.transcribe_calls == 0
        choice = client.post(
            f"/api/v1/analyses/{tid}/transcript-choice",
            json={"skip": False},
        )
        assert choice.status_code in (200, 202)
        done = _wait_done(client, tid)
        assert done["status"] == "completed"
        assert whisper.transcribe_calls >= 1
        segs = done["result"]["content_analysis"]["segments"]
        assert len(segs) == 3
        assert [s["kind"] for s in segs] == ["开场", "展开", "收束"]
        assert done["result"]["health"]["transcript"]["mode"] == "speech_to_text"


def test_captions_ok_never_awaiting():
    init_db(make_test_db_url(), static_pool=False)
    whisper = FakeWhisper(ok=True, enabled=True)
    set_deps(_deps(transcript_ok=True, whisper=whisper, skip_audio=True))
    with TestClient(app) as client:
        r = client.post("/api/v1/analyses", json={"url": URL})
        tid = r.json()["task_id"]
        done = _wait_done(client, tid)
        assert done["status"] == "completed"
        assert done.get("awaiting") in (None, "")
        assert "awaiting" not in done or done.get("awaiting") is None
        assert whisper.transcribe_calls == 0
        segs = done["result"]["content_analysis"]["segments"]
        assert len(segs) == 3
        choice = client.post(
            f"/api/v1/analyses/{tid}/transcript-choice",
            json={"skip": True},
        )
        assert choice.status_code == 409


def test_choice_unknown_and_not_awaiting():
    init_db(make_test_db_url(), static_pool=False)
    set_deps(_deps(transcript_ok=True, whisper_enabled=False, skip_audio=True))
    with TestClient(app) as client:
        missing = client.post(
            "/api/v1/analyses/not-a-real-task/transcript-choice",
            json={"skip": True},
        )
        assert missing.status_code == 404
        r = client.post("/api/v1/analyses", json={"url": URL})
        tid = r.json()["task_id"]
        done = _wait_done(client, tid)
        assert done["status"] in ("completed", "failed")
        conflict = client.post(
            f"/api/v1/analyses/{tid}/transcript-choice",
            json={"skip": False},
        )
        assert conflict.status_code == 409


def test_same_url_post_while_awaiting_returns_202_same_task():
    init_db(make_test_db_url(), static_pool=False)
    whisper = FakeWhisper(ok=True, enabled=True)
    set_deps(_deps(transcript_ok=False, whisper=whisper, skip_audio=False))
    with TestClient(app) as client:
        r = client.post("/api/v1/analyses", json={"url": URL})
        tid = r.json()["task_id"]
        paused = _wait_status(client, tid)
        assert paused.get("awaiting") == "transcript_choice"
        r2 = client.post("/api/v1/analyses", json={"url": URL})
        assert r2.status_code == 202
        body2 = r2.json()
        assert body2["task_id"] == tid
        assert body2["status"] == "analyzing"
        assert body2.get("awaiting") == "transcript_choice"
        again = client.get(f"/api/v1/analyses/{tid}").json()
        assert again.get("awaiting") == "transcript_choice"
        assert again["status"] == "analyzing"


def test_channel_child_auto_skips_stt_without_awaiting():
    """Child created via channel _ensure_child_task auto-resumes skip; no hang on awaiting."""
    from app import db as db_mod
    from app.services.channel_worker import _ensure_child_task
    from app.services.worker import is_awaiting_transcript_choice, run_task
    from app.worker.auto_skip_stt import consume_auto_skip_stt

    init_db(make_test_db_url(), static_pool=True)
    whisper = FakeWhisper(ok=True, enabled=True)
    deps = _deps(transcript_ok=False, whisper=whisper, skip_audio=True)
    set_deps(deps)

    assert db_mod.SessionLocal is not None
    db = db_mod.SessionLocal()
    try:
        task = _ensure_child_task(db, VID)
        db.commit()
        tid = task.id
    finally:
        db.close()

    run_task(tid, deps)

    assert is_awaiting_transcript_choice(tid) is False
    assert consume_auto_skip_stt(tid) is False  # already consumed
    assert whisper.transcribe_calls == 0

    db = db_mod.SessionLocal()
    try:
        from app.models.analysis_task import AnalysisTask
        from app.models.analysis_result import AnalysisResultRow
        import json

        task = db.get(AnalysisTask, tid)
        assert task is not None
        assert task.status == "completed"
        assert task.result_id
        row = db.get(AnalysisResultRow, task.result_id)
        assert row is not None
        result = json.loads(row.result_json)
        ca = result["content_analysis"]
        assert ca.get("skip_reason") == "user_skipped_stt"
        assert SKIP_NOTE in result["health"]["notes"]
        assert result["health"]["transcript"]["mode"] == "none"
    finally:
        db.close()


def test_ensure_child_inflight_does_not_mark_again():
    """Reusing an existing in-flight task must not re-mark auto-skip (single-video ownership)."""
    from app import db as db_mod
    from app.services.channel_worker import _ensure_child_task
    from app.worker.auto_skip_stt import clear_auto_skip_stt, consume_auto_skip_stt

    init_db(make_test_db_url(), static_pool=True)
    clear_auto_skip_stt()
    assert db_mod.SessionLocal is not None
    db = db_mod.SessionLocal()
    try:
        first = _ensure_child_task(db, VID)
        db.commit()
        assert consume_auto_skip_stt(first.id) is True
        # Consume cleared the flag; returning same inflight must not re-mark.
        second = _ensure_child_task(db, VID)
        assert second.id == first.id
        assert consume_auto_skip_stt(second.id) is False
    finally:
        db.close()
