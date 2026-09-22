"""youtube-transcript-api + yt-dlp caption cascade. Never raises into comments pipeline."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import get_settings
from app.services.egress import (
    EgressProvider,
    StaticEgress,
    get_caption_egress,
    is_rate_limited_message,
)

logger = logging.getLogger(__name__)


def sanitize_caption_failure_message(msg: str) -> str:
    """Shorten YouTube IP/cloud blocks for notes/UI. Keep other errors intact."""
    s = (msg or "").strip()
    if not s:
        return "未拿到可用字幕"
    if re.search(
        r"\b429\b|Too Many Requests|rate.?limit|RequestBlocked|IpBlocked|"
        r"blocking requests from your IP|cloud provider|"
        r"IPs from cloud providers are blocked",
        s,
        re.I,
    ):
        return (
            "当前运行环境无法从 YouTube 拉取字幕（限流或机房 IP 限制）。"
            "已按产品策略降级：内容侧重标题/简介，评论分析照常。"
        )
    if len(s) > 280:
        return s[:277] + "..."
    return s

_PREFERRED_LANGUAGES = ["zh-Hans", "zh-Hant", "zh", "en"]


@dataclass
class Cue:
    start: float
    duration: float
    text: str


@dataclass
class TranscriptResult:
    ok: bool
    cues: list[Cue] = field(default_factory=list)
    text: str = ""
    language: str = "unknown"
    error_msg: str = ""
    # None = unknown (old fakes without is_generated). False = human/manual. True = ASR.
    is_generated: Optional[bool] = None


def _settings_proxy(settings: Any) -> Optional[str]:
    fn = getattr(settings, "proxy_url", None)
    if callable(fn):
        try:
            return fn()
        except Exception:
            return None
    return None


def _prefer_lang(tracks: list[Any]) -> Any:
    for lang in _PREFERRED_LANGUAGES:
        for t in tracks:
            if getattr(t, "language_code", None) == lang:
                return t
    return tracks[0]


def _iter_tracks(transcript_list) -> list[Any]:
    try:
        return list(transcript_list)
    except Exception:
        return []


def _split_tracks(transcript_list) -> tuple[Any, Any]:
    """Return (human_or_unknown_track, generated_track). Either may be None.

    getattr(t, "is_generated", None):
      False → human. Prefer zh-Hans, zh-Hant, zh, en, then any other manual track.
      True → auto/ASR; held for a later cascade step (after yt-dlp manual).
      None (old unit-test fakes missing the attribute) → find_transcript / first
        track so those tests keep working. Treated as a passthrough "human-like" hit.
    """
    tracks = _iter_tracks(transcript_list)
    known = [t for t in tracks if getattr(t, "is_generated", None) is not None]
    if known:
        human = [t for t in tracks if getattr(t, "is_generated", None) is False]
        generated = [t for t in tracks if getattr(t, "is_generated", None) is True]
        return (
            _prefer_lang(human) if human else None,
            _prefer_lang(generated) if generated else None,
        )

    try:
        return transcript_list.find_transcript(_PREFERRED_LANGUAGES), None
    except Exception:
        transcript = next(iter(tracks), None)
        if transcript is None:
            try:
                transcript = next(iter(transcript_list), None)
            except Exception:
                transcript = None
        return transcript, None


class TranscriptClient:
    def __init__(self, settings=None, api: Any = None, ydl_cls: Any = None, egress: EgressProvider | None = None):
        self.settings = settings or get_settings()
        self._api = api
        self._ydl_cls = ydl_cls
        self._egress = egress
        self.calls = 0

    def _provider(self) -> EgressProvider:
        if self._egress is not None:
            return self._egress
        # Injected API fakes in unit tests: skip process-wide slow gate.
        if self._api is not None:
            return StaticEgress(
                proxy_url=_settings_proxy(self.settings),
                concurrency=8,
                min_interval_s=0.0,
                backoff_s=0.0,
                jitter_s=0.0,
                label="test",
            )
        return get_caption_egress()

    def _build_api(self, proxy: Optional[str] = None):
        if self._api is not None:
            return self._api
        from youtube_transcript_api import YouTubeTranscriptApi

        proxy_config = None
        if proxy:
            try:
                from youtube_transcript_api.proxies import GenericProxyConfig

                proxy_config = GenericProxyConfig(http_url=proxy, https_url=proxy)
            except ImportError:
                logger.warning(
                    "youtube_transcript_api.proxies unavailable; skipping GenericProxyConfig "
                    "(env HTTP_PROXY/HTTPS_PROXY still apply if the HTTP stack reads them)"
                )
        if proxy_config is not None:
            try:
                return YouTubeTranscriptApi(proxy_config=proxy_config)
            except TypeError:
                logger.warning("YouTubeTranscriptApi does not accept proxy_config; constructing without it")
        return YouTubeTranscriptApi()

    def fetch(self, video_id: str) -> TranscriptResult:
        """Caption cascade: API human → yt-dlp manual → API auto → yt-dlp auto.

        Auto tracks are valid (is_generated=True). Worker STT runs only if this
        returns ok=False. Outbound path goes through EgressProvider (static gate now).
        """
        self.calls += 1
        provider = self._provider()
        handle = provider.acquire()
        try:
            result = self._fetch_with_proxy(video_id, proxy=handle.proxy_url)
            if result.ok:
                provider.report(handle, "ok")
            elif is_rate_limited_message(result.error_msg or ""):
                provider.report(handle, "rate_limited")
            else:
                provider.report(handle, "error")
            return result
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            if is_rate_limited_message(msg):
                provider.report(handle, "rate_limited")
            else:
                provider.report(handle, "error")
            logger.warning("transcript fetch crashed for %s: %s", video_id, msg)
            return TranscriptResult(ok=False, error_msg=sanitize_caption_failure_message(msg))
        finally:
            provider.release(handle)

    def _fetch_with_proxy(self, video_id: str, *, proxy: Optional[str]) -> TranscriptResult:
        errors: list[str] = []
        human_track = None
        auto_track = None

        try:
            api = self._build_api(proxy)
            transcript_list = api.list(video_id)
            human_track, auto_track = _split_tracks(transcript_list)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            errors.append(msg)
            logger.warning("transcript API list failed for %s: %s", video_id, msg)

        human_result = self._try_track(human_track, errors)
        if human_result is not None:
            return human_result

        ytdlp_manual = self._try_ytdlp(video_id, automatic=False, errors=errors, proxy=proxy)
        if ytdlp_manual is not None:
            return ytdlp_manual

        auto_result = self._try_track(auto_track, errors)
        if auto_result is not None:
            return auto_result

        ytdlp_auto = self._try_ytdlp(video_id, automatic=True, errors=errors, proxy=proxy)
        if ytdlp_auto is not None:
            return ytdlp_auto

        raw = "; ".join(errors) if errors else "该视频没有任何可用字幕轨"
        logger.warning("transcript fetch failed for %s: %s", video_id, raw)
        return TranscriptResult(ok=False, error_msg=sanitize_caption_failure_message(raw))

    def _try_track(self, transcript, errors: list[str]) -> Optional[TranscriptResult]:
        if transcript is None:
            return None
        try:
            fetched = transcript.fetch()
            cues = self._to_cues(fetched)
            if not cues:
                raise ValueError("字幕轨存在但内容为空")
            text = " ".join(c.text for c in cues).strip()
            return TranscriptResult(
                ok=True,
                cues=cues,
                text=text,
                language=getattr(transcript, "language_code", "unknown"),
                is_generated=getattr(transcript, "is_generated", None),
            )
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            errors.append(msg)
            logger.warning("transcript track fetch failed: %s", msg)
            return None

    def _try_ytdlp(
        self,
        video_id: str,
        *,
        automatic: bool,
        errors: list[str],
        proxy: Optional[str] = None,
    ) -> Optional[TranscriptResult]:
        try:
            from app.services.captions_ytdlp import fetch_subtitles

            payload = fetch_subtitles(
                video_id,
                automatic=automatic,
                ydl_cls=self._ydl_cls,
                proxy=proxy if proxy is not None else _settings_proxy(self.settings),
            )
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
            return None
        if not payload:
            return None
        raw_cues = payload.get("cues") or []
        cues = [
            Cue(start=float(c.get("start") or 0), duration=float(c.get("duration") or 0), text=str(c.get("text") or "").strip())
            for c in raw_cues
            if str(c.get("text") or "").strip()
        ]
        if not cues:
            return None
        return TranscriptResult(
            ok=True,
            cues=cues,
            text=" ".join(c.text for c in cues).strip(),
            language=str(payload.get("language") or "unknown"),
            is_generated=bool(payload.get("is_generated")),
        )

    @staticmethod
    def _to_cues(fetched) -> list[Cue]:

        cues: list[Cue] = []
        for item in fetched:
            if isinstance(item, dict):
                start = float(item.get("start") or 0)
                duration = float(item.get("duration") or 0)
                text = (item.get("text") or "").replace("\n", " ").strip()
            else:
                start = float(getattr(item, "start", 0) or 0)
                duration = float(getattr(item, "duration", 0) or 0)
                text = (getattr(item, "text", "") or "").replace("\n", " ").strip()
            if text:
                cues.append(Cue(start=start, duration=duration, text=text))
        return cues
