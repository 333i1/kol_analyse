
"""Parse a pasted string into an 11-char YouTube video id. Never raises to the framework."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
INVALID_URL_MESSAGE = "无法解析为合法 YouTube 视频，不创建任务"


def parse_youtube_video_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if VIDEO_ID_RE.match(text):
        # bare id is not a legal URL per contract (need watch?v= or youtu.be/)
        return None
    try:
        parsed = urlparse(text if "://" in text else "https://" + text)
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in ("youtu.be", "m.youtu.be"):
        candidate = parsed.path.lstrip("/").split("/")[0]
        return candidate if VIDEO_ID_RE.match(candidate) else None
    if host in ("youtube.com", "m.youtube.com", "www.youtube.com", "music.youtube.com"):
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            candidate = qs["v"][0]
            return candidate if VIDEO_ID_RE.match(candidate) else None
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("embed", "shorts", "live", "v"):
            candidate = parts[1]
            return candidate if VIDEO_ID_RE.match(candidate) else None
        return None
    return None
