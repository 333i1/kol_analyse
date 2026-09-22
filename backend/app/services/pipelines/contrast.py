
"""Deterministic contrast. Zero LLM."""
from __future__ import annotations

from typing import Any, Optional

from app.services.banned import rewrite_if_banned

_FALLBACK_BOTH = "内容与评论均不可用，无法对照。"
_CONTENT_FAIL = "无转录，无法概括内容。"
_COMMENTS_FAIL = "无评论样本。"
_FAIL_MARKERS = ("LLM失败", "超预算", "无可分析评论")


def _content_fail_sentence(content: dict[str, Any] | None) -> str:
    skip = str((content or {}).get("skip_reason") or "").strip()
    if skip.startswith("LLM失败"):
        return skip
    return _CONTENT_FAIL


def _comments_fail_sentence(comments: dict[str, Any] | None) -> str:
    label = str(((comments or {}).get("noise") or {}).get("label") or "")
    for marker in _FAIL_MARKERS:
        if marker in label:
            if "；" in label:
                return label.split("；")[-1].strip() or marker
            return marker
    return _COMMENTS_FAIL


def build_contrast(
    *,
    content: dict[str, Any] | None,
    comments: dict[str, Any] | None,
    freshness_tags: list[str],
    fetched_at_label: str,
) -> dict[str, Any]:
    content_ok = bool(content) and content.get("pipeline_status") != "failed"
    comments_ok = bool(comments) and comments.get("pipeline_status") != "failed"

    if content_ok:
        thesis = (content.get("content_conclusion") or {}).get("thesis") or ""
        content_says = thesis
        primary_type = ((content.get("content_conclusion") or {}).get("video_types") or ["其他"])[0]
    else:
        content_says = _content_fail_sentence(content)
        primary_type = None

    if comments_ok:
        doing = (comments.get("comment_conclusion") or {}).get("doing") or ""
        comments_reply = doing
        reactions = comments.get("reaction_types") or []
        primary_reaction = reactions[0]["type"] if reactions else None
    else:
        comments_reply = _comments_fail_sentence(comments)
        primary_reaction = None

    if not content_ok and not comments_ok:
        sentence = _FALLBACK_BOTH
    else:
        thesis_short = (content_says[:24] + "…") if len(content_says) > 24 else content_says
        doing_short = (comments_reply[:24] + "…") if len(comments_reply) > 24 else comments_reply
        if primary_type == "教程" and primary_reaction == "提问":
            sentence = "视频在教步骤，评论在要排障。"
        elif primary_type == "观点" and primary_reaction in ("反驳", "共鸣"):
            sentence = f"视频在讲「{thesis_short}」，评论在「{doing_short}」。"
        else:
            sentence = f"视频在讲「{thesis_short}」，评论在「{doing_short}」。"

    return {
        "content_says": rewrite_if_banned(content_says),
        "comments_reply": rewrite_if_banned(comments_reply),
        "contrast_sentence": rewrite_if_banned(sentence),
        "freshness_tags": freshness_tags or ["样本范围未知"],
        "fetched_at_label": fetched_at_label or "抓取时间未知",
    }
