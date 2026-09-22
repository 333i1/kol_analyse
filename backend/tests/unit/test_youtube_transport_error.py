from __future__ import annotations

import httpx

from app.services.youtube_client import (
    VideoUnavailable,
    YoutubeClientError,
    classify_youtube_client_error,
    format_youtube_transport_error,
)


def test_connect_refused_with_proxy_short_zh():
    exc = httpx.ConnectError("[WinError 10061] 由于目标计算机积极拒绝，无法连接。")
    msg = format_youtube_transport_error(exc, proxy_url="http://127.0.0.1:10809")
    assert "YOUTUBE_API_PROXY" in msg
    assert "10061" not in msg
    assert "ConnectError" not in msg


def test_connect_refused_without_proxy():
    exc = httpx.ConnectError("Connection refused")
    msg = format_youtube_transport_error(exc, proxy_url=None)
    assert "网络连接失败" in msg
    assert "ConnectError" not in msg


def test_non_transport_keeps_type():
    exc = ValueError("bad json")
    msg = format_youtube_transport_error(exc, proxy_url="http://127.0.0.1:1")
    assert msg.startswith("ValueError:")


def test_classify_video_unavailable():
    code, msg = classify_youtube_client_error(VideoUnavailable("gone"), proxy_url=None)
    assert code == "video_unavailable"
    assert "gone" in msg


def test_classify_proxy_failed():
    exc = YoutubeClientError("无法连接代理或外网（请检查 YOUTUBE_API_PROXY 是否已启动）")
    code, msg = classify_youtube_client_error(exc, proxy_url="socks5h://127.0.0.1:1080")
    assert code == "youtube_proxy_failed"
    assert "代理" in msg


def test_classify_network_failed_direct():
    exc = YoutubeClientError("网络连接失败，无法访问 YouTube API（当前为直连；必要时再设 YOUTUBE_API_PROXY）")
    code, msg = classify_youtube_client_error(exc, proxy_url=None)
    assert code == "youtube_network_failed"


def test_classify_other_fetch():
    code, msg = classify_youtube_client_error(YoutubeClientError("YOUTUBE_API_KEY missing"), proxy_url=None)
    assert code == "youtube_fetch_failed"
    assert "YOUTUBE_API_KEY" in msg
