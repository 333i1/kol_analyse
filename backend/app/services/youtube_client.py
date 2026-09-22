
"""YouTube Data API v3: videos.list + commentThreads.list (topLevelComment only)."""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
API_BASE = "https://www.googleapis.com/youtube/v3"


class VideoUnavailable(Exception):
    pass


class YoutubeClientError(Exception):
    pass

def format_youtube_transport_error(exc: BaseException, *, proxy_url: str | None = None) -> str:
    """Short operator-facing message for proxy / connection failures.

    Does not invent proxy ports; points at HTTP_PROXY/HTTPS_PROXY when configured.
    Non-transport errors keep ``TypeName: detail`` for debugging.
    """
    name = type(exc).__name__
    detail = str(exc) or ""
    blob = f"{name} {detail}".lower()
    transport = False
    if isinstance(exc, (httpx.ConnectError, httpx.ProxyError, httpx.ConnectTimeout)):
        transport = True
    markers = (
        "10061",
        "actively refused",
        "connection refused",
        "errno 111",
        "errno 61",
        "failed to establish a new connection",
        "proxyerror",
        "connecterror",
        "name or service not known",
        "nodename nor servname",
        "getaddrinfo failed",
        "timed out",
        "timeout",
    )
    if any(m in blob for m in markers):
        transport = True
    # Walk __cause__ / __context__ for wrapped OSError
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (ConnectionRefusedError, TimeoutError, OSError)):
            transport = True
            break
        cur = cur.__cause__ or cur.__context__
    if not transport:
        return f"{name}: {exc}"
    if proxy_url:
        return "无法连接代理或外网（请检查 YOUTUBE_API_PROXY 是否已启动）"
    return "网络连接失败，无法访问 YouTube API（当前为直连；必要时再设 YOUTUBE_API_PROXY）"


def wrap_youtube_http_error(exc: BaseException, *, proxy_url: str | None = None) -> YoutubeClientError:
    if isinstance(exc, YoutubeClientError):
        return exc
    return YoutubeClientError(format_youtube_transport_error(exc, proxy_url=proxy_url))


def _looks_like_transport_message(msg: str) -> bool:
    blob = (msg or "").lower()
    markers = (
        "无法连接代理",
        "网络连接失败",
        "10061",
        "actively refused",
        "connection refused",
        "proxyerror",
        "connecterror",
        "timed out",
        "timeout",
        "getaddrinfo failed",
        "socksio",
        "socks support",
    )
    return any(m in blob for m in markers)


def classify_youtube_client_error(
    exc: BaseException, *, proxy_url: str | None = None
) -> tuple[str, str]:
    """Map exceptions to stable task error_code + operator-facing message.

    Codes:
      video_unavailable — video missing/private (API returned empty items)
      youtube_proxy_failed — Data API transport failed while a proxy was in use
      youtube_network_failed — Data API transport failed on direct egress
      youtube_fetch_failed — other YouTube/client errors (HTTP 4xx/5xx, bad key, …)
    """
    if isinstance(exc, VideoUnavailable):
        return "video_unavailable", f"video_unavailable: {exc}"

    if isinstance(exc, YoutubeClientError):
        msg = str(exc)
    else:
        msg = format_youtube_transport_error(exc, proxy_url=proxy_url)

    transport = _looks_like_transport_message(msg)
    if not transport and not isinstance(exc, YoutubeClientError):
        # Re-check raw exception type
        probe = format_youtube_transport_error(exc, proxy_url=proxy_url)
        if probe != f"{type(exc).__name__}: {exc}":
            transport = True
            msg = probe

    if transport:
        if proxy_url or "代理" in msg:
            return "youtube_proxy_failed", msg
        return "youtube_network_failed", msg

    if isinstance(exc, YoutubeClientError):
        return "youtube_fetch_failed", msg
    return "youtube_fetch_failed", f"{type(exc).__name__}: {exc}"


class YoutubeClient:
    def __init__(self, settings=None, http: Optional[httpx.Client] = None):
        self.settings = settings or get_settings()
        self._http = http
        self.videos_list_calls = 0
        self.comment_calls = 0

    def _api_proxy(self) -> Optional[str]:
        fn = getattr(self.settings, "youtube_api_proxy_url", None)
        if callable(fn):
            return fn()
        return None

    def _client(self) -> tuple[httpx.Client, bool]:
        if self._http is not None:
            return self._http, False
        # Data API: YOUTUBE_API_PROXY only (default direct). Captions use egress separately.
        proxies = self._api_proxy()
        kwargs: dict[str, Any] = {"timeout": 20.0}
        if proxies:
            kwargs["proxy"] = proxies
        return httpx.Client(**kwargs), True

    def videos_list(self, video_id: str) -> dict[str, Any]:
        if not self.settings.YOUTUBE_API_KEY:
            raise YoutubeClientError("YOUTUBE_API_KEY missing")
        self.videos_list_calls += 1
        client, close = self._client()
        data: dict[str, Any] = {}
        try:
            parts_primary = "snippet,statistics,contentDetails,paidProductPlacementDetails"
            parts_fallback = "snippet,statistics,contentDetails"
            resp = client.get(
                f"{API_BASE}/videos",
                params={
                    "key": self.settings.YOUTUBE_API_KEY,
                    "id": video_id,
                    "part": parts_primary,
                },
            )
            if resp.status_code == 400 and "paidProductPlacementDetails" in (resp.text or ""):
                resp = client.get(
                    f"{API_BASE}/videos",
                    params={
                        "key": self.settings.YOUTUBE_API_KEY,
                        "id": video_id,
                        "part": parts_fallback,
                    },
                )
            resp.raise_for_status()
            data = resp.json()
        except YoutubeClientError:
            raise
        except Exception as e:
            raise wrap_youtube_http_error(e, proxy_url=self._api_proxy()) from e
        finally:
            if close:
                client.close()
        items = data.get("items") or []
        if not items:
            raise VideoUnavailable(f"video {video_id} not found or private")
        return items[0]



    def channel_statistics(self, channel_id: str) -> dict[str, Any]:
        """Return subscriber_count (and raw stats) for one channel id."""
        if not self.settings.YOUTUBE_API_KEY:
            raise YoutubeClientError("YOUTUBE_API_KEY missing")
        if not channel_id:
            raise YoutubeClientError("channel_id required")
        client, close = self._client()
        try:
            resp = client.get(
                f"{API_BASE}/channels",
                params={
                    "key": self.settings.YOUTUBE_API_KEY,
                    "id": channel_id,
                    "part": "statistics,snippet",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except YoutubeClientError:
            raise
        except Exception as e:
            raise wrap_youtube_http_error(e, proxy_url=self._api_proxy()) from e
        finally:
            if close:
                client.close()
        items = data.get("items") or []
        if not items:
            return {"channel_id": channel_id, "subscriber_count": None}
        item = items[0]
        st = item.get("statistics") or {}
        sub = st.get("subscriberCount")
        try:
            sub_i = int(sub) if sub is not None else None
        except (TypeError, ValueError):
            sub_i = None
        return {
            "channel_id": item.get("id") or channel_id,
            "title": ((item.get("snippet") or {}).get("title")) or "",
            "subscriber_count": sub_i,
            "hidden_subscriber_count": bool(st.get("hiddenSubscriberCount")),
        }

    def comment_threads(
        self, video_id: str, *, max_comments: int = 200
    ) -> list[dict[str, Any]]:
        if not self.settings.YOUTUBE_API_KEY:
            raise YoutubeClientError("YOUTUBE_API_KEY missing")
        results: list[dict[str, Any]] = []
        page_token: str | None = None
        client, close = self._client()
        try:
            while len(results) < max_comments:
                self.comment_calls += 1
                params: dict[str, Any] = {
                    "key": self.settings.YOUTUBE_API_KEY,
                    "videoId": video_id,
                    "part": "snippet",
                    "maxResults": min(100, max_comments - len(results)),
                    "textFormat": "plainText",
                }
                if page_token:
                    params["pageToken"] = page_token
                resp = client.get(f"{API_BASE}/commentThreads", params=params)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    raise YoutubeClientError(str(data["error"].get("message")))
                for item in data.get("items") or []:
                    snippet_wrap = item.get("snippet") or {}
                    top = snippet_wrap.get("topLevelComment") or {}
                    sn = top.get("snippet") or {}
                    results.append(
                        {
                            "comment_id": top.get("id") or "",
                            "text": sn.get("textOriginal") or "",
                            "like_count": int(sn.get("likeCount") or 0),
                            "reply_count": int(snippet_wrap.get("totalReplyCount") or 0),
                            "published_at": sn.get("publishedAt"),
                            "is_top_level": True,
                        }
                    )
                    if len(results) >= max_comments:
                        break
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        except Exception as e:
            logger.warning("commentThreads failed: %s: %s", type(e).__name__, e)
            if not results:
                raise wrap_youtube_http_error(e, proxy_url=self._api_proxy()) from e
        finally:
            if close:
                client.close()
        return results
