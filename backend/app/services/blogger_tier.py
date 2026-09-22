# -*- coding: utf-8 -*-
"""Classify YouTube channels into 博主层级 by subscriber count."""
from __future__ import annotations

from typing import Iterable, Optional

# Default breaks: 素人 <10k, 小博主 <100k, 腰部 <1M, else 头部
DEFAULT_BREAKS = (10_000, 100_000, 1_000_000)
TIER_LABELS = ("素人", "小博主", "腰部博主", "头部博主")


def parse_breaks(raw: str | None) -> tuple[int, int, int]:
    """Parse '10000,100000,1000000' into three ascending thresholds."""
    if not raw or not str(raw).strip():
        return DEFAULT_BREAKS
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    if len(parts) != 3:
        return DEFAULT_BREAKS
    try:
        a, b, c = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return DEFAULT_BREAKS
    if not (0 < a < b < c):
        return DEFAULT_BREAKS
    return (a, b, c)


def classify_blogger_tier(
    subscriber_count: Optional[int],
    *,
    breaks: Iterable[int] | None = None,
) -> str:
    """Return Chinese tier label, or empty string if count unknown."""
    if subscriber_count is None:
        return ""
    try:
        n = int(subscriber_count)
    except (TypeError, ValueError):
        return ""
    if n < 0:
        return ""
    b = tuple(breaks) if breaks is not None else DEFAULT_BREAKS
    if len(b) != 3:
        b = DEFAULT_BREAKS
    t0, t1, t2 = b
    if n < t0:
        return TIER_LABELS[0]
    if n < t1:
        return TIER_LABELS[1]
    if n < t2:
        return TIER_LABELS[2]
    return TIER_LABELS[3]
