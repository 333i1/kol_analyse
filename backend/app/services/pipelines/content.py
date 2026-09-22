
"""Content pipeline: one billed llm_content call. Project exactly 3 segments."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.banned import rewrite_if_banned
from app.services.budget import BudgetExceededError
from app.services.llm_client import LLMClient, LLMError

logger = logging.getLogger(__name__)
_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "content_v1.txt"
KINDS = ("开场", "展开", "收束")
VIDEO_TYPES = ("教程", "评测", "日常", "观点", "资讯", "娱乐", "游戏", "音乐", "影视解说", "体育", "喜剧", "其他")
TONES = ("解说", "吐槽", "陪伴", "严肃", "表演", "论证", "其他")
CTA_TYPES = ("关注", "下一集", "投票", "购买", "收藏", "评论", "其他")


def _clamp_confidence(value, default: float = 0.7) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if x != x or x in (float("inf"), float("-inf")):
        return default
    return max(0.0, min(1.0, x))


def _coerce_video_types(raw) -> list[str]:
    seq = raw if isinstance(raw, list) else ([raw] if raw else [])
    out: list[str] = []
    seen: set[str] = set()
    for item in seq:
        v = item if item in VIDEO_TYPES else "其他"
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out or ["其他"]



def _coerce_topics(raw) -> list[str]:
    """Freeform subject tags: strip, drop empty, max 3, each max 20 chars; missing -> []."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s:
            continue
        if len(s) > 20:
            s = s[:20]
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= 3:
            break
    return out


def _coerce_tone(raw) -> str:
    return raw if raw in TONES else "其他"


def _coerce_cta(raw, *, kind: str):
    if kind != "收束":
        return None
    if raw is None or raw == "":
        return None
    return raw if raw in CTA_TYPES else "其他"


def _nonempty(value, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback



def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def _slice_captions(cues: list[dict], start_s: float, end_s: float) -> list[dict]:
    out = []
    for c in cues:
        st = float(c.get("start") or 0)
        if start_s - 0.5 <= st <= end_s + 0.5:
            out.append({"start": _fmt_ts(st), "text": (c.get("text") or "").strip() or "…"})
        if len(out) >= 5:
            break
    if not out and cues:
        # fallback: take first cue near window
        c = cues[0]
        out = [{"start": _fmt_ts(float(c.get("start") or 0)), "text": (c.get("text") or "").strip() or "…"}]
    if not out:
        out = [{"start": "00:00", "text": "（无字幕片段）"}]
    return out


def _parse_mmss(text: str | None, default: float = 0.0) -> float:
    if not text:
        return default
    try:
        parts = str(text).split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except Exception:
        return default
    return default


def coverage_label(ratio: float | None, duration: int | None) -> str:
    if ratio is None:
        return "覆盖率未知"
    pct = int(round(ratio * 100))
    return f"覆盖片长约 {pct}%"


def run_content(
    *,
    db: Session,
    task_id: str,
    video_id: str,
    title: str,
    transcript_text: str | None,
    cues: list[dict],
    duration_seconds: int | None,
    llm: LLMClient,
    mock_response: dict | None = None,
) -> dict[str, Any]:
    if not (transcript_text or "").strip():
        return {
            "pipeline_status": "failed",
            "skip_reason": "无转录",
            "segments": [],
        }

    settings = get_settings()
    # truncate for budget
    text = transcript_text
    max_chars = 12000
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    system = _load_prompt()
    user = f"标题：{title}\n\n转录：\n{text}"

    try:
        if mock_response is not None:
            raw = mock_response
        else:
            raw = llm.complete(
                db=db,
                task_id=task_id,
                video_id=video_id,
                stage="llm_content",
                system_prompt=system,
                user_prompt=user,
            )
    except BudgetExceededError:
        return {
            "pipeline_status": "failed",
            "skip_reason": "超预算",
            "segments": [],
        }
    except LLMError as e:
        logger.warning("content LLM failed: %s", e)
        return {
            "pipeline_status": "failed",
            "skip_reason": f"LLM失败: {e}",
            "segments": [],
        }

    # project segments
    segs_in = {s.get("kind"): s for s in (raw.get("segments") or []) if isinstance(s, dict)}
    segments = []
    for kind in KINDS:
        s = segs_in.get(kind) or {}
        start_s = _parse_mmss(s.get("caption_start"), 0)
        end_s = _parse_mmss(s.get("caption_end"), start_s + 30)
        ad = s.get("ad_overlay")
        if ad == "":
            ad = None
        segments.append(
            {
                "kind": kind,
                "confidence": _clamp_confidence(s.get("confidence"), 0.7),
                "summary": _nonempty(s.get("summary"), f"{kind}摘要"),
                "cta_type": _coerce_cta(s.get("cta_type"), kind=kind),
                "ad_overlay": ad,
                "captions": _slice_captions(cues, start_s, end_s),
            }
        )

    cue_span = 0.0
    if cues:
        last = cues[-1]
        cue_span = float(last.get("start") or 0) + float(last.get("duration") or 0)
    ratio = None
    if duration_seconds and duration_seconds > 0:
        ratio = min(1.0, cue_span / float(duration_seconds))
        if truncated and ratio is not None:
            ratio = min(ratio, 0.85)

    conclusion_in = raw.get("content_conclusion") or {}
    commercial = conclusion_in.get("commercial", None)
    if commercial == "":
        commercial = None
    if isinstance(commercial, str):
        commercial = rewrite_if_banned(commercial, "提及商业信息，详见内容摘要。")
    conclusion = {
        "video_types": _coerce_video_types(conclusion_in.get("video_types")),
        "topics": _coerce_topics(conclusion_in.get("topics")),
        "thesis": rewrite_if_banned(
            _nonempty(conclusion_in.get("thesis"), "内容摘要不可用。"),
            "内容摘要不可用。",
        ),
        "tone": _coerce_tone(conclusion_in.get("tone")),
        "tone_description": _nonempty(conclusion_in.get("tone_description"), "语气未判定。"),
        "commercial": commercial,
        "confidence": _clamp_confidence(conclusion_in.get("confidence"), 0.7),
        "coverage_label": coverage_label(ratio, duration_seconds),
        "coverage_ratio": None if ratio is None else max(0.0, min(1.0, float(ratio))),
    }

    return {
        "pipeline_status": "ok",
        "skip_reason": None,
        "segments": segments,
        "content_conclusion": conclusion,
    }

SKIP_STT_REASON = "user_skipped_stt"

_BRIEF_SYSTEM_PROMPT = """你是视频内容分析器。当前没有字幕、也没有语音转录，只能根据 YouTube 标题与简介做简要结论。
禁止编造开场/展开/收束三段，禁止编造时间戳，禁止输出 segments 非空数组。
commercial 必须为 JSON null。
禁止输出：适合合作、危机、核心卖点、selling_points。

必须恰好输出 JSON：
{
  "segments": [],
  "content_conclusion": {
    "video_types": ["教程"|"评测"|"日常"|"观点"|"资讯"|"娱乐"|"游戏"|"音乐"|"影视解说"|"体育"|"喜剧"|"其他"],
    "topics": ["题材自由词1-3个，可空数组"],
    "thesis": "事实句，禁止危机/适合合作",
    "tone": "解说"|"吐槽"|"陪伴"|"严肃"|"表演"|"论证"|"其他",
    "tone_description": "…",
    "commercial": null,
    "confidence": 0.0-1.0
  }
}
规则：segments 必须是空数组；不要编三段；只填结论槽。
"""


def _fallback_brief_conclusion(title: str, description: str = "") -> dict:
    title = (title or "").strip() or "未命名视频"
    desc = (description or "").strip()
    if desc:
        thesis = f"根据标题「{title}」与简介的简要描述，无语音转录。"
    else:
        thesis = f"根据标题「{title}」的简要描述，无语音转录。"
    return {
        "video_types": ["其他"],
        "topics": [],
        "thesis": rewrite_if_banned(thesis, "内容摘要不可用。"),
        "tone": "其他",
        "tone_description": "无转录，语气未判定。",
        "commercial": None,
        "confidence": 0.3,
        "coverage_label": "无语音转录，仅标题/简介",
        "coverage_ratio": None,
    }


def run_content_from_snippet(
    *,
    db: Session,
    task_id: str,
    video_id: str,
    title: str,
    description: str | None = None,
    llm: LLMClient,
    mock_response: dict | None = None,
) -> dict[str, Any]:
    """One billed llm_content call: title+description only. Never fabricate 3 segments."""
    desc = (description or "").strip()
    user = f"标题：{title or ''}\n\n简介：\n{desc or '（无简介）'}"
    raw: dict | None = None
    try:
        if mock_response is not None:
            raw = mock_response
        else:
            raw = llm.complete(
                db=db,
                task_id=task_id,
                video_id=video_id,
                stage="llm_content",
                system_prompt=_BRIEF_SYSTEM_PROMPT,
                user_prompt=user,
            )
    except BudgetExceededError:
        conclusion = _fallback_brief_conclusion(title, desc)
        return {
            "pipeline_status": "degraded",
            "skip_reason": SKIP_STT_REASON,
            "segments": [],
            "content_conclusion": conclusion,
        }
    except LLMError as e:
        logger.warning("brief content LLM failed: %s", e)
        conclusion = _fallback_brief_conclusion(title, desc)
        return {
            "pipeline_status": "degraded",
            "skip_reason": SKIP_STT_REASON,
            "segments": [],
            "content_conclusion": conclusion,
        }
    except Exception as e:
        logger.warning("brief content failed: %s", e)
        raw = None

    if not isinstance(raw, dict):
        conclusion = _fallback_brief_conclusion(title, desc)
        return {
            "pipeline_status": "degraded",
            "skip_reason": SKIP_STT_REASON,
            "segments": [],
            "content_conclusion": conclusion,
        }

    conclusion_in = raw.get("content_conclusion") or {}
    if not isinstance(conclusion_in, dict):
        conclusion_in = {}
    conclusion = {
        "video_types": _coerce_video_types(conclusion_in.get("video_types")),
        "topics": _coerce_topics(conclusion_in.get("topics")),
        "thesis": rewrite_if_banned(
            _nonempty(conclusion_in.get("thesis"), _fallback_brief_conclusion(title, desc)["thesis"]),
            "内容摘要不可用。",
        ),
        "tone": _coerce_tone(conclusion_in.get("tone")),
        "tone_description": _nonempty(conclusion_in.get("tone_description"), "无转录，语气未判定。"),
        "commercial": None,
        "confidence": _clamp_confidence(conclusion_in.get("confidence"), 0.4),
        "coverage_label": "无语音转录，仅标题/简介",
        "coverage_ratio": None,
    }
    return {
        "pipeline_status": "degraded",
        "skip_reason": SKIP_STT_REASON,
        "segments": [],
        "content_conclusion": conclusion,
    }
