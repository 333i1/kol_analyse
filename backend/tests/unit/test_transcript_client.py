from types import SimpleNamespace

from app.services.captions_ytdlp import parse_json3, parse_srt, parse_vtt
from app.services.transcript_client import TranscriptClient


class _Track:
    def __init__(self, language_code, is_generated=None, text="今天只做番茄炒蛋"):
        self.language_code = language_code
        if is_generated is not None:
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
                if getattr(t, "language_code", None) == lang:
                    return t
        if self._tracks:
            return self._tracks[0]
        raise RuntimeError("no transcript")


class _Api:
    def __init__(self, tracks):
        self._tracks = tracks
        self.list_calls = 0

    def list(self, video_id):
        self.list_calls += 1
        return _List(self._tracks)


class _BoomApi:
    def list(self, video_id):
        raise RuntimeError("api down")


class _NoSubsYdl:
    """Injected yt-dlp: writes nothing. Avoids network in unit tests."""

    calls = []

    def __init__(self, opts):
        self.opts = opts
        type(self).calls.append(opts)

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

_AUTO_VTT = """WEBVTT

00:00:00.000 --> 00:00:08.000
yt-dlp auto asr
"""


class _YdlManualVtt:
    calls = []

    def __init__(self, opts):
        self.opts = opts
        type(self).calls.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        import os

        if self.opts.get("writeautomaticsub"):
            return
        if not self.opts.get("writesubtitles"):
            return
        tmpdir = os.path.dirname(self.opts["outtmpl"])
        path = os.path.join(tmpdir, "dQw4w9WgXcQ.zh-Hans.vtt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_MANUAL_VTT)


class _YdlAutoVtt:
    calls = []

    def __init__(self, opts):
        self.opts = opts
        type(self).calls.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        import os

        if not self.opts.get("writeautomaticsub"):
            return
        tmpdir = os.path.dirname(self.opts["outtmpl"])
        path = os.path.join(tmpdir, "dQw4w9WgXcQ.en.vtt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_AUTO_VTT)


def _client(tracks, ydl_cls=None, api=None, settings=None):
    return TranscriptClient(
        settings=settings or SimpleNamespace(),
        api=api if api is not None else _Api(tracks),
        ydl_cls=ydl_cls if ydl_cls is not None else _NoSubsYdl,
    )


def test_manual_track_preferred_over_generated():
    human = _Track("zh", is_generated=False, text="人工字幕正文")
    auto = _Track("en", is_generated=True, text="auto asr")
    tr = _client([auto, human]).fetch("dQw4w9WgXcQ")
    assert tr.ok is True
    assert tr.is_generated is False
    assert "人工字幕正文" in tr.text
    assert tr.language == "zh"


def test_manual_prefers_zh_hans_then_en():
    tracks = [
        _Track("en", is_generated=False, text="english manual"),
        _Track("zh-Hans", is_generated=False, text="简体人工"),
        _Track("ja", is_generated=False, text="日本語"),
    ]
    tr = _client(tracks).fetch("dQw4w9WgXcQ")
    assert tr.ok is True
    assert tr.language == "zh-Hans"
    assert "简体人工" in tr.text


def test_generated_only_is_accepted():
    """Reverse of scheme-2: auto API tracks are valid captions, not a reject."""
    tr = _client([_Track("en", is_generated=True, text="auto asr body")]).fetch("dQw4w9WgXcQ")
    assert tr.ok is True
    assert tr.is_generated is True
    assert "auto asr body" in tr.text
    assert tr.language == "en"


def test_no_tracks_fails():
    tr = _client([]).fetch("dQw4w9WgXcQ")
    assert tr.ok is False
    assert "字幕轨" in tr.error_msg or "no transcript" in tr.error_msg.lower() or "StopIteration" in tr.error_msg


def test_missing_is_generated_keeps_find_transcript():
    """Old fakes without is_generated: language match via find_transcript."""
    tracks = [_Track("en"), _Track("zh")]  # neither sets is_generated
    tr = _client(tracks).fetch("dQw4w9WgXcQ")
    assert tr.ok is True
    assert tr.is_generated is None
    assert tr.language == "zh"


def test_api_fail_ytdlp_manual_vtt():
    _YdlManualVtt.calls = []
    tr = _client([], api=_BoomApi(), ydl_cls=_YdlManualVtt).fetch("dQw4w9WgXcQ")
    assert tr.ok is True
    assert tr.is_generated is False
    assert "yt-dlp 人工字幕" in tr.text
    assert _YdlManualVtt.calls
    first = _YdlManualVtt.calls[0]
    assert first.get("writesubtitles") is True
    assert first.get("writeautomaticsub") is not True
    assert first.get("skip_download") is True
    # manual hit: do not mix auto into the first attempt
    assert not any(c.get("writeautomaticsub") is True for c in _YdlManualVtt.calls)


def test_api_fail_ytdlp_auto_vtt():
    _YdlAutoVtt.calls = []
    tr = _client([], api=_BoomApi(), ydl_cls=_YdlAutoVtt).fetch("dQw4w9WgXcQ")
    assert tr.ok is True
    assert tr.is_generated is True
    assert "yt-dlp auto asr" in tr.text
    assert any(c.get("writeautomaticsub") is True for c in _YdlAutoVtt.calls)
    manuals = [c for c in _YdlAutoVtt.calls if c.get("writesubtitles") is True and c.get("writeautomaticsub") is not True]
    autos = [c for c in _YdlAutoVtt.calls if c.get("writeautomaticsub") is True]
    assert manuals, "first phase should be manual-only"
    assert autos, "second phase should request auto subs"
    assert manuals[0].get("writeautomaticsub") is not True
    assert autos[0].get("writesubtitles") is False


def test_human_api_does_not_call_ytdlp():
    _NoSubsYdl.calls = []
    tr = _client([_Track("zh", is_generated=False)]).fetch("dQw4w9WgXcQ")
    assert tr.ok is True
    assert _NoSubsYdl.calls == []


def test_ytdlp_respects_proxy():
    _YdlManualVtt.calls = []
    settings = SimpleNamespace(proxy_url=lambda: "http://127.0.0.1:7890")
    tr = _client([], api=_BoomApi(), ydl_cls=_YdlManualVtt, settings=settings).fetch("dQw4w9WgXcQ")
    assert tr.ok is True
    assert _YdlManualVtt.calls[0].get("proxy") == "http://127.0.0.1:7890"


def test_parse_vtt_cues():
    cues = parse_vtt(
        "WEBVTT\n\n00:00:00.000 --> 00:00:08.000\nhello <c>world</c>\n\n00:00:08.000 --> 00:00:12.000\nnext\n"
    )
    assert len(cues) == 2
    assert cues[0]["text"] == "hello world"
    assert cues[0]["start"] == 0.0
    assert cues[0]["duration"] == 8.0
    assert cues[1]["text"] == "next"


def test_parse_srt_and_json3():
    srt = "1\n00:00:00,000 --> 00:00:05,000\nfrom srt\n"
    cues = parse_srt(srt)
    assert cues and cues[0]["text"] == "from srt"
    payload = '{"events":[{"tStartMs":1000,"dDurationMs":2000,"segs":[{"utf8":"from json3"}]}]}'
    jcues = parse_json3(payload)
    assert jcues == [{"start": 1.0, "duration": 2.0, "text": "from json3"}]
