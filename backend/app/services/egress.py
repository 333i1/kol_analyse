# -*- coding: utf-8 -*-
"""Caption egress: single-IP static now; pool mode reserved (falls back to static)."""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Literal, Optional, Protocol

from app.config import normalize_proxy_url

logger = logging.getLogger(__name__)

EgressOutcome = Literal["ok", "rate_limited", "dead", "error"]


@dataclass(frozen=True)
class EgressHandle:
    """One acquired outbound path for a caption fetch."""

    proxy_url: Optional[str]
    label: str = "static"
    # Internal token for provider bookkeeping (not for callers).
    _token: int = 0


class EgressProvider(Protocol):
    def acquire(self) -> EgressHandle: ...

    def report(self, handle: EgressHandle, outcome: EgressOutcome) -> None: ...

    def release(self, handle: EgressHandle) -> None: ...


def parse_proxy_list(raw: str | None) -> list[str]:
    """Split CAPTION_PROXIES (comma / whitespace / newline). Empty entries dropped."""
    if not raw or not str(raw).strip():
        return []
    parts: list[str] = []
    for chunk in str(raw).replace("\n", ",").replace(";", ",").split(","):
        for piece in chunk.split():
            u = normalize_proxy_url(piece)
            if u:
                parts.append(u)
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


class StaticEgress:
    """Fixed single egress + global caption gate (concurrency + min interval + backoff)."""

    def __init__(
        self,
        *,
        proxy_url: Optional[str] = None,
        concurrency: int = 1,
        min_interval_s: float = 10.0,
        backoff_s: float = 90.0,
        jitter_s: float = 2.0,
        label: str = "static",
        sleeper=None,
        clock=None,
    ):
        self.proxy_url = normalize_proxy_url(proxy_url) if proxy_url else None
        self.concurrency = max(1, int(concurrency or 1))
        self.min_interval_s = max(0.0, float(min_interval_s or 0))
        self.backoff_s = max(0.0, float(backoff_s or 0))
        self.jitter_s = max(0.0, float(jitter_s or 0))
        self.label = label or "static"
        self._sleep = sleeper or time.sleep
        self._clock = clock or time.monotonic
        self._sem = threading.Semaphore(self.concurrency)
        self._gate = threading.Lock()
        self._next_ok_at = 0.0
        self._token_seq = 0
        self._held: set[int] = set()

    def acquire(self) -> EgressHandle:
        self._sem.acquire()
        try:
            with self._gate:
                now = self._clock()
                wait = max(0.0, self._next_ok_at - now)
            if wait > 0:
                logger.info(
                    "caption egress wait %.1fs (interval/backoff) label=%s",
                    wait,
                    self.label,
                )
                self._sleep(wait)
            with self._gate:
                now = self._clock()
                gap = self.min_interval_s
                if self.jitter_s > 0:
                    gap += random.uniform(0, self.jitter_s)
                self._next_ok_at = max(self._next_ok_at, now + gap)
                self._token_seq += 1
                token = self._token_seq
                self._held.add(token)
            return EgressHandle(proxy_url=self.proxy_url, label=self.label, _token=token)
        except Exception:
            self._sem.release()
            raise

    def report(self, handle: EgressHandle, outcome: EgressOutcome) -> None:
        if outcome == "rate_limited" and self.backoff_s > 0:
            with self._gate:
                now = self._clock()
                extra = self.backoff_s
                if self.jitter_s > 0:
                    extra += random.uniform(0, self.jitter_s)
                self._next_ok_at = max(self._next_ok_at, now + extra)
            logger.warning(
                "caption egress rate_limited; backoff %.1fs label=%s",
                self.backoff_s,
                handle.label,
            )
        elif outcome == "dead":
            logger.warning("caption egress marked dead label=%s (static keeps same path)", handle.label)

    def release(self, handle: EgressHandle) -> None:
        with self._gate:
            self._held.discard(handle._token)
        self._sem.release()


_provider_lock = threading.Lock()
_provider: Optional[EgressProvider] = None


def reset_caption_egress_for_tests() -> None:
    """Clear process-wide singleton (unit tests)."""
    global _provider
    with _provider_lock:
        _provider = None


def build_caption_egress(settings: Optional[object] = None) -> EgressProvider:
    """Construct provider from settings. pool mode is reserved → fallback static."""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()

    mode = str(getattr(settings, "CAPTION_EGRESS", "static") or "static").strip().lower()
    proxies = parse_proxy_list(getattr(settings, "CAPTION_PROXIES", "") or "")
    # Prefer explicit caption proxy list first entry; else legacy ALL_PROXY/HTTP(S)_PROXY
    legacy = None
    fn = getattr(settings, "proxy_url", None)
    if callable(fn):
        try:
            legacy = fn()
        except Exception:
            legacy = None
    proxy = proxies[0] if proxies else legacy

    if mode == "pool":
        logger.warning(
            "CAPTION_EGRESS=pool is reserved and not implemented yet; "
            "falling back to static (first proxy or ALL_PROXY/direct)"
        )
        mode = "static"

    if mode != "static":
        logger.warning("unknown CAPTION_EGRESS=%r; using static", mode)

    return StaticEgress(
        proxy_url=proxy,
        concurrency=int(getattr(settings, "CAPTION_CONCURRENCY", 1) or 1),
        min_interval_s=float(getattr(settings, "CAPTION_MIN_INTERVAL_S", 10.0) or 0),
        backoff_s=float(getattr(settings, "CAPTION_BACKOFF_S", 90.0) or 0),
        jitter_s=float(getattr(settings, "CAPTION_JITTER_S", 2.0) or 0),
        label="static",
    )


def get_caption_egress() -> EgressProvider:
    """Process-wide caption egress (shared gate across tasks)."""
    global _provider
    with _provider_lock:
        if _provider is None:
            _provider = build_caption_egress()
        return _provider


def is_rate_limited_message(msg: str) -> bool:
    s = (msg or "")
    # Sanitized user-facing notes may drop the literal "429".
    if "限流" in s or "机房 IP" in s:
        return True
    return bool(
        __import__("re").search(
            r"\b429\b|Too Many Requests|rate.?limit|RequestBlocked|IpBlocked|"
            r"blocking requests from your IP|cloud provider",
            s,
            __import__("re").I,
        )
    )
