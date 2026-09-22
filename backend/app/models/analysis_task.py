
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"
    __table_args__ = (
        Index("ix_tasks_video_status", "video_id", "status"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    video_id: Mapped[str] = mapped_column(
        String(11), ForeignKey("videos.video_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_step_label: Mapped[str] = mapped_column(Text, nullable=False, default="校验 URL")
    cache_hit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    budget_exceeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_snapshot_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    billed: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
