
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

INVALID_URL_MESSAGE = "无法解析为合法 YouTube 视频，不创建任务"
INVALID_JSON_MESSAGE = "请求体无效，需要 JSON 且包含 url 字段"


class AnalyzeRequest(BaseModel):
    url: str
    force: bool = False
    skip_captions: bool = False
    captions: bool | None = None  # aliases: False => skip_captions


class TranscriptChoiceRequest(BaseModel):
    skip: bool


class ErrorBody(BaseModel):
    error: str
    message: str
    hint: Optional[str] = None


class TaskError(BaseModel):
    code: str
    message: str


class TaskEnvelope(BaseModel):
    task_id: str
    video_id: str
    status: str
    cache_hit: bool = False
    current_step: int = 0
    current_step_label: str = "校验 URL"
    budget_exceeded: bool = False
    error: Optional[TaskError] = None
    result: Optional[dict[str, Any]] = None
    awaiting: Optional[str] = None


INVALID_CHANNEL_URL_MESSAGE = "无法解析为合法 YouTube 频道，不创建任务"


class ChannelAnalyzeRequest(BaseModel):
    url: str


class ChannelOpsRequest(BaseModel):
    verdict: Optional[str] = None
    note: Optional[str] = None


class ChannelTaskEnvelope(BaseModel):
    task_id: str
    channel_id: Optional[str] = None
    status: str
    current_step: int = 0
    error: Optional[TaskError] = None
    result: Optional[dict[str, Any]] = None
