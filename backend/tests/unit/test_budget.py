
from app.db import init_db
from app import db as db_mod
from app.models.analysis_task import AnalysisTask
from app.models.video import Video
from app.services.budget import BudgetExceededError, check_or_stop, record, spent_usd
from app.models.budget_alert import BudgetAlert


def _task(db):
    db.add(
        Video(
            video_id="dQw4w9WgXcQ",
            platform="youtube",
            canonical_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            created_at="2026-08-31T00:00:00Z",
            updated_at="2026-08-31T00:00:00Z",
        )
    )
    db.flush()
    t = AnalysisTask(
        id="task-budget",
        video_id="dQw4w9WgXcQ",
        status="analyzing",
        current_step=3,
        current_step_label="内容拆解",
        created_at="2026-08-31T00:00:00Z",
    )
    db.add(t)
    db.commit()
    return t


def test_exceed_stops_and_alerts(engine):
    db = db_mod.SessionLocal()
    try:
        t = _task(db)
        record(
            db,
            task_id=t.id,
            video_id=t.video_id,
            stage="llm_content",
            vendor="openai-compatible",
            usd_amount=0.09,
        )
        try:
            check_or_stop(
                db, task_id=t.id, video_id=t.video_id, stage="llm_comments", estimate_usd=0.05
            )
            raised = False
        except BudgetExceededError:
            raised = True
        assert raised
        alerts = db.query(BudgetAlert).all()
        assert len(alerts) >= 1
        db.refresh(t)
        assert t.budget_exceeded == 1
        # no subsequent higher stage ledger
        from app.models.cost_ledger import CostLedgerEntry

        later = (
            db.query(CostLedgerEntry)
            .filter(CostLedgerEntry.stage == "llm_comments")
            .all()
        )
        assert later == []
        assert spent_usd(db, t.id) == 0.09
    finally:
        db.close()
