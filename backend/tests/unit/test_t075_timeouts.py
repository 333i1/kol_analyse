from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from app import db as db_mod
from app.db import init_db
from app.main import app
from app.models.analysis_task import AnalysisTask
from app.services.cache import get_in_flight
from app.services.transcript_client import Cue, TranscriptResult
from app.services.worker import WorkerDeps, set_deps
from app.worker import util as worker_util
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


def _wait(client, tid, *, ticks: int = 80):
    body = None
    continued = False
    for _ in range(ticks):
        body = client.get(f"/api/v1/analyses/{tid}").json()
        if body.get("awaiting") == "transcript_choice" and not continued:
            client.post(f"/api/v1/analyses/{tid}/transcript-choice", json={"skip": False})
            continued = True
            time.sleep(0.05)
            continue
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"timeout waiting for {tid}: {body}")


def _session():
    assert db_mod.SessionLocal is not None
    return db_mod.SessionLocal()


class HungWhisper:
    def enabled(self):
        return True

    def transcribe(self, audio_file: str) -> TranscriptResult:
        threading.Event().wait()
        cues = [Cue(0, 10, "never")]
        return TranscriptResult(ok=True, cues=cues, text="never", language="zh")


class HungYoutube:
    def __init__(self):
        self.videos_list_calls = 0
        self.comment_calls = 0

    def videos_list(self, video_id: str):
        self.videos_list_calls += 1
        threading.Event().wait()
        return {}

    def comment_threads(self, video_id: str, *, max_comments: int = 200):
        self.comment_calls += 1
        return []


def test_timeout_constants_locked():
    assert worker_util.YDL_AUDIO_TIMEOUT_S == 120.0
    assert worker_util.WHISPER_TRANSCRIBE_TIMEOUT_S == 300.0
    assert worker_util.RUN_TASK_TIMEOUT_S == 720.0


def test_hung_audio_download_times_out_and_degrades(monkeypatch):
    monkeypatch.setattr(worker_util, "YDL_AUDIO_TIMEOUT_S", 0.2)
    init_db(make_test_db_url(), static_pool=False)

    def _hang(_vid: str) -> str:
        threading.Event().wait()
        return "never.wav"

    set_deps(
        WorkerDeps(
            youtube=FakeYoutube(),
            transcript=FakeTranscript(ok=False, error="ParseError: boom"),
            whisper=FakeWhisper(ok=True, enabled=True),
            audio_fetch_fn=_hang,
            skip_audio=False,
            content_llm_mock=CONTENT_MOCK,
            comments_llm_mock=COMMENTS_MOCK,
        )
    )
    t0 = time.monotonic()
    with TestClient(app) as client:
        r = client.post("/api/v1/analyses", json={"url": URL})
        assert r.status_code == 202
        body = _wait(client, r.json()["task_id"], ticks=120)
        elapsed = time.monotonic() - t0
        assert elapsed < 8, elapsed
        assert body["status"] in ("completed", "failed")
        result = body.get("result")
        if result:
            assert result["health"]["transcript"]["mode"] in ("none", "speech_to_text")
            assert result["health"]["source"]["level"] in ("degraded", "failed")
            notes = " ".join(result["health"]["notes"])
            assert "TimeoutError" in notes or result["health"]["transcript"]["mode"] == "none"


def test_hung_whisper_times_out_and_degrades(monkeypatch):
    monkeypatch.setattr(worker_util, "WHISPER_TRANSCRIBE_TIMEOUT_S", 0.2)
    init_db(make_test_db_url(), static_pool=False)
    set_deps(
        WorkerDeps(
            youtube=FakeYoutube(),
            transcript=FakeTranscript(ok=False, error="ParseError: boom"),
            whisper=HungWhisper(),
            skip_audio=True,
            content_llm_mock=CONTENT_MOCK,
            comments_llm_mock=COMMENTS_MOCK,
        )
    )
    t0 = time.monotonic()
    with TestClient(app) as client:
        r = client.post("/api/v1/analyses", json={"url": URL})
        assert r.status_code == 202
        body = _wait(client, r.json()["task_id"], ticks=120)
        elapsed = time.monotonic() - t0
        assert elapsed < 8, elapsed
        assert body["status"] in ("completed", "failed")
        result = body.get("result")
        if result:
            assert result["health"]["transcript"]["mode"] == "none"
            notes = " ".join(result["health"]["notes"])
            assert "TimeoutError" in notes


def test_run_task_hard_timeout_releases_analysis_lock(monkeypatch):
    monkeypatch.setattr(worker_util, "RUN_TASK_TIMEOUT_S", 0.25)
    init_db(make_test_db_url(), static_pool=False)
    hung = HungYoutube()
    set_deps(
        WorkerDeps(
            youtube=hung,
            transcript=FakeTranscript(ok=True),
            whisper=FakeWhisper(enabled=False),
            skip_audio=True,
            content_llm_mock=CONTENT_MOCK,
            comments_llm_mock=COMMENTS_MOCK,
        )
    )
    t0 = time.monotonic()
    with TestClient(app) as client:
        r1 = client.post("/api/v1/analyses", json={"url": URL})
        assert r1.status_code == 202
        tid1 = r1.json()["task_id"]
        body1 = _wait(client, tid1, ticks=120)
        elapsed = time.monotonic() - t0
        assert elapsed < 8, elapsed
        assert body1["status"] == "failed"
        db = _session()
        try:
            row = db.get(AnalysisTask, tid1)
            assert row is not None
            assert row.status == "failed"
            assert row.error_code
            assert get_in_flight(db, VID) is None
        finally:
            db.close()

        monkeypatch.setattr(worker_util, "RUN_TASK_TIMEOUT_S", 720.0)
        set_deps(
            WorkerDeps(
                youtube=FakeYoutube(),
                transcript=FakeTranscript(ok=True),
                whisper=FakeWhisper(enabled=False),
                skip_audio=True,
                content_llm_mock=CONTENT_MOCK,
                comments_llm_mock=COMMENTS_MOCK,
            )
        )
        r2 = client.post("/api/v1/analyses", json={"url": URL})
        assert r2.status_code == 202
        body2 = _wait(client, r2.json()["task_id"], ticks=120)
        assert body2["status"] == "completed"
