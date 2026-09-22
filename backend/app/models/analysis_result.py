
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AnalysisResultRow(Base):
    __tablename__ = "analysis_results"
    __table_args__ = (
        Index("ix_results_video_created", "video_id", "created_at"),
        Index("ix_results_replay", "replay_hash"),
        Index("ix_results_created", "created_at"),
        UniqueConstraint("task_id", name="uk_results_task_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    video_id: Mapped[str] = mapped_column(
        String(11), ForeignKey("videos.video_id"), nullable=False
    )
    raw_snapshot_id: Mapped[str] = mapped_column(
        Text, ForeignKey("raw_snapshots.id"), nullable=False
    )
    task_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, default="1.0.0")
    result_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    llm_model: Mapped[str] = mapped_column(Text, nullable=False)
    replay_hash: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_fixture: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
