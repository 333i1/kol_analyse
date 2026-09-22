"""Deterministic recent-3 + hot-2 sampling (D11). Pure function; no network."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SampleVideo:
    video_id: str
    view_count: int = 0
    published_at: str | None = None


@dataclass
class SampleResult:
    recent: list[SampleVideo]
    hot: list[SampleVideo]
    window_size: int
    gap_notes: list[str] = field(default_factory=list)

    @property
    def video_ids(self) -> list[str]:
        seen: list[str] = []
        for v in self.recent + self.hot:
            if v.video_id not in seen:
                seen.append(v.video_id)
        return seen


def sample_recent_and_hot(
    ordered_newest_first: list[SampleVideo],
    *,
    recent_n: int = 3,
    hot_n: int = 2,
    window_size: int = 50,
) -> SampleResult:
    """`ordered_newest_first` is uploads playlist order (newest first)."""
    gaps: list[str] = []
    if not ordered_newest_first:
        gaps.append("uploads 为空，无法抽样")
        return SampleResult(recent=[], hot=[], window_size=window_size, gap_notes=gaps)

    window = ordered_newest_first[: max(1, window_size)]
    recent = window[:recent_n]
    if len(recent) < recent_n:
        gaps.append(f"最近仅 {len(recent)} 条，少于 {recent_n}")

    recent_ids = {v.video_id for v in recent}
    ranked = sorted(window, key=lambda v: (-int(v.view_count or 0), v.video_id))
    hot: list[SampleVideo] = []
    for v in ranked:
        if v.video_id in recent_ids:
            continue
        hot.append(v)
        if len(hot) >= hot_n:
            break
    if len(hot) < hot_n:
        gaps.append(f"最热仅 {len(hot)} 条（去重后），少于 {hot_n}")

    return SampleResult(recent=recent, hot=hot, window_size=window_size, gap_notes=gaps)
