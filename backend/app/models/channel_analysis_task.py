from __future__ import annotations

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ChannelAnalysisTask(Base):
    __tablename__ = "channel_analysis_tasks"
    __table_args__ = (
        Index("ix_channel_tasks_status", "status"),
        Index("ix_channel_tasks_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    channel_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_url: Mapped[str] = mapped_column(Text, nullable=False)
    handle: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    sample_video_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    child_task_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ops_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    ops_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
