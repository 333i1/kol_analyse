"""Parse a pasted string into a YouTube channel identity. Never raises to the framework."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
INVALID_CHANNEL_MESSAGE = "无法解析为合法 YouTube 频道，不创建任务"


@dataclass(frozen=True)
class ChannelRef:
    """One of channel_id or handle is set."""

    channel_id: str | None = None
    handle: str | None = None
    raw: str = ""

    def ok(self) -> bool:
        return bool(self.channel_id or self.handle)


def parse_youtube_channel_ref(raw: str | None) -> ChannelRef | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if CHANNEL_ID_RE.match(text):
        return ChannelRef(channel_id=text, raw=text)
    if text.startswith("@") and HANDLE_RE.match(text[1:]):
        return ChannelRef(handle=text[1:], raw=text)
    try:
        parsed = urlparse(text if "://" in text else "https://" + text)
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    if parts[0].startswith("@") and HANDLE_RE.match(parts[0][1:]):
        return ChannelRef(handle=parts[0][1:], raw=text)
    if parts[0] == "channel" and len(parts) >= 2 and CHANNEL_ID_RE.match(parts[1]):
        return ChannelRef(channel_id=parts[1], raw=text)
    if parts[0] in ("c", "user") and len(parts) >= 2 and HANDLE_RE.match(parts[1]):
        return ChannelRef(handle=parts[1], raw=text)
    if len(parts) == 1 and HANDLE_RE.match(parts[0]) and parts[0] not in ("watch", "shorts", "embed", "live"):
        return ChannelRef(handle=parts[0], raw=text)
    return None
