"""Dual content/comments pipeline scheduling. Isolated Sessions; merge on caller thread."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from sqlalchemy.orm import Session

from app import db as db_mod
from app.models.analysis_task import AnalysisTask
from app.services.cache import get_reusable_comments, get_reusable_content
from app.services.pipelines.comments import run_comments
from app.services.pipelines.content import run_content
from app.worker.deps import WorkerDeps


def _run_pipeline_isolated(fn, **kwargs):
    """Each pipeline opens/closes its own Session. Never share the worker Session across threads."""
    if db_mod.SessionLocal is None:
        raise RuntimeError("DB not initialized")
    session = db_mod.SessionLocal()
    try:
        return fn(db=session, **kwargs)
    finally:
        session.close()


def schedule_pipelines(
    db: Session,
    task: AnalysisTask,
    *,
    task_id: str,
    title: str,
    transcript_text: str,
    cues: list,
    duration_seconds: int | None,
    comments: list,
    platform_comment_count: int | None,
    deps: WorkerDeps,
    set_step: Callable[[Session, AnalysisTask, int], None],
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], AnalysisTask | None]:
    """Run or reuse content+comments. Parallel billed calls use isolated Sessions.

    After the parallel branch the worker Session is expire_all()'d; returned task
    may be None if the row disappeared. Caller must merge on the main thread.
    """
    reused_content = None if force else get_reusable_content(db, task.video_id)
    reused_comments = None if force else get_reusable_comments(db, task.video_id)
    run_content_now = reused_content is None
    run_comments_now = reused_comments is None
    if run_content_now and run_comments_now:
        set_step(db, task, 3)
        set_step(db, task, 4)
        # Do not pass the worker Session into two threads. Each pipeline owns a Session.
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_content = pool.submit(
                _run_pipeline_isolated,
                run_content,
                task_id=task.id,
                video_id=task.video_id,
                title=title,
                transcript_text=transcript_text,
                cues=cues,
                duration_seconds=duration_seconds,
                llm=deps.llm,
                mock_response=deps.content_llm_mock,
            )
            fut_comments = pool.submit(
                _run_pipeline_isolated,
                run_comments,
                task_id=task.id,
                video_id=task.video_id,
                comments=comments,
                platform_total=platform_comment_count,
                llm=deps.llm,
                mock_response=deps.comments_llm_mock,
            )
            content = fut_content.result()
            comments_out = fut_comments.result()
        # Pipeline sessions may have committed budget_exceeded / cost_ledger.
        db.expire_all()
        task = db.get(AnalysisTask, task_id)
        return content, comments_out, task
    elif run_content_now:
        set_step(db, task, 3)
        content = run_content(
            db=db,
            task_id=task.id,
            video_id=task.video_id,
            title=title,
            transcript_text=transcript_text,
            cues=cues,
            duration_seconds=duration_seconds,
            llm=deps.llm,
            mock_response=deps.content_llm_mock,
        )
        comments_out = reused_comments
        set_step(db, task, 4)
        return content, comments_out, task
    elif run_comments_now:
        set_step(db, task, 4)
        comments_out = run_comments(
            db=db,
            task_id=task.id,
            video_id=task.video_id,
            comments=comments,
            platform_total=platform_comment_count,
            llm=deps.llm,
            mock_response=deps.comments_llm_mock,
        )
        content = reused_content
        return content, comments_out, task
    else:
        return reused_content, reused_comments, task
