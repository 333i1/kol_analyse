from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.analysis_result import AnalysisResultRow
from app.models.analysis_task import AnalysisTask
from app.models.video import Video
from app.models.channel_analysis_task import ChannelAnalysisTask
from app.schemas.http import (
    INVALID_CHANNEL_URL_MESSAGE,
    INVALID_JSON_MESSAGE,
    INVALID_URL_MESSAGE,
    AnalyzeRequest,
    ChannelOpsRequest,
    TaskEnvelope,
)
from app.services.cache import get_fresh, get_in_flight
from app.services.url_parser import parse_youtube_video_id
from app.services.channel_url_parser import parse_youtube_channel_ref
from app.services.channel_worker import (
    create_channel_task_row,
    enqueue_channel,
    refresh_channel_task,
)
from app.services.worker import (
    STEP_LABELS,
    enqueue,
    is_awaiting_transcript_choice,
    mark_force,
    mark_skip_captions,
    submit_transcript_choice,
)
from app.worker.auto_skip_stt import mark_auto_skip_stt
from app.worker.transcript_choice import AWAITING_TRANSCRIPT_CHOICE

router = APIRouter(prefix="/api/v1")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")



def _fmt_count(n: int | None) -> str:
    if n is None:
        return "—"
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "—"
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿".replace(".0亿", "亿")
    if n >= 10_000:
        return f"{n / 10_000:.1f}万".replace(".0万", "万")
    return f"{n:,}"


def _fmt_duration(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "—"
    m, sec = divmod(max(0, s), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _task_preview(db: Session, task: AnalysisTask) -> dict | None:
    """Basics available while analyzing (video row + raw metadata). No LLM fields."""
    if task.status not in ("analyzing", "queued"):
        return None
    # Jump UX targets 口碑聚合 (step 4); still attach preview from step 1+ if present.
    if (task.current_step or 0) < 1:
        return None
    video = db.get(Video, task.video_id)
    if video is None:
        return None
    meta: dict = {}
    if task.raw_snapshot_id:
        from app.models.raw_snapshot import RawSnapshot

        raw = db.get(RawSnapshot, task.raw_snapshot_id)
        if raw and raw.metadata_json:
            try:
                loaded = json.loads(raw.metadata_json)
                if isinstance(loaded, dict):
                    meta = loaded
            except json.JSONDecodeError:
                meta = {}
    stats = meta.get("statistics") if isinstance(meta.get("statistics"), dict) else {}
    views = stats.get("viewCount")
    likes = stats.get("likeCount")
    comments = stats.get("commentCount")
    try:
        views_i = int(views) if views is not None else None
    except (TypeError, ValueError):
        views_i = None
    try:
        likes_i = int(likes) if likes is not None else None
    except (TypeError, ValueError):
        likes_i = None
    try:
        comments_i = int(comments) if comments is not None else None
    except (TypeError, ValueError):
        comments_i = None
    step_label = task.current_step_label or "分析中"
    return {
        "video": {
            "video_id": task.video_id,
            "title": video.title or task.video_id,
            "channel_name": video.channel_name or "未知频道",
            "canonical_url": video.canonical_url,
            "duration_seconds": video.duration_seconds,
            "published_at": video.published_at,
        },
        "metrics": {
            "view_count_display": _fmt_count(views_i),
            "like_count_display": _fmt_count(likes_i),
            "comment_count_display": _fmt_count(comments_i),
            "comment_count_unit": "条",
            "published_ago_display": "—",
            "published_ago_note": "",
            "duration_display": _fmt_duration(video.duration_seconds),
            "duration_note": "",
            "engagement_ratio_display": "—",
            "engagement_ratio_note": "分析完成后补全",
        },
        "health": {
            "overall_label": "分析进行中",
            "overall_level": "degraded",
            "source": {"label": "元数据已就绪", "level": "ok"},
            "llm": {"label": step_label, "level": "degraded"},
            "notes": [f"当前步骤：{step_label}", "内容拆解与口碑聚合完成后会自动填入各栏目"],
        },
        "contrast_summary": {
            "content_says": "内容分析进行中…",
            "comments_reply": "口碑聚合进行中…",
            "contrast_sentence": "基础数据已展示，分析结果写入后会刷新本页。",
            "freshness_tags": ["进行中"],
            "fetched_at_label": "预览",
        },
        "content_analysis": {
            "pipeline_status": "analyzing",
            "segments": [],
            "content_conclusion": {
                "video_types": [],
                "thesis": "内容拆解进行中…",
                "tone": "—",
                "tone_description": "—",
                "commercial": None,
                "confidence": None,
                "coverage_label": "等待中",
            },
        },
        "comment_analysis": {
            "pipeline_status": "analyzing",
            "sentiment": {"positive_pct": 0, "neutral_pct": 0, "negative_pct": 0},
            "reaction_types": [],
            "open_themes": [],
            "comment_conclusion": {
                "doing": "口碑聚合进行中…",
                "themes": "—",
                "alignment": "—",
                "confidence": None,
                "sample_label": "等待中",
            },
        },
        "is_preview": True,
    }


def _envelope(task: AnalysisTask, *, status: str | None = None, result=None, cache_hit=False, preview=None) -> dict:
    err = None
    if task.error_code:
        err = {"code": task.error_code, "message": task.error_message or task.error_code}
    out = {
        "task_id": task.id,
        "video_id": task.video_id,
        "status": status or task.status,
        "cache_hit": cache_hit,
        "current_step": task.current_step,
        "current_step_label": task.current_step_label,
        "budget_exceeded": bool(task.budget_exceeded),
        "error": err,
        "result": result,
    }
    if (status or task.status) == "analyzing" and is_awaiting_transcript_choice(task.id):
        out["awaiting"] = AWAITING_TRANSCRIPT_CHOICE
    if preview is not None:
        out["preview"] = preview
        out["result_ready"] = False
    return out



def _history_item(db: Session, task: AnalysisTask) -> dict:
    video = db.get(Video, task.video_id)
    canonical_url = (
        video.canonical_url
        if video and video.canonical_url
        else f"https://www.youtube.com/watch?v={task.video_id}"
    )
    title = (video.title if video else None) or ""
    summary = ""
    types: list[str] = []
    if task.result_id:
        row = db.get(AnalysisResultRow, task.result_id)
        if row and row.result_json:
            try:
                result = json.loads(row.result_json)
            except json.JSONDecodeError:
                result = {}
            vid = result.get("video") or {}
            title = vid.get("title") or title
            cc = (result.get("content_analysis") or {}).get("content_conclusion") or {}
            types = list(cc.get("video_types") or [])
            summary = (cc.get("thesis") or "").strip()
            if not summary:
                summary = ((result.get("contrast_summary") or {}).get("contrast_sentence") or "").strip()
    if task.status == "failed":
        if not title:
            title = "无法获取的视频"
        if not summary:
            summary = task.error_message or "分析未完成"
    if not title:
        title = task.video_id
    return {
        "task_id": task.id,
        "video_id": task.video_id,
        "canonical_url": canonical_url,
        "title": title,
        "summary": summary,
        "video_types": types,
        "status": task.status,
        "created_at": task.created_at,
        "error": (
            {"code": task.error_code, "message": task.error_message or task.error_code}
            if task.error_code
            else None
        ),
    }


@router.post("/analyses")
async def create_analysis(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": INVALID_JSON_MESSAGE},
        )
    if not isinstance(body, dict) or "url" not in body:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": INVALID_JSON_MESSAGE},
        )
    url = body.get("url")
    if not isinstance(url, str):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": INVALID_JSON_MESSAGE},
        )

    force = body.get("force") is True
    # Per-task: skip_captions=true OR captions=false
    skip_captions = body.get("skip_captions") is True or body.get("captions") is False
    # Batch/multi: honor upfront caption/STT policy — never show second transcript_choice UI.
    auto_skip_stt = body.get("auto_skip_stt") is True or body.get("batch") is True

    video_id = parse_youtube_video_id(url)
    if not video_id:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_url",
                "message": INVALID_URL_MESSAGE,
                "hint": "需要 youtube.com/watch?v= 或 youtu.be/ 及有效 11 位视频 ID。",
            },
        )

    fresh = None if force else get_fresh(db, video_id)
    if fresh is not None:
        row, task = fresh
        result = json.loads(row.result_json)
        result = dict(result)
        result["task"] = {
            "status": "cache_hit",
            "cache_hit": True,
            "video_id": video_id,
        }
        return JSONResponse(
            status_code=200,
            content=_envelope(task, status="cache_hit", result=result, cache_hit=True),
        )

    inflight = get_in_flight(db, video_id)
    if inflight is not None:
        return JSONResponse(status_code=202, content=_envelope(inflight))

    now = _now()
    video = db.get(Video, video_id)
    if video is None:
        video = Video(
            video_id=video_id,
            platform="youtube",
            canonical_url=f"https://www.youtube.com/watch?v={video_id}",
            created_at=now,
            updated_at=now,
        )
        db.add(video)
        db.flush()

    task = AnalysisTask(
        id=str(uuid.uuid4()),
        video_id=video_id,
        status="queued",
        current_step=0,
        current_step_label=STEP_LABELS[0],
        cache_hit=0,
        budget_exceeded=0,
        billed=1,
        created_at=now,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    if force:
        mark_force(task.id)
    if skip_captions:
        mark_skip_captions(task.id)
    if auto_skip_stt:
        mark_auto_skip_stt(task.id)
    await enqueue(task.id)
    return JSONResponse(status_code=202, content=_envelope(task))



@router.get("/analyses")
def list_analyses(limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db)):
    tasks = (
        db.query(AnalysisTask)
        .filter(AnalysisTask.status.in_(("completed", "failed")))
        .order_by(AnalysisTask.created_at.desc())
        .limit(limit)
        .all()
    )
    items = [_history_item(db, t) for t in tasks]
    return {"items": items, "count": len(items)}


@router.get("/analyses/{task_id}")
def get_analysis(task_id: str, db: Session = Depends(get_db)):
    task = db.get(AnalysisTask, task_id)
    if task is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": "任务不存在"},
        )
    result = None
    if task.result_id:
        row = db.get(AnalysisResultRow, task.result_id)
        if row:
            result = json.loads(row.result_json)
    preview = None
    if result is None and task.status in ("analyzing", "queued"):
        preview = _task_preview(db, task)
    return _envelope(task, result=result, preview=preview)


@router.post("/analyses/{task_id}/transcript-choice")
async def post_transcript_choice(task_id: str, request: Request, db: Session = Depends(get_db)):
    task = db.get(AnalysisTask, task_id)
    if task is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": "任务不存在"},
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": "请求体无效，需要 JSON 且包含 skip 布尔字段"},
        )
    if not isinstance(body, dict) or "skip" not in body or not isinstance(body.get("skip"), bool):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": "请求体无效，需要 JSON 且包含 skip 布尔字段"},
        )
    code = submit_transcript_choice(task_id, body["skip"])
    if code != "ok":
        return JSONResponse(
            status_code=409,
            content={"error": "conflict", "message": "任务未在等待转录选择"},
        )
    await enqueue(task_id)
    return JSONResponse(status_code=202, content=_envelope(task))



_DONE_CHILD = frozenset({"completed", "degraded", "failed", "cache_hit"})


def _channel_step_info(task: ChannelAnalysisTask, result: dict | None) -> tuple[int, str]:
    """Derive progress from status + result_json (no DB migration)."""
    status = task.status
    if status == "queued":
        return 0, "解析频道"
    if status in ("completed", "degraded"):
        return 3, "完成"
    if status == "failed":
        return 0, (task.error_message or "失败")
    # analyzing (or unexpected)
    if not result:
        return 1, "抽样中"
    videos = result.get("videos") if isinstance(result, dict) else None
    if isinstance(videos, list) and videos:
        done = sum(1 for v in videos if isinstance(v, dict) and v.get("pipeline_status") in _DONE_CHILD)
        total = max(len(videos), 5)
        return 2, f"子视频分析 {done}/{total}"
    return 2, "子视频分析"


def _channel_envelope(task: ChannelAnalysisTask) -> dict:
    err = None
    if task.error_code:
        err = {"code": task.error_code, "message": task.error_message or task.error_code}
    result = None
    if task.result_json:
        try:
            result = json.loads(task.result_json)
        except json.JSONDecodeError:
            result = None
    step, label = _channel_step_info(task, result)
    return {
        "task_id": task.id,
        "channel_id": task.channel_id,
        "status": task.status,
        "current_step": step,
        "current_step_label": label,
        "error": err,
        "result": result,
    }


@router.post("/channel-analyses")
async def create_channel_analysis(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": INVALID_JSON_MESSAGE},
        )
    if not isinstance(body, dict) or "url" not in body or not isinstance(body.get("url"), str):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": INVALID_JSON_MESSAGE},
        )
    url = body["url"]
    if parse_youtube_channel_ref(url) is None:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_channel_url",
                "message": INVALID_CHANNEL_URL_MESSAGE,
                "hint": "需要 youtube.com/@handle、/channel/UC… 或裸 UC… / @handle。",
            },
        )
    row = create_channel_task_row(db, url)
    await enqueue_channel(row.id)
    return JSONResponse(status_code=202, content=_channel_envelope(row))


@router.get("/channel-analyses/{task_id}")
def get_channel_analysis(task_id: str, db: Session = Depends(get_db)):
    task = db.get(ChannelAnalysisTask, task_id)
    if task is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": "任务不存在"},
        )
    if task.status == "analyzing":
        try:
            task = refresh_channel_task(db, task_id)
        except Exception:
            pass
    return _channel_envelope(task)


@router.put("/channel-analyses/{task_id}/ops")
async def put_channel_ops(task_id: str, request: Request, db: Session = Depends(get_db)):
    task = db.get(ChannelAnalysisTask, task_id)
    if task is None:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": "任务不存在"},
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": "请求体无效"},
        )
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": "请求体无效"},
        )
    verdict = body.get("verdict")
    if verdict is not None and verdict not in ("倾向合作", "再观察", "不合作"):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_ops", "message": "verdict 必须是 倾向合作/再观察/不合作 或 null"},
        )
    task.ops_verdict = verdict
    if "note" in body:
        note = body.get("note")
        task.ops_note = note if isinstance(note, str) or note is None else str(note)
    task.updated_at = _now()
    # refresh ops inside result_json if present
    if task.result_json:
        try:
            data = json.loads(task.result_json)
            data["ops"] = {
                "verdict": task.ops_verdict,
                "note": task.ops_note,
                "updated_at": task.updated_at,
            }
            task.result_json = json.dumps(data, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    db.commit()
    db.refresh(task)
    return _channel_envelope(task)
