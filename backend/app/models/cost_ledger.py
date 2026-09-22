
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CostLedgerEntry(Base):
    __tablename__ = "cost_ledger"
    __table_args__ = (
        Index("ix_ledger_task", "task_id"),
        Index("ix_ledger_video", "video_id"),
        Index("ix_ledger_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(
        Text, ForeignKey("analysis_tasks.id"), nullable=False
    )
    video_id: Mapped[str] = mapped_column(String(11), nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    vendor: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[str | None] = mapped_column(Text, nullable=True)
    usd_amount: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
