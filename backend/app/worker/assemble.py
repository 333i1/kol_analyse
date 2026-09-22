"""Result document assembly and schema repair. Prompts / banned list / HTTP contract unchanged."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.banned import rewrite_if_banned
from app.services.pipelines.contrast import build_contrast
from app.services.pipelines.content import _coerce_topics

SCHEMA_PATH = None  # resolved relative to repo

VIDEO_TYPES = {"教程", "评测", "日常", "观点", "资讯", "娱乐", "游戏", "音乐", "影视解说", "体育", "喜剧", "其他"}
TONES = {"解说", "吐槽", "陪伴", "严肃", "表演", "论证", "其他"}
REACTIONS = {"共鸣", "反驳", "提问", "纠错", "玩梗", "点名时间戳", "求更新", "分享经历", "其他"}
CTA_TYPES = {"关注", "下一集", "投票", "购买", "收藏", "评论", "其他"}


def _schema_path() -> Path:
    """Prefer schema bundled inside the backend package (publish mounts only backend/)."""
    here = Path(__file__).resolve()
    bundled = here.parents[1] / "schema_assets" / "video-analysis-result.schema.json"
    candidates = [
        bundled,
        # Local monorepo fallbacks when running from a full checkout
        here.parents[3] / "docs" / "02-施工" / "schema" / "video-analysis-result.schema.json",
        Path.cwd() / "docs" / "02-施工" / "schema" / "video-analysis-result.schema.json",
        Path.cwd().parent / "docs" / "02-施工" / "schema" / "video-analysis-result.schema.json",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def _validate_result_doc(result: dict[str, Any]) -> None:
    """Import jsonschema inside this helper so a failed import cannot UnboundLocalError the caller."""
    import jsonschema

    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    jsonschema.validate(result, schema)


def iso8601_duration_to_seconds(text: str | None) -> int | None:
    if not text:
        return None
    import re

    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", text)
    if not m:
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return "0:00"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_count(n: int) -> str:
    if n >= 10000:
        v = n / 10000
        return f"{v:.1f} 万".replace(".0 ", " ")
    return str(n)


def published_ago(published_at: str | None, now: datetime | None = None) -> tuple[float, str]:
    now = now or datetime.now(timezone.utc)
    if not published_at:
        return 0.0, "未知"
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = max(0.0, (now - dt).total_seconds() / 86400.0)
    except Exception:
        return 0.0, "未知"
    if days < 1:
        return days, "不到 1 天"
    d = int(days)
    return float(d), f"{d} 天"


def _clamp01(value, default: float = 0.7) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if x != x or x in (float("inf"), float("-inf")):
        return default
    return max(0.0, min(1.0, x))


def _as_sentence(value, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        s = value.strip()
    elif isinstance(value, (list, tuple)):
        parts = [str(x).strip() for x in value if str(x).strip()]
        s = "、".join(parts) if parts else fallback
    elif value is None:
        s = fallback
    else:
        s = str(value).strip() or fallback
    return rewrite_if_banned(s, fallback)


def _format_schema_error(exc: BaseException) -> str:
    path = getattr(exc, "absolute_path", None)
    msg = getattr(exc, "message", None) or str(exc)
    if path is not None:
        loc = "/".join(str(p) for p in path) or "$"
        return f"{loc}: {msg}"
    return f"{type(exc).__name__}: {exc}"


def _failed_content(reason: str = "结果校验失败") -> dict[str, Any]:
    return {"pipeline_status": "failed", "skip_reason": reason or "结果校验失败", "segments": []}


def _failed_comments() -> dict[str, Any]:
    return {
        "pipeline_status": "failed",
        "low_confidence_items": [],
        "human_review_items": [],
        "noise": {
            "filtered_count": 0,
            "filtered_ratio": 0.0,
            "label": "噪音已过滤 0 条（约 0%）",
        },
        "reply_heat": {"reply_count": None, "included_in_sentiment": False},
    }


def _repair_content(ca: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ca, dict):
        return _failed_content()
    if ca.get("skip_reason") == "user_skipped_stt":
        cc = ca.get("content_conclusion") if isinstance(ca.get("content_conclusion"), dict) else {}
        types_in = cc.get("video_types") or ["其他"]
        if not isinstance(types_in, list):
            types_in = [types_in]
        vtypes = []
        seen = set()
        for t in types_in:
            v = t if t in VIDEO_TYPES else "其他"
            if v not in seen:
                seen.add(v)
                vtypes.append(v)
        repaired_cc = {
            "video_types": vtypes or ["其他"],
            "topics": _coerce_topics(cc.get("topics")),
            "thesis": _as_sentence(cc.get("thesis"), "内容摘要不可用。"),
            "tone": cc.get("tone") if cc.get("tone") in TONES else "其他",
            "tone_description": str(cc.get("tone_description") or "无转录，语气未判定。").strip() or "无转录，语气未判定。",
            "commercial": None,
            "confidence": _clamp01(cc.get("confidence"), 0.4),
            "coverage_label": str(cc.get("coverage_label") or "无语音转录，仅标题/简介").strip() or "无语音转录，仅标题/简介",
            "coverage_ratio": None if cc.get("coverage_ratio") is None else _clamp01(cc.get("coverage_ratio"), 0.0),
        }
        return {
            "pipeline_status": "degraded",
            "skip_reason": "user_skipped_stt",
            "segments": [],
            "content_conclusion": repaired_cc,
        }
    status = ca.get("pipeline_status")
    if status == "failed":
        return {
            "pipeline_status": "failed",
            "skip_reason": str(ca.get("skip_reason") or "无转录"),
            "segments": ca.get("segments") if isinstance(ca.get("segments"), list) else [],
        }
    segs = []
    for kind, raw in zip(("开场", "展开", "收束"), list(ca.get("segments") or []) + [{}, {}, {}]):
        s = raw if isinstance(raw, dict) else {}
        captions = []
        for cap in s.get("captions") or []:
            if not isinstance(cap, dict):
                continue
            start = str(cap.get("start") or "00:00")
            txt = str(cap.get("text") or "").strip() or "…"
            captions.append({"start": start, "text": txt})
        if not captions:
            captions = [{"start": "00:00", "text": "（无字幕片段）"}]
        cta = s.get("cta_type")
        if kind != "收束":
            cta = None
        elif cta not in CTA_TYPES and cta is not None:
            cta = "其他"
        ad = s.get("ad_overlay")
        if ad == "":
            ad = None
        segs.append(
            {
                "kind": kind,
                "confidence": _clamp01(s.get("confidence"), 0.7),
                "summary": str(s.get("summary") or f"{kind}摘要").strip() or f"{kind}摘要",
                "cta_type": cta,
                "ad_overlay": ad,
                "captions": captions,
            }
        )
    cc = ca.get("content_conclusion") if isinstance(ca.get("content_conclusion"), dict) else {}
    types_in = cc.get("video_types") or ["其他"]
    if not isinstance(types_in, list):
        types_in = [types_in]
    vtypes: list[str] = []
    seen: set[str] = set()
    for t in types_in:
        v = t if t in VIDEO_TYPES else "其他"
        if v not in seen:
            seen.add(v)
            vtypes.append(v)
    commercial = cc.get("commercial", None)
    if commercial == "":
        commercial = None
    if isinstance(commercial, str):
        commercial = rewrite_if_banned(commercial, "提及商业信息，详见内容摘要。")
    ratio = cc.get("coverage_ratio")
    if ratio is not None:
        ratio = _clamp01(ratio, 0.0)
    repaired_cc = {
        "video_types": vtypes or ["其他"],
        "topics": _coerce_topics(cc.get("topics")),
        "thesis": _as_sentence(cc.get("thesis"), "内容摘要不可用。"),
        "tone": cc.get("tone") if cc.get("tone") in TONES else "其他",
        "tone_description": str(cc.get("tone_description") or "语气未判定。").strip() or "语气未判定。",
        "commercial": commercial,
        "confidence": _clamp01(cc.get("confidence"), 0.7),
        "coverage_label": str(cc.get("coverage_label") or "覆盖率未知").strip() or "覆盖率未知",
        "coverage_ratio": ratio,
    }
    out = {
        "pipeline_status": status if status in ("ok", "degraded", "failed") else "ok",
        "skip_reason": ca.get("skip_reason"),
        "segments": segs,
        "content_conclusion": repaired_cc,
    }
    if out["skip_reason"] == "":
        out["skip_reason"] = None
    return out


def _repair_comments(cm: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cm, dict):
        return _failed_comments()
    if cm.get("pipeline_status") == "failed":
        noise = cm.get("noise") if isinstance(cm.get("noise"), dict) else {
            "filtered_count": 0,
            "filtered_ratio": 0.0,
            "label": "噪音已过滤 0 条（约 0%）",
        }
        return {
            "pipeline_status": "failed",
            "low_confidence_items": [],
            "human_review_items": [],
            "noise": {
                "filtered_count": int(noise.get("filtered_count") or 0),
                "filtered_ratio": _clamp01(noise.get("filtered_ratio"), 0.0),
                "label": str(noise.get("label") or "噪音已过滤 0 条（约 0%）"),
            },
            "reply_heat": cm.get("reply_heat")
            if isinstance(cm.get("reply_heat"), dict)
            else {"reply_count": None, "included_in_sentiment": False},
        }
    conc = cm.get("comment_conclusion") if isinstance(cm.get("comment_conclusion"), dict) else {}
    low = []
    for it in cm.get("low_confidence_items") or []:
        if not isinstance(it, dict):
            continue
        conf = _clamp01(it.get("confidence"), 0.0)
        if conf >= 0.6:
            continue
        txt = str(it.get("text") or "").strip() or "（空）"
        low.append({"text": txt, "confidence": conf})
    reactions = []
    for r in cm.get("reaction_types") or []:
        if not isinstance(r, dict):
            continue
        rtype = r.get("type") if r.get("type") in REACTIONS else "其他"
        examples = [str(x) for x in (r.get("examples") or []) if str(x)]
        reactions.append(
            {
                "type": rtype,
                "display_label": str(r.get("display_label") or rtype).strip() or rtype,
                "count": int(r.get("count") or 0),
                "examples": examples or ["（无）"],
            }
        )
    if not reactions:
        reactions = [{"type": "其他", "display_label": "其他", "count": 0, "examples": ["（无）"]}]
    themes = []
    for t in cm.get("open_themes") or []:
        if not isinstance(t, dict):
            continue
        examples = [str(x) for x in (t.get("examples") or []) if str(x)]
        item = {
            "name": str(t.get("name") or "其他").strip() or "其他",
            "examples": examples or ["（无）"],
        }
        if t.get("weight") is not None:
            try:
                item["weight"] = int(t.get("weight") or 0)
            except (TypeError, ValueError):
                item["weight"] = 0
        themes.append(item)
    if not themes:
        themes = [{"name": "其他", "examples": ["（无）"], "weight": 0}]
    noise = cm.get("noise") if isinstance(cm.get("noise"), dict) else {}
    sent = cm.get("sentiment") if isinstance(cm.get("sentiment"), dict) else {"positive_pct": 0, "neutral_pct": 0, "negative_pct": 0}
    out = {
        "pipeline_status": cm.get("pipeline_status") if cm.get("pipeline_status") in ("ok", "degraded", "failed") else "ok",
        "sentiment": {
            "positive_pct": int(sent.get("positive_pct") or 0),
            "neutral_pct": int(sent.get("neutral_pct") or 0),
            "negative_pct": int(sent.get("negative_pct") or 0),
        },
        "reaction_types": reactions,
        "open_themes": themes,
        "low_confidence_items": low,
        "human_review_items": [
            {"text": str(h.get("text") or "（空）").strip() or "（空）", "status": "待确认"}
            for h in (cm.get("human_review_items") or [])
            if isinstance(h, dict)
        ],
        "noise": {
            "filtered_count": int(noise.get("filtered_count") or 0),
            "filtered_ratio": _clamp01(noise.get("filtered_ratio"), 0.0),
            "label": str(noise.get("label") or "噪音已过滤 0 条（约 0%）"),
        },
        "comment_conclusion": {
            "doing": _as_sentence(conc.get("doing"), "评论信号已聚合。"),
            "themes": _as_sentence(conc.get("themes"), "主题见 open_themes。"),
            "alignment": _as_sentence(conc.get("alignment"), "对照见总述。"),
            "confidence": _clamp01(conc.get("confidence"), 0.7),
            "sample_label": str(conc.get("sample_label") or "样本未知").strip() or "样本未知",
            "sample_fetched": int(conc.get("sample_fetched") or 0),
            "sample_platform_total": conc.get("sample_platform_total"),
            "sample_ratio": None if conc.get("sample_ratio") is None else _clamp01(conc.get("sample_ratio"), 0.0),
        },
    }
    if cm.get("sentiment_examples"):
        se = cm["sentiment_examples"] if isinstance(cm["sentiment_examples"], dict) else {}
        out["sentiment_examples"] = {
            "正面": list(se.get("正面") or []),
            "中性": list(se.get("中性") or []),
            "负面": list(se.get("负面") or []),
        }
    if isinstance(cm.get("top_positive_quote"), dict):
        out["top_positive_quote"] = cm["top_positive_quote"]
    if isinstance(cm.get("top_negative_quote"), dict):
        out["top_negative_quote"] = cm["top_negative_quote"]
    if isinstance(cm.get("reply_heat"), dict):
        rh = dict(cm["reply_heat"])
        rh["included_in_sentiment"] = False
        out["reply_heat"] = rh
    return out


def _repair_result(result: dict[str, Any], schema_err: str | None = None) -> dict[str, Any]:
    result["content_analysis"] = _repair_content(result.get("content_analysis") or {})
    result["comment_analysis"] = _repair_comments(result.get("comment_analysis") or {})
    cs = result.get("contrast_summary")
    if isinstance(cs, dict):
        result["contrast_summary"] = {
            "content_says": _as_sentence(cs.get("content_says"), "无转录，无法概括内容。"),
            "comments_reply": _as_sentence(cs.get("comments_reply"), "无评论样本。"),
            "contrast_sentence": _as_sentence(cs.get("contrast_sentence"), "内容与评论均不可用，无法对照。"),
            "freshness_tags": [str(t) for t in (cs.get("freshness_tags") or []) if str(t).strip()] or ["样本范围未知"],
            "fetched_at_label": str(cs.get("fetched_at_label") or "抓取时间未知").strip() or "抓取时间未知",
        }
    health = result.get("health") if isinstance(result.get("health"), dict) else {}
    notes = [str(n).strip() for n in (health.get("notes") or []) if str(n).strip()]
    if schema_err:
        note = f"schema repaired: {schema_err}"
        if note not in notes:
            notes.append(note[:400])
    if not notes:
        notes = ["分析完成"]
    health["notes"] = notes
    result["health"] = health
    return result


def _degrade_to_failed_result(result: dict[str, Any], schema_err: str) -> dict[str, Any]:
    result["content_analysis"] = _failed_content("结果校验失败")
    result["comment_analysis"] = _failed_comments()
    tags = ["样本范围未知"]
    fetched = "抓取时间未知"
    cs = result.get("contrast_summary")
    if isinstance(cs, dict):
        tags = [str(t) for t in (cs.get("freshness_tags") or []) if str(t).strip()] or tags
        fetched = str(cs.get("fetched_at_label") or fetched)
    result["contrast_summary"] = build_contrast(
        content=result["content_analysis"],
        comments=result["comment_analysis"],
        freshness_tags=tags,
        fetched_at_label=fetched,
    )
    health = result.get("health") if isinstance(result.get("health"), dict) else {}
    notes = [str(n).strip() for n in (health.get("notes") or []) if str(n).strip()]
    notes.append(f"schema: {schema_err}"[:400])
    health["overall_level"] = "failed"
    health["overall_label"] = "结果校验失败"
    health["llm"] = {"level": "failed", "label": "大模型失败"}
    if not isinstance(health.get("source"), dict):
        health["source"] = {"level": "degraded", "label": "数据源降级"}
    if not isinstance(health.get("transcript"), dict):
        health["transcript"] = {"status": "failed", "mode": "none"}
    if not isinstance(health.get("comments"), dict):
        health["comments"] = {"status": "failed", "fetched_count": 0, "is_partial": False}
    health["notes"] = notes or ["结果校验失败"]
    result["health"] = health
    if isinstance(result.get("task"), dict):
        result["task"]["status"] = "failed"
    return result


def build_result_document(
    *,
    task,
    settings,
    title: str,
    channel: str,
    duration_seconds: int | None,
    published_at: str | None,
    view_count: int,
    like_count: int,
    fetched_n: int,
    platform_comment_count: int | None,
    is_partial: bool,
    notes: list[str],
    source_level: str,
    transcript_mode: str,
    transcript_status: str,
    comments_status: str,
    content: dict[str, Any],
    comments_out: dict[str, Any],
    subscriber_count: int | None = None,
    blogger_tier: str = "",
    channel_id: str | None = None,
    language: str = "",
    format_kind: str = "",
) -> tuple[dict[str, Any], str, bool, bool]:
    """Assemble the result dict. Returns (result, overall, content_failed, comments_failed)."""
    content_failed = content.get("pipeline_status") == "failed"
    comments_failed = comments_out.get("pipeline_status") == "failed"
    llm_level = "ok"
    if content_failed and comments_failed:
        llm_level = "failed"
    elif content_failed or comments_failed:
        llm_level = "degraded"
    if source_level == "ok" and transcript_mode == "speech_to_text":
        source_level = "degraded"

    if content_failed and comments_failed:
        overall = "failed"
        overall_label = "分析未完成"
    elif content_failed or comments_failed or source_level == "degraded":
        overall = "degraded"
        overall_label = "部分降级"
    else:
        overall = "ok"
        overall_label = "分析完成"

    if content_failed:
        sr = str(content.get("skip_reason") or "").strip()
        if sr and sr not in notes:
            notes.append(sr[:400])
    if comments_failed:
        nlabel = str((comments_out.get("noise") or {}).get("label") or "")
        extra = nlabel.split("；", 1)[-1].strip() if "；" in nlabel else ""
        if extra and extra not in notes:
            notes.append(extra[:400])
    if not notes:
        notes = ["分析完成"]

    ago_days, ago_disp = published_ago(published_at)
    duration_disp = format_duration(duration_seconds)
    comment_disp = str(fetched_n)
    unit = "条（已抓取）"
    freshness = [f"发布距今 {ago_disp}"]
    if platform_comment_count is not None:
        freshness.append(f"样本范围 {fetched_n}/{platform_comment_count}")
    else:
        freshness.append(f"样本范围 {fetched_n}")
    if is_partial:
        freshness.append("非全量")

    contrast = build_contrast(
        content=content,
        comments=comments_out,
        freshness_tags=freshness,
        fetched_at_label="抓取于刚才",
    )

    result_version = f"schema1.0.0+{settings.PROMPT_VERSION_CONTENT}+{settings.PROMPT_VERSION_COMMENTS}+{settings.LLM_MODEL}"
    result: dict[str, Any] = {
        "schema_version": "1.0.0",
        "result_version": result_version,
        "is_fixture": False,
        "video": {
            "video_id": task.video_id,
            "platform": "youtube",
            "url": f"https://www.youtube.com/watch?v={task.video_id}",
            "title": title,
            "channel_name": channel,
            "channel_id": channel_id or "",
            "duration_seconds": int(duration_seconds or 0),
            "duration_display": duration_disp,
            "subscriber_count": subscriber_count,
            "blogger_tier": blogger_tier or "",
            "language": language or "",
            "format_kind": format_kind or "",
        },
        "task": {
            "status": "completed" if overall != "failed" else "failed",
            "cache_hit": False,
            "video_id": task.video_id,
        },
        "health": {
            "overall_level": overall,
            "overall_label": overall_label,
            "source": {
                "level": source_level if not (content_failed and comments_failed and source_level == "ok") else source_level,
                "label": "数据源正常" if source_level == "ok" else ("数据源降级" if source_level == "degraded" else "数据源失败"),
            },
            "llm": {
                "level": llm_level,
                "label": "大模型正常" if llm_level == "ok" else ("大模型降级" if llm_level == "degraded" else "大模型失败"),
            },
            "notes": notes,
            "transcript": {
                "status": "ok" if transcript_status == "ok" else ("degraded" if transcript_mode == "speech_to_text" else "failed"),
                "mode": transcript_mode,
                "coverage_ratio": (content.get("content_conclusion") or {}).get("coverage_ratio")
                if not content_failed
                else None,
            },
            "comments": {
                "status": "ok" if comments_status == "ok" and not comments_failed else ("failed" if comments_failed else comments_status),
                "fetched_count": fetched_n,
                "platform_total": platform_comment_count,
                "is_partial": is_partial,
            },
        },
        "contrast_summary": contrast,
        "metrics": {
            "view_count": view_count,
            "view_count_display": format_count(view_count),
            "like_count": like_count,
            "like_count_display": format_count(like_count),
            "comment_count_fetched": fetched_n,
            "comment_count_display": comment_disp,
            "comment_count_unit": unit,
            "published_ago_days": ago_days,
            "published_ago_display": ago_disp,
            "duration_seconds": int(duration_seconds or 0),
            "duration_display": duration_disp,
            "platform_comment_count": int(platform_comment_count or 0),
        },
        "content_analysis": content,
        "comment_analysis": comments_out,
    }
    return result, overall, content_failed, comments_failed


def validate_and_repair_result(result: dict[str, Any]) -> tuple[dict[str, Any], bool, str | None, str | None]:
    """Schema validate with repair then degrade. Returns (result, valid, schema_err, overall_override)."""
    import logging

    logger = logging.getLogger(__name__)
    valid = True
    schema_err = None
    overall_override = None
    try:
        _validate_result_doc(result)
    except Exception as e:
        schema_err = _format_schema_error(e)
        logger.warning("result schema invalid: %s", schema_err)
        result = _repair_result(result, schema_err)
        try:
            _validate_result_doc(result)
            valid = True
        except Exception as e2:
            schema_err = _format_schema_error(e2)
            logger.warning("result schema still invalid after repair: %s", schema_err)
            result = _degrade_to_failed_result(result, schema_err)
            try:
                _validate_result_doc(result)
                valid = True
                overall_override = "failed"
            except Exception as e3:
                valid = False
                schema_err = _format_schema_error(e3)
                logger.warning("result schema unrecoverable: %s", schema_err)
    return result, valid, schema_err, overall_override
