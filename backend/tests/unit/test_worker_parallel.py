from __future__ import annotations

import os
import time
from types import SimpleNamespace

from app.services.transcript_client import TranscriptClient, TranscriptResult
from app.services.worker import (
    WorkerDeps,
    _fetch_caption_and_or_comments,
    _fetch_transcript_bundle,
)
from tests.helpers import FakeWhisper, FakeYoutube, VID


class _Track:
    def __init__(self, language_code, is_generated, text="今天只做番茄炒蛋"):
        self.language_code = language_code
        self.is_generated = is_generated
        self._snippets = [{"start": 0.0, "duration": 8.0, "text": text}]

    def fetch(self):
        return self._snippets


class _List:
    def __init__(self, tracks):
        self._tracks = tracks

    def __iter__(self):
        return iter(self._tracks)

    def find_transcript(self, langs):
        for lang in langs:
            for t in self._tracks:
                if t.language_code == lang:
                    return t
        raise RuntimeError("no transcript")


class _Api:
    def __init__(self, tracks):
        self._tracks = tracks

    def list(self, video_id):
        return _List(self._tracks)


class _BoomApi:
    def list(self, video_id):
        raise RuntimeError("api down")


class _NoSubsYdl:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        return


_MANUAL_VTT = """WEBVTT

00:00:00.000 --> 00:00:08.000
yt-dlp 人工字幕
"""


class _YdlManualVtt:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        if self.opts.get("writeautomaticsub"):
            return
        if not self.opts.get("writesubtitles"):
            return
        tmpdir = os.path.dirname(self.opts["outtmpl"])
        path = os.path.join(tmpdir, f"{VID}.zh-Hans.vtt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_MANUAL_VTT)


def _caption_client(tracks=None, *, api=None, ydl_cls=None):
    return TranscriptClient(
        settings=SimpleNamespace(),
        api=api if api is not None else _Api(tracks or []),
        ydl_cls=ydl_cls if ydl_cls is not None else _NoSubsYdl,
    )


def test_manual_track_skips_whisper():
    whisper = FakeWhisper(ok=True, enabled=True)
    deps = WorkerDeps(
        transcript=_caption_client([_Track("zh", is_generated=False)]),
        whisper=whisper,
        skip_audio=True,
    )
    mode, status, cues, text, src, notes = _fetch_transcript_bundle(deps, VID)
    assert mode == "captions"
    assert status == "ok"
    assert src == "ok"
    assert whisper.transcribe_calls == 0
    assert "人工字幕" in notes
    assert text


def test_generated_only_skips_whisper():
    """Auto API track is captions (degraded source). Whisper is not called."""
    whisper = FakeWhisper(ok=True, enabled=True)
    deps = WorkerDeps(
        transcript=_caption_client([_Track("en", is_generated=True)]),
        whisper=whisper,
        skip_audio=True,
    )
    mode, status, cues, text, src, notes = _fetch_transcript_bundle(deps, VID)
    assert whisper.transcribe_calls == 0
    assert mode == "captions"
    assert status == "ok"
    assert src == "degraded"
    assert any("自动字幕（YouTube ASR）" in n for n in notes)
    assert "字幕接口失败，已降级语音转录" not in notes
    assert text


def test_api_fail_ytdlp_manual_skips_whisper():
    whisper = FakeWhisper(ok=True, enabled=True)
    deps = WorkerDeps(
        transcript=_caption_client(api=_BoomApi(), ydl_cls=_YdlManualVtt),
        whisper=whisper,
        skip_audio=True,
    )
    mode, status, cues, text, src, notes = _fetch_transcript_bundle(deps, VID)
    assert whisper.transcribe_calls == 0
    assert mode == "captions"
    assert src == "ok"
    assert "人工字幕" in notes
    assert "yt-dlp 人工字幕" in text


def test_api_fail_no_ytdlp_does_not_auto_whisper():
    """Caption miss no longer auto-calls STT (operator choice is a later step)."""
    whisper = FakeWhisper(ok=True, enabled=True)
    deps = WorkerDeps(
        transcript=_caption_client(api=_BoomApi(), ydl_cls=_NoSubsYdl),
        whisper=whisper,
        skip_audio=True,
    )
    mode, status, cues, text, src, notes = _fetch_transcript_bundle(deps, VID)
    assert whisper.transcribe_calls == 0
    assert mode == "none"
    assert status == "failed"
    assert src == "degraded"
    assert not text


def test_all_caption_paths_fail_whisper_off():
    whisper = FakeWhisper(ok=True, enabled=False)
    deps = WorkerDeps(
        transcript=_caption_client(api=_BoomApi(), ydl_cls=_NoSubsYdl),
        whisper=whisper,
        skip_audio=True,
    )
    mode, status, cues, text, src, notes = _fetch_transcript_bundle(deps, VID)
    assert whisper.transcribe_calls == 0
    assert mode == "none"
    assert status == "failed"
    assert not text


class _SlowTranscript:
    def __init__(self, result: TranscriptResult, delay=0.2):
        self._result = result
        self.delay = delay
        self.calls = 0
        self.started = 0.0
        self.ended = 0.0

    def fetch(self, video_id: str) -> TranscriptResult:
        self.calls += 1
        self.started = time.monotonic()
        time.sleep(self.delay)
        self.ended = time.monotonic()
        return self._result


class _SlowWhisper(FakeWhisper):
    def __init__(self, delay=0.2):
        super().__init__(ok=True, enabled=True)
        self.delay = delay
        self.started = 0.0
        self.ended = 0.0

    def transcribe(self, audio_file: str):
        self.started = time.monotonic()
        time.sleep(self.delay)
        self.ended = time.monotonic()
        return super().transcribe(audio_file)


class _SlowYoutube(FakeYoutube):
    def __init__(self, delay=0.2):
        super().__init__()
        self.delay = delay
        self.comment_started = 0.0
        self.comment_ended = 0.0

    def comment_threads(self, video_id: str, *, max_comments: int = 200):
        self.comment_started = time.monotonic()
        time.sleep(self.delay)
        self.comment_ended = time.monotonic()
        return super().comment_threads(video_id, max_comments=max_comments)


def test_parallel_fetch_overlaps_captions_and_comments():
    """Captions miss does not call Whisper; comments still overlap caption fetch."""
    tr = _SlowTranscript(TranscriptResult(ok=False, error_msg="ParseError: boom"), delay=0.15)
    whisper = _SlowWhisper(delay=0.2)
    yt = _SlowYoutube(delay=0.15)
    deps = WorkerDeps(youtube=yt, transcript=tr, whisper=whisper, skip_audio=True)
    t0 = time.monotonic()
    tr_bundle, cm = _fetch_caption_and_or_comments(deps, VID, True, True)
    elapsed = time.monotonic() - t0
    mode, status, cues, text, src, notes = tr_bundle
    comments, comments_err = cm
    assert tr.calls == 1
    assert whisper.transcribe_calls == 0
    assert yt.comment_calls == 1
    assert mode == "none"
    assert comments
    assert comments_err is None
    assert elapsed < 0.4
