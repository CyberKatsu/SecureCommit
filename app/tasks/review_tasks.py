"""
review_tasks.py — Celery tasks for asynchronous PR review processing.

Design decisions:
* A single `process_pull_request` task handles the entire pipeline: fetch
  diff → parse → analyse → post comments → persist.  Breaking it into subtasks
  would add complexity without benefit for a portfolio project; in production
  you might use Celery chord/chain to parallelise per-file analysis.
* We create a *synchronous* SQLAlchemy session inside the task using the sync
  database URL.  Running an asyncio event loop inside Celery workers requires
  careful setup (nest_asyncio or a dedicated event loop per task) that adds
  complexity.  The synchronous approach is simpler and correct.
* autoretry_for: transient network errors (Anthropic/GitHub API flakiness) are
  retried up to 3 times with exponential back-off.  Permanent errors (invalid
  credentials, non-existent PR) are not in the list and will fail immediately.
* bind=True: the task receives itself as first argument so it can call
  self.retry() and access self.request.id for logging.
"""

from __future__ import annotations

import logging
import uuid

from celery import Celery
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models.database import Finding, ReviewSession
from app.models.schemas import AIFinding, ReviewSessionCreate
from app.services import ai_service, diff_parser, github_service

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Celery app ────────────────────────────────────────────────────────────────

celery_app = Celery(
    "securecommit",
    broker=settings.redis_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,  # Process one task at a time — Claude calls are slow.
    task_acks_late=True,           # Only ack after task completes → at-least-once delivery.
)

# ── Synchronous DB session (Celery workers don't use asyncio) ─────────────────

_sync_engine = create_engine(
    settings.database_url_sync,
    pool_pre_ping=True,
    pool_size=5,
)
_SyncSession = sessionmaker(bind=_sync_engine)


def _get_sync_db() -> Session:
    return _SyncSession()


# ── Task ──────────────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="securecommit.review_tasks.process_pull_request",
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def process_pull_request(
    self,
    repo_full_name: str,
    pr_number: int,
    pr_title: str,
    pr_url: str,
    head_sha: str,
) -> dict:
    """
    Full pipeline: fetch diff → AI analysis → post GitHub review → persist.

    Returns a summary dict for Celery result storage / monitoring.
    """
    db = _get_sync_db()
    session_id: uuid.UUID | None = None

    try:
        # 1. Create a ReviewSession record so the dashboard shows "pending" immediately.
        session_id = _create_review_session(
            db,
            ReviewSessionCreate(
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                pr_title=pr_title,
                pr_url=pr_url,
                head_sha=head_sha,
            ),
        )
        _update_status(db, session_id, "processing")
        logger.info("[task %s] Processing %s#%d", self.request.id, repo_full_name, pr_number)

        # 2. Fetch diff from GitHub.
        pr_files = github_service.get_pr_files(repo_full_name, pr_number)
        chunks = diff_parser.extract_chunks_from_files(
            pr_files, settings.max_diff_lines_per_chunk
        )
        logger.info("[task %s] Parsed %d diff chunk(s)", self.request.id, len(chunks))

        if not chunks:
            _update_status(db, session_id, "completed")
            return {"status": "completed", "findings": 0, "reason": "no_additions"}

        # 3. Analyse with Claude.
        findings: list[AIFinding] = ai_service.analyse_chunks(chunks)
        logger.info("[task %s] Found %d issue(s)", self.request.id, len(findings))

        # 4. Generate summary review body.
        summary = ai_service.generate_summary(findings)

        # 5. Post review to GitHub.
        github_service.post_review(
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha=head_sha,
            findings=findings,
            chunks=chunks,
            summary_body=summary,
        )

        # 6. Persist all findings.
        _persist_findings(db, session_id, findings, chunks)
        _update_status(db, session_id, "completed")

        return {
            "status": "completed",
            "session_id": str(session_id),
            "findings": len(findings),
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("[task %s] Failed: %s", self.request.id, exc)
        if session_id:
            _update_status(db, session_id, "failed", str(exc))
        raise
    finally:
        db.close()


# ── Private helpers ───────────────────────────────────────────────────────────

def _create_review_session(db: Session, data: ReviewSessionCreate) -> uuid.UUID:
    session = ReviewSession(**data.model_dump())
    db.add(session)
    db.commit()
    db.refresh(session)
    return session.id  # type: ignore[return-value]


def _update_status(
    db: Session,
    session_id: uuid.UUID,
    status: str,
    error_message: str | None = None,
) -> None:
    obj = db.get(ReviewSession, session_id)
    if obj:
        obj.status = status
        if error_message:
            obj.error_message = error_message
        db.commit()


def _persist_findings(
    db: Session,
    session_id: uuid.UUID,
    findings: list[AIFinding],
    chunks: list[diff_parser.DiffChunk],
) -> None:
    # Build a consolidated position map across all chunks.
    position_map: dict[str, dict[int, int]] = {}
    for chunk in chunks:
        if chunk.file_path not in position_map:
            position_map[chunk.file_path] = {}
        position_map[chunk.file_path].update(chunk.diff_position_map)

    for finding in findings:
        fp_map = position_map.get(finding.file_path, {})
        github_pos = fp_map.get(finding.diff_line_number, 0)

        row = Finding(
            session_id=session_id,
            file_path=finding.file_path,
            diff_line_number=finding.diff_line_number,
            github_position=github_pos,
            severity=finding.severity.value,
            category=finding.category.value,
            explanation=finding.explanation,
            suggested_fix=finding.suggested_fix,
        )
        db.add(row)

    db.commit()
