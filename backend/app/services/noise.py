
"""Deterministic comment noise marking. Mark, do not delete. No buy-intent whitelist."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

NOISE_PURE_URL = "pure_url"
NOISE_AD_SPAM = "ad_spam"
NOISE_TOO_SHORT = "too_short"
NOISE_REPEATED_CHAR = "repeated_char"
NOISE_PURE_SYMBOL = "pure_symbol"
NOISE_DUPLICATE = "duplicate"

MIN_TEXT_LENGTH = 2
REPEATED_CHAR_LIMIT = 8

_URL_RE = re.compile(r"https?://\S+|www\.\S+|\S+\.(?:com|cn|net|org|io)/\S*", re.I)
_AD_PATTERNS = (
    re.compile(r"(加|私|➕)\s*(微信|vx|v信|威信|wechat|qq)", re.I),
    re.compile(r"(免费|一元|1元|低价)\s*(领|抢|送|拿)"),
    re.compile(r"(点击|戳)\s*(链接|下方|主页).{0,6}(领取|购买|下单)"),
    re.compile(r"(刷单|兼职|日入|月入)\s*\d"),
    re.compile(r"\b[a-z0-9_]{6,}\.(top|xyz|club|vip)\b", re.I),
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[a-zA-Z]")


@dataclass
class PreprocessResult:
    comment_id: str
    video_id: str
    text: str
    cleaned_text: str
    like_count: int = 0
    reply_count: int = 0
    is_noise: bool = False
    noise_reason: str | None = None
    language: str = "other"
    notes: list[str] = field(default_factory=list)

    @property
    def should_analyze(self) -> bool:
        return not self.is_noise and bool(self.cleaned_text.strip())


def text_fingerprint(text: str) -> str:
    norm = unicodedata.normalize("NFKC", (text or "").strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def detect_language(text: str) -> str:
    if not text:
        return "other"
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    if cjk == 0 and latin == 0:
        return "other"
    if cjk * 3 >= latin:
        return "zh" if cjk else "other"
    return "en"


def _has_repeated_char(text: str, limit: int = REPEATED_CHAR_LIMIT) -> bool:
    if len(text) < limit:
        return False
    run_char = ""
    run_len = 0
    for ch in text:
        if ch == run_char:
            run_len += 1
            if run_len >= limit:
                return True
        else:
            run_char = ch
            run_len = 1
    return False


def _is_pure_symbol(text: str) -> bool:
    if not text:
        return False
    return all(
        unicodedata.category(ch).startswith(("P", "S", "Z", "C")) or ch.isspace()
        for ch in text
    )


def _is_pure_url(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    without = _URL_RE.sub("", stripped).strip()
    return (not without) and bool(_URL_RE.search(stripped))


def _is_ad_spam(text: str) -> bool:
    return any(p.search(text) for p in _AD_PATTERNS)


def preprocess_comment(
    comment: dict,
    *,
    seen_fingerprints: set[str] | None = None,
    video_id: str | None = None,
) -> PreprocessResult:
    cid = str(comment.get("comment_id") or comment.get("id") or "")
    vid = str(video_id or comment.get("video_id") or "")
    text = str(comment.get("text") or "")
    cleaned = unicodedata.normalize("NFKC", text).strip()
    result = PreprocessResult(
        comment_id=cid,
        video_id=vid,
        text=text,
        cleaned_text=cleaned,
        like_count=int(comment.get("like_count") or 0),
        reply_count=int(comment.get("reply_count") or 0),
        language=detect_language(cleaned),
    )
    if _is_pure_url(cleaned):
        result.is_noise = True
        result.noise_reason = NOISE_PURE_URL
        return result
    if _is_ad_spam(cleaned):
        result.is_noise = True
        result.noise_reason = NOISE_AD_SPAM
        return result
    if _is_pure_symbol(cleaned):
        result.is_noise = True
        result.noise_reason = NOISE_PURE_SYMBOL
        return result
    if _has_repeated_char(cleaned):
        result.is_noise = True
        result.noise_reason = NOISE_REPEATED_CHAR
        return result
    # length after stripping urls/spaces
    compact = _URL_RE.sub("", cleaned).strip()
    if len(compact) < MIN_TEXT_LENGTH:
        result.is_noise = True
        result.noise_reason = NOISE_TOO_SHORT
        return result
    fp = text_fingerprint(cleaned)
    if seen_fingerprints is not None:
        if fp in seen_fingerprints:
            result.is_noise = True
            result.noise_reason = NOISE_DUPLICATE
            return result
        seen_fingerprints.add(fp)
    return result


def preprocess_comments(comments: list[dict], video_id: str | None = None) -> list[PreprocessResult]:
    seen: set[str] = set()
    return [preprocess_comment(c, seen_fingerprints=seen, video_id=video_id) for c in comments]
