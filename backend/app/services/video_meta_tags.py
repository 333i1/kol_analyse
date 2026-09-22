# -*- coding: utf-8 -*-
"""Derive language / short|video / commercial clues from YouTube metadata + text."""
from __future__ import annotations

import re
from typing import Optional

# Shorts: URL path or duration ≤ 60s (YouTube Shorts classic length).
SHORT_MAX_SECONDS = 60

_LANG_MAP = {
    "zh": "中文",
    "zh-cn": "中文",
    "zh-hans": "中文",
    "zh-sg": "中文",
    "zh-tw": "中文（繁体）",
    "zh-hant": "中文（繁体）",
    "zh-hk": "中文（繁体）",
    "en": "英语",
    "en-us": "英语",
    "en-gb": "英语",
    "en-au": "英语",
    "en-ca": "英语",
    "ja": "日语",
    "ja-jp": "日语",
    "ko": "韩语",
    "ko-kr": "韩语",
    "es": "西班牙语",
    "es-es": "西班牙语",
    "es-mx": "西班牙语",
    "es-419": "西班牙语",
    "pt": "葡萄牙语",
    "pt-br": "葡萄牙语",
    "pt-pt": "葡萄牙语",
    "fr": "法语",
    "de": "德语",
    "it": "意大利语",
    "ru": "俄语",
    "ar": "阿拉伯语",
    "hi": "印地语",
    "th": "泰语",
    "vi": "越南语",
    "id": "印尼语",
    "ms": "马来语",
    "tr": "土耳其语",
    "pl": "波兰语",
    "nl": "荷兰语",
    "sv": "瑞典语",
    "uk": "乌克兰语",
}


def classify_format_kind(
    *,
    url: str | None = None,
    duration_seconds: int | None = None,
) -> str:
    """Return ``short`` or ``video`` for CSV column short/video."""
    u = (url or "").lower()
    if "/shorts/" in u:
        return "short"
    if duration_seconds is not None:
        try:
            d = int(duration_seconds)
        except (TypeError, ValueError):
            d = -1
        if 0 < d <= SHORT_MAX_SECONDS:
            return "short"
        if d > SHORT_MAX_SECONDS:
            return "video"
    return "video"


def _map_lang_code(code: str | None) -> str:
    if not code or not str(code).strip():
        return ""
    raw = str(code).strip().replace("_", "-")
    low = raw.lower()
    if low in _LANG_MAP:
        return _LANG_MAP[low]
    primary = low.split("-", 1)[0]
    if primary in _LANG_MAP:
        return _LANG_MAP[primary]
    return raw


def detect_language_from_text(title: str = "", description: str = "") -> str:
    """Very light heuristic when YouTube language fields are empty."""
    text = f"{title or ''}\n{description or ''}"
    if not text.strip():
        return ""
    # Prefer scripts with stronger signal
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    kana = len(re.findall(r"[\u3040-\u30ff]", text))
    hangul = len(re.findall(r"[\uac00-\ud7af]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if kana >= 8 or (kana >= 3 and kana >= cjk):
        return "日语"
    if hangul >= 8 or (hangul >= 3 and hangul >= cjk):
        return "韩语"
    if cjk >= 6 and cjk >= latin // 2:
        return "中文"
    if latin >= 12 and latin > cjk * 2:
        return "英语"
    return ""


def normalize_language(
    *,
    default_audio_language: str | None = None,
    default_language: str | None = None,
    title: str = "",
    description: str = "",
) -> str:
    """Prefer YouTube defaultAudioLanguage, then defaultLanguage, then text heuristic."""
    for code in (default_audio_language, default_language):
        mapped = _map_lang_code(code)
        if mapped:
            return mapped
    return detect_language_from_text(title, description)


_COMMERCIAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"#\s*ad\b|\#ad\b", re.I), "#ad"),
    (re.compile(r"#\s*sponsored\b|\#sponsored\b", re.I), "#sponsored"),
    (re.compile(r"\bpaid\s+partnership\b", re.I), "paid partnership"),
    (re.compile(r"\bsponsored\s+by\b", re.I), "sponsored by"),
    (re.compile(r"\baffiliate\b", re.I), "affiliate"),
    (re.compile(r"\bdiscount\s+code\b|\buse\s+code\b", re.I), "discount code"),
    (re.compile(r"包含付费推广|付费推广|有付费推广", re.I), "付费推广"),
    (re.compile(r"广告合作|品牌合作|商务合作|商业合作", re.I), "合作"),
    (re.compile(r"赞助|商单|软广|硬广|带货|植入广告|广告植入", re.I), "赞助/商单"),
    (re.compile(r"优惠码|折扣码|购买链接|专属链接", re.I), "优惠/购买链接"),
    (re.compile(r"本视频由.{0,20}(提供|赞助|冠名)", re.I), "品牌提供/赞助"),
]


def infer_commercial(
    *,
    title: str = "",
    description: str = "",
    has_paid_product_placement: bool | None = None,
) -> Optional[str]:
    """Return a short commercial fact sentence, or None if no clear clue."""
    if has_paid_product_placement is True:
        return "平台标记：含付费产品植入"
    blob = f"{title or ''}\n{description or ''}"
    if not blob.strip():
        return None
    hits: list[str] = []
    for pat, label in _COMMERCIAL_PATTERNS:
        if pat.search(blob):
            if label not in hits:
                hits.append(label)
            if len(hits) >= 3:
                break
    if not hits:
        return None
    return "标题/简介含商单线索（" + "、".join(hits) + "）"


def extract_paid_placement(metadata: dict | None) -> bool | None:
    """Read videos.list paidProductPlacementDetails when present."""
    if not isinstance(metadata, dict):
        return None
    pp = metadata.get("paidProductPlacementDetails")
    if not isinstance(pp, dict):
        return None
    if "hasPaidProductPlacement" not in pp:
        return None
    return bool(pp.get("hasPaidProductPlacement"))
