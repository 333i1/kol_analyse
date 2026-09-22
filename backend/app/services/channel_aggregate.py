\
"""Deterministic channel brief aggregation (D11). Zero LLM."""
from __future__ import annotations

from collections import Counter
from typing import Any

CHANNEL_BANNED = ("危机", "适合合作", "建议合作")


def _scrub(text: str | None) -> str | None:
    if text is None:
        return None
    if any(t in text for t in CHANNEL_BANNED):
        return "[已屏蔽违规裁决用语]"
    return text


def _avg(nums: list[float]) -> float | None:
    if not nums:
        return None
    return sum(nums) / len(nums)


def _pipeline(result: dict | None, key: str) -> str | None:
    if not isinstance(result, dict):
        return None
    block = result.get(key)
    if not isinstance(block, dict):
        return None
    st = block.get("pipeline_status")
    return st if isinstance(st, str) else None


def _metrics(result: dict | None) -> dict:
    if not isinstance(result, dict):
        return {}
    m = result.get("metrics") or {}
    return m if isinstance(m, dict) else {}


def _content(result: dict | None) -> dict:
    if not isinstance(result, dict):
        return {}
    c = result.get("content_analysis") or {}
    return c if isinstance(c, dict) else {}


def _comments(result: dict | None) -> dict:
    if not isinstance(result, dict):
        return {}
    c = result.get("comment_analysis") or {}
    return c if isinstance(c, dict) else {}


def _contrast(result: dict | None) -> dict:
    if not isinstance(result, dict):
        return {}
    c = result.get("contrast_summary") or {}
    return c if isinstance(c, dict) else {}


def aggregate_channel_brief(
    *,
    channel: dict[str, Any],
    children: list[dict[str, Any]],
    sample_meta: dict[str, Any] | None = None,
    ops: dict[str, Any] | None = None,
    result_version: str = "channel-brief-v1",
) -> dict[str, Any]:
    """Build a ChannelBriefResult-shaped dict.

    Each child:
      video_id, cohort ('recent'|'hot'), title?, view_count?, like_count?,
      comment_count?, duration_seconds?, pipeline_status?, child_task_id?,
      notes?, result? (v1 AnalysisResult dict or None)
    """
    sample_meta = sample_meta or {}
    ops = ops or {"verdict": None, "note": None, "updated_at": None}

    content_ok = content_total = comments_ok = comments_total = 0
    auto_caption = 0
    failed_ids: list[str] = []
    health_notes: list[str] = []

    view_nums: list[float] = []
    like_nums: list[float] = []
    comment_nums: list[float] = []
    dur_nums: list[float] = []
    er_nums: list[float] = []

    type_counts: Counter[str] = Counter()
    tone_counts: Counter[str] = Counter()
    theses: list[dict] = []
    cta_purchase = 0
    cta_total = 0
    ad_overlay_count = 0
    commercial_nonnull = 0
    commercial_total = 0
    commercial_excerpts: list[dict] = []

    # weighted sentiment
    w_pos = w_neu = w_neg = 0.0
    w_videos = 0
    theme_video: dict[str, set[str]] = {}
    reaction_acc: Counter[str] = Counter()
    reaction_weight = 0.0
    quotes: list[dict] = []

    mismatch = 0
    mismatch_total = 0
    contrast_sentences: list[str] = []
    human_review: list[str] = []
    high_neg: list[str] = []

    recent_children = [c for c in children if c.get("cohort") == "recent"]
    hot_children = [c for c in children if c.get("cohort") == "hot"]

    rows: list[dict] = []

    for ch in children:
        vid = ch.get("video_id") or ""
        cohort = ch.get("cohort") or "recent"
        result = ch.get("result")
        status = ch.get("pipeline_status") or ("completed" if result else "queued")
        notes = list(ch.get("notes") or [])

        content_total += 1
        comments_total += 1
        cps = _pipeline(result, "content_analysis")
        cms = _pipeline(result, "comment_analysis")
        if cps in ("ok", "degraded"):
            content_ok += 1
        elif cps == "failed" or status == "failed":
            failed_ids.append(vid)
        if cms == "ok":
            comments_ok += 1
        elif cms == "failed":
            if vid not in failed_ids:
                failed_ids.append(vid)
            health_notes.append(f"{vid} 评论管线失败，不进口碑加权")

        # auto caption heuristic from health notes in v1
        health = (result or {}).get("health") if isinstance(result, dict) else None
        if isinstance(health, dict):
            for n in health.get("notes") or []:
                if isinstance(n, str) and ("自动字幕" in n or "ASR" in n):
                    auto_caption += 1
                    notes.append("自动字幕")
                    break

        m = _metrics(result)
        # prefer child-level overrides
        views = ch.get("view_count")
        if views is None:
            views = m.get("view_count")
        likes = ch.get("like_count")
        if likes is None:
            likes = m.get("like_count")
        comments_n = ch.get("comment_count")
        if comments_n is None:
            comments_n = m.get("comment_count")
        dur = ch.get("duration_seconds")
        if dur is None:
            dur = m.get("duration_seconds")

        usable_metrics = status in ("completed", "degraded", "cache_hit") and cps in ("ok", "degraded")
        if usable_metrics:
            if views is not None:
                view_nums.append(float(views))
            if likes is not None:
                like_nums.append(float(likes))
            if comments_n is not None:
                comment_nums.append(float(comments_n))
            if dur is not None:
                dur_nums.append(float(dur))
            if views and float(views) > 0 and likes is not None and comments_n is not None:
                er_nums.append((float(likes) + float(comments_n)) / float(views))

        cc = _content(result)
        conclusion = cc.get("content_conclusion") or {}
        if isinstance(conclusion, dict):
            for t in conclusion.get("video_types") or []:
                if isinstance(t, str):
                    type_counts[t] += 1
            tone = conclusion.get("tone")
            if isinstance(tone, str):
                tone_counts[tone] += 1
            thesis = _scrub(conclusion.get("thesis"))
            if thesis:
                theses.append({"video_id": vid, "cohort": cohort, "text": thesis})

        segs = cc.get("segments") or []
        if isinstance(segs, list):
            for seg in segs:
                if not isinstance(seg, dict):
                    continue
                if seg.get("ad_overlay"):
                    ad_overlay_count += 1
                if seg.get("segment_kind") == "收束":
                    cta_total += 1
                    if seg.get("cta_type") == "购买":
                        cta_purchase += 1
                if seg.get("commercial"):
                    commercial_nonnull += 1
                    commercial_excerpts.append(
                        {
                            "video_id": vid,
                            "text": _scrub(str(seg.get("commercial"))) or "",
                            "kind": "commercial",
                        }
                    )
                commercial_total += 1 if seg.get("segment_kind") else 0

        # commercial at conclusion level sometimes
        if conclusion.get("commercial"):
            commercial_nonnull += 1

        cm = _comments(result)
        sent = cm.get("sentiment") or {}
        weight = float(comments_n or 0)
        if cms == "ok" and isinstance(sent, dict) and weight >= 0:
            # if weight 0, still count video once with equal weight 1
            w = weight if weight > 0 else 1.0
            w_pos += float(sent.get("positive") or 0) * w
            w_neu += float(sent.get("neutral") or 0) * w
            w_neg += float(sent.get("negative") or 0) * w
            w_videos += 1
            for th in cm.get("open_themes") or []:
                label = th if isinstance(th, str) else (th.get("label") if isinstance(th, dict) else None)
                if label:
                    theme_video.setdefault(label, set()).add(vid)
            for rt in cm.get("reaction_types") or []:
                if isinstance(rt, dict) and rt.get("label"):
                    share = float(rt.get("share") or rt.get("count") or 0)
                    reaction_acc[rt["label"]] += share
                    reaction_weight += share
            for q in (cm.get("positive_examples") or [])[:1]:
                if isinstance(q, dict):
                    quotes.append(
                        {
                            "polarity": "positive",
                            "text": _scrub(q.get("text") or "") or "",
                            "likes": q.get("like_count"),
                            "video_id": vid,
                            "video_title": ch.get("title"),
                        }
                    )
            for q in (cm.get("negative_examples") or [])[:1]:
                if isinstance(q, dict):
                    quotes.append(
                        {
                            "polarity": "negative",
                            "text": _scrub(q.get("text") or "") or "",
                            "likes": q.get("like_count"),
                            "video_id": vid,
                            "video_title": ch.get("title"),
                        }
                    )
                    high_neg.append(
                        f"「{_scrub(q.get('text') or '')}」· 赞 {q.get('like_count') or 0}"
                    )

        ctr = _contrast(result)
        mismatch_total += 1
        cs = ctr.get("contrast_sentence")
        if isinstance(cs, str) and cs.strip() and "无法对照" not in cs and "均不可用" not in cs:
            # treat non-empty factual contrast as potential mismatch signal when both sides present
            if cps in ("ok", "degraded") and cms == "ok":
                mismatch += 1
                contrast_sentences.append(_scrub(cs) or cs)
        for item in (cm.get("human_review_items") or []):
            if isinstance(item, dict):
                human_review.append(_scrub(item.get("text") or item.get("reason") or "") or "")
            elif isinstance(item, str):
                human_review.append(_scrub(item) or item)

        pos_share = None
        if cms == "ok" and isinstance(sent, dict) and sent.get("positive") is not None:
            pos_share = float(sent.get("positive"))

        vtypes = (conclusion.get("video_types") or []) if isinstance(conclusion, dict) else []
        rows.append(
            {
                "video_id": vid,
                "cohort": cohort,
                "title": ch.get("title"),
                "published_at": ch.get("published_at"),
                "duration_seconds": int(dur) if dur is not None else None,
                "view_count": int(views) if views is not None else None,
                "video_type": vtypes[0] if vtypes else None,
                "tone": conclusion.get("tone") if isinstance(conclusion, dict) else None,
                "sentiment_positive": pos_share,
                "pipeline_status": status if status != "completed" or cms != "failed" else "degraded",
                "child_task_id": ch.get("child_task_id"),
                "notes": notes,
            }
        )

    def cohort_summary(group: list[dict]) -> dict:
        if not group:
            return {
                "video_count": 0,
                "type_summary": None,
                "tone_summary": None,
                "avg_view_count": None,
                "sentiment_summary": None,
                "incomplete": True,
            }
        tc: Counter[str] = Counter()
        tones: Counter[str] = Counter()
        views: list[float] = []
        incomplete = False
        pos = neu = neg = 0.0
        w = 0.0
        for g in group:
            r = g.get("result")
            if _pipeline(r, "comment_analysis") != "ok":
                incomplete = True
            cc = _content(r)
            conc = cc.get("content_conclusion") or {}
            for t in (conc.get("video_types") or []) if isinstance(conc, dict) else []:
                tc[t] += 1
            tone = conc.get("tone") if isinstance(conc, dict) else None
            if tone:
                tones[tone] += 1
            vv = g.get("view_count")
            if vv is None:
                vv = _metrics(r).get("view_count")
            if vv is not None:
                views.append(float(vv))
            cm = _comments(r)
            sent = cm.get("sentiment") or {}
            cn = g.get("comment_count")
            if cn is None:
                cn = _metrics(r).get("comment_count")
            if _pipeline(r, "comment_analysis") == "ok" and isinstance(sent, dict):
                ww = float(cn) if cn else 1.0
                pos += float(sent.get("positive") or 0) * ww
                neu += float(sent.get("neutral") or 0) * ww
                neg += float(sent.get("negative") or 0) * ww
                w += ww
        type_summary = " · ".join(f"{k} {v}" for k, v in tc.most_common()) or None
        tone_summary = " · ".join(f"{k} {v}" for k, v in tones.most_common()) or None
        sent_summary = None
        if w > 0:
            sent_summary = (
                f"正 {round(pos/w)}% · 中 {round(neu/w)}% · 负 {round(neg/w)}%"
            )
        return {
            "video_count": len(group),
            "type_summary": type_summary,
            "tone_summary": tone_summary,
            "avg_view_count": _avg(views),
            "sentiment_summary": sent_summary,
            "incomplete": incomplete,
        }

    themes = [
        {"label": lab, "video_count": len(vids)}
        for lab, vids in theme_video.items()
        if len(vids) >= 2
    ]
    themes.sort(key=lambda x: -x["video_count"])

    reactions = []
    if reaction_weight > 0:
        for lab, val in reaction_acc.most_common():
            reactions.append({"label": lab, "share": round(val / reaction_weight, 4)})

    total_w = w_pos + w_neu + w_neg
    if total_w > 0:
        sentiment = {
            "positive": round(100 * w_pos / total_w, 1),
            "neutral": round(100 * w_neu / total_w, 1),
            "negative": round(100 * w_neg / total_w, 1),
        }
    else:
        sentiment = {"positive": 0, "neutral": 0, "negative": 0}

    commercial = None
    if commercial_excerpts or commercial_nonnull or ad_overlay_count or cta_purchase:
        commercial = {
            "commercial_nonnull_rate": (
                round(commercial_nonnull / max(commercial_total, 1), 4)
                if commercial_total
                else None
            ),
            "ad_overlay_count": ad_overlay_count,
            "cta_purchase_rate": (
                round(cta_purchase / cta_total, 4) if cta_total else None
            ),
            "excerpts": commercial_excerpts[:5],
        }

    brief: dict[str, Any] = {
        "schema_version": "2.0.0",
        "result_version": result_version,
        "channel": {
            "channel_id": channel.get("channel_id") or "",
            "title": channel.get("title") or "",
            "handle": channel.get("handle"),
            "subscriber_count": channel.get("subscriber_count"),
            "view_count": channel.get("view_count"),
            "video_count": channel.get("video_count"),
            "canonical_url": channel.get("canonical_url"),
        },
        "sample": {
            "window_size": int(sample_meta.get("window_size") or 50),
            "recent_count": len(recent_children),
            "hot_count": len(hot_children),
            "video_ids": [c.get("video_id") for c in children if c.get("video_id")],
            "gap_notes": list(sample_meta.get("gap_notes") or []),
        },
        "health": {
            "content_ok": content_ok,
            "content_total": content_total,
            "comments_ok": comments_ok,
            "comments_total": comments_total,
            "auto_caption_count": auto_caption,
            "failed_video_ids": failed_ids,
            "notes": health_notes,
        },
        "metrics": {
            "sample_count_used": len(view_nums),
            "avg_view_count": _avg(view_nums),
            "avg_like_count": _avg(like_nums),
            "avg_comment_count": _avg(comment_nums),
            "avg_duration_seconds": _avg(dur_nums),
            "avg_engagement_rate": _avg(er_nums),
            "channel_subscriber_count": channel.get("subscriber_count"),
            "channel_view_count": channel.get("view_count"),
            "channel_video_count": channel.get("video_count"),
        },
        "recent_vs_hot": {
            "recent": cohort_summary(recent_children),
            "hot": cohort_summary(hot_children),
        },
        "content_habits": {
            "type_counts": dict(type_counts),
            "tone_counts": dict(tone_counts),
            "cta_purchase_rate": (round(cta_purchase / cta_total, 4) if cta_total else None),
            "ad_overlay_count": ad_overlay_count,
            "theses": theses,
        },
        "audience_habits": {
            "sentiment": sentiment,
            "weighted_video_count": w_videos,
            "themes": themes,
            "reaction_types": reactions,
            "quotes": quotes[:4],
        },
        "alignment": {
            "mismatch_count": mismatch,
            "mismatch_total": mismatch_total,
            "contrast_sentences": contrast_sentences[:5],
            "human_review_items": [h for h in human_review if h][:10],
            "high_like_negatives": high_neg[:5],
        },
        "videos": rows,
        "ops": {
            "verdict": ops.get("verdict"),
            "note": ops.get("note"),
            "updated_at": ops.get("updated_at"),
        },
    }
    if commercial is not None:
        brief["commercial"] = commercial
    return brief
