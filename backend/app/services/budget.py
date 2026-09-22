
"""Per-task budget gate. Gate BEFORE the billed request."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.analysis_task import AnalysisTask
from app.models.budget_alert import BudgetAlert
from app.models.cost_ledger import CostLedgerEntry


class BudgetExceededError(Exception):
    def __init__(self, stage: str, spent: float, estimate: float, threshold: float):
        self.stage = stage
        self.spent = spent
        self.estimate = estimate
        self.threshold = threshold
        super().__init__(
            f"将超过 ANALYSIS_BUDGET_USD，已停止 {stage}"
        )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def spent_usd(db: Session, task_id: str) -> float:
    total = db.scalar(
        select(func.coalesce(func.sum(CostLedgerEntry.usd_amount), 0.0)).where(
            CostLedgerEntry.task_id == task_id
        )
    )
    return float(total or 0.0)


def check_or_stop(
    db: Session,
    *,
    task_id: str,
    video_id: str,
    stage: str,
    estimate_usd: float,
    extra_spent: float = 0.0,
) -> None:
    settings = get_settings()
    threshold = float(settings.ANALYSIS_BUDGET_USD)
    spent = spent_usd(db, task_id) + float(extra_spent or 0.0)
    if spent + float(estimate_usd) > threshold:
        alert = BudgetAlert(
            id=str(uuid.uuid4()),
            task_id=task_id,
            video_id=video_id,
            threshold_usd=threshold,
            spent_usd=spent,
            blocked_stage=stage,
            message=f"将超过 ANALYSIS_BUDGET_USD，已停止 {stage}",
            created_at=_now(),
        )
        db.add(alert)
        task = db.get(AnalysisTask, task_id)
        if task is not None:
            task.budget_exceeded = 1
        db.commit()
        raise BudgetExceededError(stage, spent, float(estimate_usd), threshold)


def record(
    db: Session,
    *,
    task_id: str,
    video_id: str,
    stage: str,
    vendor: str,
    usd_amount: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    units: str | None = None,
    note: str | None = None,
) -> CostLedgerEntry:
    entry = CostLedgerEntry(
        id=str(uuid.uuid4()),
        task_id=task_id,
        video_id=video_id,
        stage=stage,
        vendor=vendor,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        units=units,
        usd_amount=float(usd_amount),
        created_at=_now(),
        note=note,
    )
    db.add(entry)
    db.commit()
    return entry


def estimate_llm(input_tokens: int, output_tokens: int) -> float:
    s = get_settings()
    return (input_tokens / 1000.0) * s.LLM_INPUT_USD_PER_1K + (
        output_tokens / 1000.0
    ) * s.LLM_OUTPUT_USD_PER_1K
