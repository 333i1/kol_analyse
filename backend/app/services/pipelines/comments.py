
"""Comments pipeline: noise → one billed llm_comments → deterministic aggregation."""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.services.banned import rewrite_if_banned
from app.services.budget import BudgetExceededError
from app.services.llm_client import LLMClient, LLMError
from app.services.noise import preprocess_comments
from app.config import get_settings

logger = logging.getLogger(__name__)
_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "comments_v1.txt"
CONF_THRESHOLD = 0.6
LLM_COMMENT_CAP = 30
REACTION_TYPES = {
    "共鸣", "反驳", "提问", "纠错", "玩梗", "点名时间戳", "求更新", "分享经历", "其他",
}


def _clamp_confidence(value, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if x != x or x in (float("inf"), float("-inf")):
        return default
    return max(0.0, min(1.0, x))


def _as_fact_sentence(value, fallback: str) -> str:
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


def _coerce_reaction(value) -> str:
    v = str(value or "").strip()
    return v if v in REACTION_TYPES else "其他"




def _dump_llm_comment_items(*, task_id: str, video_id: str, raw: dict[str, Any]) -> None:
    """One-off SC-003 dump: per-comment items are not in public result JSON."""
    dump_dir = Path(__file__).resolve().parents[3] / "data" / "debug_comment_items"
    dump_dir.mkdir(parents=True, exist_ok=True)
    try:
        pv = get_settings().PROMPT_VERSION_COMMENTS
    except Exception:
        pv = "unknown"
    payload = {
        "task_id": task_id,
        "video_id": video_id,
        "prompt_version": pv,
        "items": list(raw.get("items") or []),
        "comment_conclusion": raw.get("comment_conclusion"),
    }
    (dump_dir / f"{video_id}_{task_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (dump_dir / f"{video_id}_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")



def _select_for_llm(analyzable: list, cap: int) -> list:
    """Top `cap` by like_count; restore original order (stable)."""
    if len(analyzable) <= cap:
        return list(analyzable)
    ranked = sorted(
        enumerate(analyzable),
        key=lambda pair: (-int(getattr(pair[1], "like_count", 0) or 0), pair[0]),
    )
    chosen = ranked[:cap]
    chosen.sort(key=lambda pair: pair[0])
    return [p for _, p in chosen]


def _failed_comments(noise_obj: dict, reason: str) -> dict[str, Any]:
    """Schema-valid failed comment_analysis. Do not add skip_reason (additionalProperties false)."""
    label = str(noise_obj.get("label") or "噪音已过滤 0 条（约 0%）")
    if reason and reason not in label:
        label = f"{label}；{reason}"
    return {
        "pipeline_status": "failed",
        "low_confidence_items": [],
        "human_review_items": [],
        "noise": {
            "filtered_count": int(noise_obj.get("filtered_count") or 0),
            "filtered_ratio": float(noise_obj.get("filtered_ratio") or 0),
            "label": label,
        },
        "reply_heat": {"reply_count": None, "included_in_sentiment": False},
    }


def round_sentiment(pos: int, neu: int, neg: int) -> dict[str, int]:
    total = pos + neu + neg
    if total <= 0:
        return {"positive_pct": 0, "neutral_pct": 0, "negative_pct": 0}
    raw = {
        "positive_pct": pos * 100.0 / total,
        "neutral_pct": neu * 100.0 / total,
        "negative_pct": neg * 100.0 / total,
    }
    floors = {k: int(v) for k, v in raw.items()}
    remain = 100 - sum(floors.values())
    fracs = sorted(((raw[k] - floors[k], k) for k in floors), reverse=True)
    for i in range(remain):
        floors[fracs[i % 3][1]] += 1
    # ensure sum == 100 within ±0 by construction
    return floors


def aggregate_items(
    items: list[dict],
    comments_by_id: dict[str, dict],
    *,
    sample_fetched: int,
    platform_total: int | None,
    llm_sent: int | None = None,
) -> dict[str, Any]:
    low = []
    human = []
    usable = []
    for it in items:
        cid = str(it.get("comment_id") or "")
        src = comments_by_id.get(cid) or {}
        text = src.get("text") or src.get("cleaned_text") or ""
        conf = _clamp_confidence(it.get("confidence"), 0.0)
        # exclusiveMaximum 0.6: conf==0.6 must NOT enter low_confidence_items
        if conf < CONF_THRESHOLD:
            low.append({"text": (text or cid or "（空）") or "（空）", "confidence": conf})
            continue
        if it.get("ambiguous") is True:
            human.append({"text": text or cid or "（空）", "status": "待确认"})
            # ambiguous still can count? Spec: ambiguous → human_review; typically still excluded from main? 
            # Plan: ambiguous=true → human_review_items; "其余" go to sentiment. So ambiguous excluded from 其余.
            continue
        usable.append((it, text, src))

    pos = neu = neg = 0
    sent_examples = {"正面": [], "中性": [], "负面": []}
    reaction_counter: Counter = Counter()
    reaction_examples: dict[str, list[str]] = defaultdict(list)
    theme_examples: dict[str, list[str]] = defaultdict(list)
    theme_weight: Counter = Counter()
    pos_quotes = []
    neg_quotes = []

    for it, text, src in usable:
        sent = it.get("sentiment") or "中性"
        if sent == "正面":
            pos += 1
            pos_quotes.append((int(src.get("like_count") or 0), text))
        elif sent == "负面":
            neg += 1
            neg_quotes.append((int(src.get("like_count") or 0), text))
        else:
            neu += 1
            sent = "中性"
        if len(sent_examples[sent]) < 3 and text:
            sent_examples[sent].append(text)
        rtype = _coerce_reaction(it.get("reaction_type"))
        reaction_counter[rtype] += 1
        label = it.get("display_label") or rtype
        if len(reaction_examples[rtype]) < 3 and text:
            reaction_examples[rtype].append(text)
        for th in it.get("themes") or []:
            if not th:
                continue
            theme_weight[th] += 1
            if len(theme_examples[th]) < 3 and text:
                theme_examples[th].append(text)

    sentiment = round_sentiment(pos, neu, neg)
    reaction_types = []
    for rtype, count in reaction_counter.most_common():
        ex = reaction_examples[rtype] or ["（无示例）"]
        reaction_types.append(
            {
                "type": _coerce_reaction(rtype),
                "display_label": str(rtype or "其他"),
                "count": count,
                "examples": ex,
            }
        )
    open_themes = []
    for name, w in theme_weight.most_common():
        open_themes.append(
            {
                "name": name,
                "examples": theme_examples[name] or ["（无示例）"],
                "weight": w,
            }
        )

    def _quote(pairs):
        if not pairs:
            return None
        pairs = sorted(pairs, key=lambda x: x[0], reverse=True)
        likes, text = pairs[0]
        if not text:
            return None
        return {
            "text": text,
            "like_count": likes,
            "like_count_display": str(likes),
        }

    reply_total = sum(int(c.get("reply_count") or 0) for c in comments_by_id.values())
    ratio = None
    if platform_total and platform_total > 0:
        ratio = min(1.0, sample_fetched / float(platform_total))
    sample_label = f"样本 {sample_fetched}"
    if platform_total is not None:
        pct = int(round((ratio or 0) * 100))
        sample_label = f"样本 {sample_fetched}/{platform_total} · 约 {pct}%"
        if sample_fetched < platform_total:
            sample_label += " · 非全量"
    if llm_sent is not None and llm_sent < sample_fetched:
        sample_label += f" · LLM子集 {llm_sent}"

    return {
        "sentiment": sentiment,
        "reaction_types": reaction_types,
        "open_themes": open_themes,
        "top_positive_quote": _quote(pos_quotes),
        "top_negative_quote": _quote(neg_quotes),
        "sentiment_examples": sent_examples,
        "low_confidence_items": low,
        "human_review_items": human,
        "reply_heat": {
            "reply_count": reply_total if reply_total else None,
            "included_in_sentiment": False,
        },
        "sample_meta": {
            "sample_label": sample_label,
            "sample_fetched": sample_fetched,
            "sample_platform_total": platform_total,
            "sample_ratio": ratio,
        },
    }


def run_comments(
    *,
    db: Session,
    task_id: str,
    video_id: str,
    comments: list[dict],
    platform_total: int | None,
    llm: LLMClient,
    mock_response: dict | None = None,
    conclusion_override: dict | None = None,
) -> dict[str, Any]:
    # only top-level
    top = [c for c in comments if c.get("is_top_level", True)]
    processed = preprocess_comments(top, video_id=video_id)
    noise_count = sum(1 for p in processed if p.is_noise)
    analyzable = [p for p in processed if p.should_analyze]
    total = len(processed) or 1
    noise_obj = {
        "filtered_count": noise_count,
        "filtered_ratio": round(noise_count / total, 4),
        "label": f"噪音已过滤 {noise_count} 条（约 {int(round(100*noise_count/total))}%）",
    }

    if not analyzable:
        return _failed_comments(noise_obj, "无可分析评论")

    sent = _select_for_llm(analyzable, LLM_COMMENT_CAP)
    payload = [
        {
            "id": p.comment_id,
            "text": p.cleaned_text,
            "like_count": p.like_count,
            "reply_count": p.reply_count,
        }
        for p in sent
    ]
    system = _load_prompt()
    user = "评论列表 JSON：\n" + json.dumps(payload, ensure_ascii=False)

    try:
        if mock_response is not None:
            raw = mock_response
        else:
            raw = llm.complete(
                db=db,
                task_id=task_id,
                video_id=video_id,
                stage="llm_comments",
                system_prompt=system,
                user_prompt=user,
                expected_input_tokens=max(400, len(user) // 3),
                expected_output_tokens=800,
            )
    except BudgetExceededError:
        return _failed_comments(noise_obj, "超预算")
    except LLMError as e:
        logger.warning("comments LLM failed: %s", e)
        return _failed_comments(noise_obj, f"LLM失败: {e}")

    try:
        _dump_llm_comment_items(task_id=task_id, video_id=video_id, raw=raw)
    except Exception:
        logger.warning("debug dump llm_comments items failed", exc_info=True)

    by_id = {p.comment_id: {"text": p.cleaned_text, "like_count": p.like_count, "reply_count": p.reply_count} for p in analyzable}
    agg = aggregate_items(
        list(raw.get("items") or []),
        by_id,
        sample_fetched=len(top),
        platform_total=platform_total,
        llm_sent=len(sent),
    )
    conclusion = conclusion_override or raw.get("comment_conclusion") or {
        "doing": "评论信号已聚合。",
        "themes": "主题见 open_themes。",
        "alignment": "对照见总述。",
        "confidence": 0.7,
    }
    meta = agg.pop("sample_meta")
    conclusion = {
        "doing": _as_fact_sentence(conclusion.get("doing"), "评论信号已聚合。"),
        "themes": _as_fact_sentence(conclusion.get("themes"), "主题见 open_themes。"),
        "alignment": _as_fact_sentence(conclusion.get("alignment"), "对照见总述。"),
        "confidence": _clamp_confidence(conclusion.get("confidence"), 0.7),
        "sample_label": meta["sample_label"],
        "sample_fetched": meta["sample_fetched"],
        "sample_platform_total": meta["sample_platform_total"],
        "sample_ratio": meta["sample_ratio"],
    }

    # drop None quotes for schema (optional fields)
    out = {
        "pipeline_status": "ok",
        "sentiment": agg["sentiment"],
        "reaction_types": agg["reaction_types"] or [
            {"type": "其他", "display_label": "其他", "count": 0, "examples": ["（无）"]}
        ],
        "open_themes": agg["open_themes"] or [
            {"name": "其他", "examples": ["（无）"], "weight": 0}
        ],
        "sentiment_examples": agg["sentiment_examples"],
        "low_confidence_items": agg["low_confidence_items"],
        "human_review_items": agg["human_review_items"],
        "noise": noise_obj,
        "reply_heat": agg["reply_heat"],
        "comment_conclusion": conclusion,
    }
    if agg.get("top_positive_quote"):
        out["top_positive_quote"] = agg["top_positive_quote"]
    if agg.get("top_negative_quote"):
        out["top_negative_quote"] = agg["top_negative_quote"]
    # reaction_types need min 1 examples — already ensured
    if not out["reaction_types"]:
        out["reaction_types"] = [
            {"type": "其他", "display_label": "其他", "count": 0, "examples": ["（无）"]}
        ]
    return out
