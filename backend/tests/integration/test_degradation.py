
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
)

NOTE = "字幕接口失败，已降级语音转录"


def _wait(client, tid):
    body = None
    continued = False
    for _ in range(80):
        body = client.get(f"/api/v1/analyses/{tid}").json()
        if body.get("awaiting") == "transcript_choice" and not continued:
            client.post(f"/api/v1/analyses/{tid}/transcript-choice", json={"skip": False})
            continued = True
            time.sleep(0.05)
            continue
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    return body


def test_stt_fallback_note_exact():
    init_db(make_test_db_url(), static_pool=False)
    deps = WorkerDeps(
        youtube=FakeYoutube(),
        transcript=FakeTranscript(ok=False, error="ParseError: boom"),
        whisper=FakeWhisper(ok=True, enabled=True),
        skip_audio=True,
        content_llm_mock=CONTENT_MOCK,
        comments_llm_mock=COMMENTS_MOCK,
    )
    set_deps(deps)
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/analyses", json={"url": "https://youtu.be/dQw4w9WgXcQ"}
        )
        body = _wait(client, r.json()["task_id"])
        assert body["status"] == "completed"
        notes = body["result"]["health"]["notes"]
        assert NOTE in notes
        assert body["result"]["health"]["source"]["level"] == "degraded"
        assert body["result"]["health"]["transcript"]["mode"] == "speech_to_text"


def test_dual_fail_not_dual_ok():
    init_db(make_test_db_url(), static_pool=False)
    deps = WorkerDeps(
        youtube=FakeYoutube(comments=[]),
        transcript=FakeTranscript(ok=False),
        whisper=FakeWhisper(ok=False, enabled=True),
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
        body = _wait(client, r.json()["task_id"])
        result = body.get("result")
        if result:
            h = result["health"]
            assert not (
                h["overall_level"] == "ok"
                and h["source"]["level"] == "ok"
                and h["llm"]["level"] == "ok"
            )
            assert h["overall_level"] == "failed"
        else:
            assert body["status"] == "failed"


def test_video_unavailable_no_llm():
    init_db(make_test_db_url(), static_pool=False)
    yt = FakeYoutube(missing=True)
    deps = WorkerDeps(
        youtube=yt,
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
        body = _wait(client, r.json()["task_id"])
        assert body["status"] == "failed"
        assert body["error"]["code"] == "video_unavailable"
        assert body["result"] is None


def test_skip_audio_false_transcribes_fetched_path():
    import os
    import tempfile

    init_db(make_test_db_url(), static_pool=False)
    tmpdir = tempfile.mkdtemp(prefix="stt_test_")
    audio_path = os.path.join(tmpdir, "clip.wav")
    with open(audio_path, "wb") as f:
        f.write(b"fake")

    class RecWhisper(FakeWhisper):
        def __init__(self):
            super().__init__(ok=True, enabled=True)
            self.paths = []

        def transcribe(self, audio_file: str):
            self.paths.append(audio_file)
            return super().transcribe(audio_file)

    whisper = RecWhisper()
    fetched = []

    def fetch(vid):
        fetched.append(vid)
        return audio_path

    deps = WorkerDeps(
        youtube=FakeYoutube(),
        transcript=FakeTranscript(ok=False, error="ParseError: boom"),
        whisper=whisper,
        skip_audio=False,
        audio_fetch_fn=fetch,
        content_llm_mock=CONTENT_MOCK,
        comments_llm_mock=COMMENTS_MOCK,
    )
    set_deps(deps)
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/analyses", json={"url": "https://youtu.be/dQw4w9WgXcQ"}
        )
        body = _wait(client, r.json()["task_id"])
    assert fetched
    assert whisper.paths == [audio_path]
    assert "" not in whisper.paths
    assert body["status"] == "completed"
    assert body["result"]["health"]["transcript"]["mode"] == "speech_to_text"


def test_skip_audio_false_audio_fetch_failure_continues():
    init_db(make_test_db_url(), static_pool=False)

    def fetch(_vid):
        raise RuntimeError("yt-dlp down")

    deps = WorkerDeps(
        youtube=FakeYoutube(),
        transcript=FakeTranscript(ok=False, error="ParseError: boom"),
        whisper=FakeWhisper(ok=True, enabled=True),
        skip_audio=False,
        audio_fetch_fn=fetch,
        content_llm_mock=CONTENT_MOCK,
        comments_llm_mock=COMMENTS_MOCK,
    )
    set_deps(deps)
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/analyses",
            json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        )
        body = _wait(client, r.json()["task_id"])
    assert body["status"] in ("completed", "failed")
    result = body.get("result")
    assert result is not None  # must still save a result for the UI
    assert result["health"]["transcript"]["mode"] == "none"

def test_cached_raw_transcript_none_retries_captions():
    import json
    from datetime import datetime, timezone

    from app import db as db_mod
    from app.models.raw_snapshot import RawSnapshot
    from app.models.video import Video

    init_db(make_test_db_url(), static_pool=False)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    vid = "dQw4w9WgXcQ"
    cached_comments = [
        {
            "comment_id": "c1",
            "text": "中火在电磁炉上怎么判断？",
            "like_count": 12,
            "reply_count": 3,
            "is_top_level": True,
        },
        {
            "comment_id": "c2",
            "text": "终于讲清楚出锅时机了。",
            "like_count": 80,
            "reply_count": 0,
            "is_top_level": True,
        },
        {
            "comment_id": "c3",
            "text": "太慢了熟手会跳过。",
            "like_count": 5,
            "reply_count": 1,
            "is_top_level": True,
        },
    ]
    db = db_mod.SessionLocal()
    try:
        db.add(
            Video(
                video_id=vid,
                platform="youtube",
                canonical_url=f"https://www.youtube.com/watch?v={vid}",
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        db.add(
            RawSnapshot(
                id="raw-none",
                video_id=vid,
                fetched_at=now,
                metadata_json="{}",
                transcript_mode="none",
                transcript_text=None,
                transcript_cues_json="[]",
                comments_json=json.dumps(cached_comments, ensure_ascii=False),
                platform_comment_count=10,
                source_notes_json=json.dumps(["youtube_transcript_api.proxies missing"]),
                checksum="old",
            )
        )
        db.commit()
    finally:
        db.close()

    yt = FakeYoutube()
    tr = FakeTranscript(ok=True)
    deps = WorkerDeps(
        youtube=yt,
        transcript=tr,
        whisper=FakeWhisper(enabled=False),
        skip_audio=True,
        content_llm_mock=CONTENT_MOCK,
        comments_llm_mock=COMMENTS_MOCK,
    )
    set_deps(deps)
    with TestClient(app) as client:
        r = client.post("/api/v1/analyses", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
        body = _wait(client, r.json()["task_id"])
    assert tr.calls >= 1
    assert yt.comment_calls == 0
    assert body["status"] == "completed"
    assert body["result"]["health"]["transcript"]["mode"] == "captions"
    assert body["result"]["content_analysis"]["pipeline_status"] == "ok"

    db = db_mod.SessionLocal()
    try:
        from app.models.analysis_task import AnalysisTask

        old_raw = db.get(RawSnapshot, "raw-none")
        assert old_raw is not None
        assert old_raw.transcript_mode == "none"
        assert old_raw.checksum == "old"
        rows = db.query(RawSnapshot).filter(RawSnapshot.video_id == vid).all()
        assert len(rows) == 2
        newer = next(r for r in rows if r.id != "raw-none")
        assert newer.transcript_mode == "captions"
        assert newer.transcript_text
        task = db.get(AnalysisTask, body["task_id"])
        assert task is not None
        assert task.raw_snapshot_id == newer.id
    finally:
        db.close()
