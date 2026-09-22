
"""Scan contrast / conclusion strings for banned verdict words."""
from __future__ import annotations

BANNED_TERMS = ("危机", "适合合作")
FALLBACK_CONTRAST = "两边摘要已给出，对照见内容结论与评论结论。"


def contains_banned(text: str | None) -> bool:
    if not text:
        return False
    return any(term in text for term in BANNED_TERMS)


def rewrite_if_banned(text: str | None, fallback: str = FALLBACK_CONTRAST) -> str:
    if text is None:
        return fallback
    return fallback if contains_banned(text) else text


def scan_mapping(values: dict[str, str | None]) -> dict[str, str]:
    return {k: rewrite_if_banned(v) for k, v in values.items()}
