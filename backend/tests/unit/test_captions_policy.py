# -*- coding: utf-8 -*-
from app.services.transcript_client import sanitize_caption_failure_message
from app.config import Settings


def test_sanitize_request_blocked():
    raw = (
        "RequestBlocked: Could not retrieve a transcript "
        "YouTube is blocking requests from your IP. cloud provider"
    )
    out = sanitize_caption_failure_message(raw)
    assert "RequestBlocked" not in out
    assert "降级" in out or "标题" in out


def test_sanitize_keeps_normal_short_errors():
    msg = "该视频没有任何可用字幕轨"
    assert sanitize_caption_failure_message(msg) == msg


def test_captions_enabled_default_true():
    s = Settings(_env_file=None)
    assert s.CAPTIONS_ENABLED is True


def test_captions_can_disable_via_env(monkeypatch):
    monkeypatch.setenv("CAPTIONS_ENABLED", "false")
    s = Settings(_env_file=None)
    assert s.CAPTIONS_ENABLED is False
