from __future__ import annotations

from app.config import Settings


def test_youtube_api_proxy_default_direct_ignores_all_proxy():
    s = Settings(ALL_PROXY="socks5://127.0.0.1:1080", YOUTUBE_API_PROXY="")
    assert s.proxy_url() is not None
    assert s.youtube_api_proxy_url() is None


def test_youtube_api_proxy_explicit():
    s = Settings(YOUTUBE_API_PROXY="socks5://127.0.0.1:9050", ALL_PROXY="socks5://127.0.0.1:1080")
    assert s.youtube_api_proxy_url().startswith("socks5h://")
    assert "9050" in s.youtube_api_proxy_url()
