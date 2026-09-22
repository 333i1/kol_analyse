
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RawSnapshot(Base):
    __tablename__ = "raw_snapshots"
    __table_args__ = (
        Index("ix_raw_video_id", "video_id"),
        Index("ix_raw_fetched", "fetched_at"),
        Index("ix_raw_checksum", "checksum"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    video_id: Mapped[str] = mapped_column(
        String(11), ForeignKey("videos.video_id"), nullable=False
    )
    fetched_at: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_mode: Mapped[str] = mapped_column(Text, nullable=False)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_cues_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    comments_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    platform_comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_notes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    checksum: Mapped[str] = mapped_column(Text, nullable=False)


@event.listens_for(RawSnapshot, "before_update")
def _reject_raw_snapshot_update(mapper, connection, target) -> None:
    raise RuntimeError("raw_snapshots is insert-only; business path must INSERT a new row")
