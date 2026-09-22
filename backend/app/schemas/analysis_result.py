
"""Pydantic mirrors of Draft-07 AnalysisResult. Used for assembly; jsonschema is source of truth."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

HealthLevel = Literal["ok", "degraded", "failed"]
SegmentKind = Literal["开场", "展开", "收束"]
HumanReviewStatus = Literal["待确认"]


class AnalysisResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0.0"] = "1.0.0"
    result_version: str
    is_fixture: bool = False
    fixture_note: Optional[str] = None
    video: dict[str, Any]
    task: dict[str, Any]
    health: dict[str, Any]
    contrast_summary: Optional[dict[str, Any]] = None
    metrics: dict[str, Any]
    content_analysis: dict[str, Any]
    comment_analysis: dict[str, Any]
