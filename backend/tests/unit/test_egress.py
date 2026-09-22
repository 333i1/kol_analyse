# -*- coding: utf-8 -*-
from types import SimpleNamespace

from app.services.egress import (
    EgressHandle,
    StaticEgress,
    build_caption_egress,
    is_rate_limited_message,
    parse_proxy_list,
    reset_caption_egress_for_tests,
)
from app.services.transcript_client import TranscriptClient, sanitize_caption_failure_message


class _MockEgress:
    def __init__(self, proxy=None):
        self.proxy = proxy
        self.acquired = 0
        self.reports = []
        self.released = 0

    def acquire(self):
        self.acquired += 1
        return EgressHandle(proxy_url=self.proxy, label="mock", _token=self.acquired)

    def report(self, handle, outcome):
        self.reports.append(outcome)

    def release(self, handle):
        self.released += 1


class _Track:
    def __init__(self, language_code="zh", is_generated=False, text="hello"):
        self.language_code = language_code
        self.is_generated = is_generated
        self._snippets = [{"start": 0.0, "duration": 1.0, "text": text}]

    def fetch(self):
        return self._snippets


class _List:
    def __init__(self, tracks):
        self._tracks = tracks

    def __iter__(self):
        return iter(self._tracks)

    def find_transcript(self, langs):
        return self._tracks[0]


class _Api:
    def __init__(self, tracks):
        self._tracks = tracks

    def list(self, video_id):
        return _List(self._tracks)


class _RateLimitApi:
    def list(self, video_id):
        raise RuntimeError("HTTP 429 Too Many Requests")


def test_parse_proxy_list():
    assert parse_proxy_list("socks5://a:1, http://b:2")[0].startswith("socks5h://")
    assert len(parse_proxy_list("a\nb")) == 2


def test_static_interval_and_backoff():
    sleeps = []
    clock = {"t": 100.0}

    def sleeper(s):
        sleeps.append(s)
        clock["t"] += s

    eg = StaticEgress(
        proxy_url=None,
        concurrency=1,
        min_interval_s=5.0,
        backoff_s=30.0,
        jitter_s=0.0,
        sleeper=sleeper,
        clock=lambda: clock["t"],
    )
    h1 = eg.acquire()
    eg.report(h1, "ok")
    eg.release(h1)
    # second acquire should wait ~5s
    h2 = eg.acquire()
    assert sleeps and sleeps[0] >= 4.9
    eg.report(h2, "rate_limited")
    eg.release(h2)
    sleeps.clear()
    h3 = eg.acquire()
    # after rate limit, wait includes backoff (~30)
    assert sleeps and sleeps[0] >= 29.0
    eg.release(h3)


def test_pool_mode_falls_back_static():
    reset_caption_egress_for_tests()
    settings = SimpleNamespace(
        CAPTION_EGRESS="pool",
        CAPTION_PROXIES="socks5://127.0.0.1:1080",
        CAPTION_CONCURRENCY=1,
        CAPTION_MIN_INTERVAL_S=0,
        CAPTION_BACKOFF_S=0,
        CAPTION_JITTER_S=0,
        proxy_url=lambda: None,
    )
    eg = build_caption_egress(settings)
    assert isinstance(eg, StaticEgress)
    assert eg.proxy_url and eg.proxy_url.startswith("socks5h://")


def test_transcript_uses_mock_egress():
    mock = _MockEgress()
    client = TranscriptClient(
        api=_Api([_Track()]),
        egress=mock,
        ydl_cls=None,
    )
    # avoid ytdlp network: provide human track so cascade returns early
    r = client.fetch("abcdefghijk")
    assert r.ok
    assert mock.acquired == 1
    assert mock.released == 1
    assert mock.reports == ["ok"]


def test_transcript_reports_rate_limited():
    mock = _MockEgress()

    class _NoYdl:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def download(self, urls):
            return

    client = TranscriptClient(api=_RateLimitApi(), egress=mock, ydl_cls=_NoYdl)
    r = client.fetch("abcdefghijk")
    assert not r.ok
    assert mock.reports == ["rate_limited"]
    assert "降级" in r.error_msg or "限流" in r.error_msg or "字幕" in r.error_msg


def test_sanitize_429():
    msg = sanitize_caption_failure_message("Error: 429 Too Many Requests")
    assert "降级" in msg


def test_is_rate_limited_message():
    assert is_rate_limited_message("HTTP Error 429: Too Many Requests")
    assert is_rate_limited_message("RequestBlocked")
    assert not is_rate_limited_message("no captions found")
