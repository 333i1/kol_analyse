
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BudgetAlert(Base):
    __tablename__ = "budget_alerts"
    __table_args__ = (
        Index("ix_alerts_task", "task_id"),
        Index("ix_alerts_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(
        Text, ForeignKey("analysis_tasks.id"), nullable=False
    )
    video_id: Mapped[str] = mapped_column(String(11), nullable=False)
    threshold_usd: Mapped[float] = mapped_column(Float, nullable=False)
    spent_usd: Mapped[float] = mapped_column(Float, nullable=False)
    blocked_stage: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
