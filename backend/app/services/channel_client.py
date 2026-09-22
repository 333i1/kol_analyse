"""YouTube Data API v3 helpers for channel resolve + uploads listing (D11)."""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import get_settings
from app.services.channel_sampler import SampleVideo
from app.services.youtube_client import YoutubeClientError, wrap_youtube_http_error

logger = logging.getLogger(__name__)
API_BASE = "https://www.googleapis.com/youtube/v3"


class ChannelClient:
    def __init__(self, settings=None, http: Optional[httpx.Client] = None):
        self.settings = settings or get_settings()
        self._http = http
        self.channels_list_calls = 0
        self.playlist_items_calls = 0
        self.videos_list_calls = 0

    def _client(self) -> tuple[httpx.Client, bool]:
        if self._http is not None:
            return self._http, False
        proxies = self.settings.proxy_url()
        kwargs: dict[str, Any] = {"timeout": 20.0}
        if proxies:
            kwargs["proxy"] = proxies
        return httpx.Client(**kwargs), True

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.YOUTUBE_API_KEY:
            raise YoutubeClientError("YOUTUBE_API_KEY missing")
        params = {**params, "key": self.settings.YOUTUBE_API_KEY}
        client, close = self._client()
        try:
            resp = client.get(f"{API_BASE}/{path}", params=params)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise YoutubeClientError(str(data["error"].get("message")))
            return data
        except YoutubeClientError:
            raise
        except Exception as e:
            raise wrap_youtube_http_error(e, proxy_url=self.settings.proxy_url()) from e
        finally:
            if close:
                client.close()

    def resolve_channel(
        self, *, channel_id: str | None = None, handle: str | None = None
    ) -> dict[str, Any]:
        """Return a normalized channel dict with uploads playlist id."""
        self.channels_list_calls += 1
        params: dict[str, Any] = {"part": "snippet,statistics,contentDetails"}
        if channel_id:
            params["id"] = channel_id
        elif handle:
            # forHandle is supported on Data API v3
            params["forHandle"] = handle.lstrip("@")
        else:
            raise YoutubeClientError("channel_id or handle required")
        data = self._get("channels", params)
        items = data.get("items") or []
        if not items:
            raise YoutubeClientError("channel not found")
        item = items[0]
        sn = item.get("snippet") or {}
        st = item.get("statistics") or {}
        cd = item.get("contentDetails") or {}
        uploads = ((cd.get("relatedPlaylists") or {}).get("uploads")) or ""
        if not uploads:
            raise YoutubeClientError("uploads playlist missing")
        return {
            "channel_id": item.get("id") or channel_id or "",
            "title": sn.get("title") or "",
            "handle": (sn.get("customUrl") or (f"@{handle}" if handle else None)),
            "subscriber_count": int(st["subscriberCount"]) if st.get("subscriberCount") is not None else None,
            "view_count": int(st["viewCount"]) if st.get("viewCount") is not None else None,
            "video_count": int(st["videoCount"]) if st.get("videoCount") is not None else None,
            "uploads_playlist_id": uploads,
        }

    def list_uploads(
        self, uploads_playlist_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Newest-first playlist items with videoId + publishedAt."""
        out: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(out) < limit:
            self.playlist_items_calls += 1
            params: dict[str, Any] = {
                "part": "contentDetails,snippet",
                "playlistId": uploads_playlist_id,
                "maxResults": min(50, limit - len(out)),
            }
            if page_token:
                params["pageToken"] = page_token
            data = self._get("playlistItems", params)
            for item in data.get("items") or []:
                cd = item.get("contentDetails") or {}
                sn = item.get("snippet") or {}
                vid = cd.get("videoId") or ""
                if len(vid) == 11:
                    out.append(
                        {
                            "video_id": vid,
                            "published_at": cd.get("videoPublishedAt") or sn.get("publishedAt"),
                            "title": sn.get("title"),
                        }
                    )
                if len(out) >= limit:
                    break
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return out

    def videos_stats(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Batch videos.list statistics; keys are video_id."""
        result: dict[str, dict[str, Any]] = {}
        ids = [v for v in video_ids if v]
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            self.videos_list_calls += 1
            data = self._get(
                "videos",
                {
                    "part": "statistics,contentDetails,snippet",
                    "id": ",".join(chunk),
                },
            )
            for item in data.get("items") or []:
                vid = item.get("id") or ""
                st = item.get("statistics") or {}
                sn = item.get("snippet") or {}
                cd = item.get("contentDetails") or {}
                result[vid] = {
                    "video_id": vid,
                    "view_count": int(st.get("viewCount") or 0),
                    "like_count": int(st.get("likeCount") or 0) if st.get("likeCount") is not None else None,
                    "comment_count": int(st.get("commentCount") or 0) if st.get("commentCount") is not None else None,
                    "title": sn.get("title"),
                    "published_at": sn.get("publishedAt"),
                    "duration": cd.get("duration"),
                }
        return result

    def fetch_sample_candidates(
        self, *, channel_id: str | None = None, handle: str | None = None, window_size: int = 50
    ) -> tuple[dict[str, Any], list[SampleVideo]]:
        ch = self.resolve_channel(channel_id=channel_id, handle=handle)
        uploads = self.list_uploads(ch["uploads_playlist_id"], limit=window_size)
        stats = self.videos_stats([u["video_id"] for u in uploads])
        samples: list[SampleVideo] = []
        for u in uploads:
            st = stats.get(u["video_id"]) or {}
            samples.append(
                SampleVideo(
                    video_id=u["video_id"],
                    view_count=int(st.get("view_count") or 0),
                    published_at=u.get("published_at"),
                )
            )
        return ch, samples
