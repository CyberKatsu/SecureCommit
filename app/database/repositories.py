"""
repositories.py — Data access layer.

Design decisions:
* Repository pattern: service modules call repository functions, never raw
  SQLAlchemy.  This makes unit-testing services easy (mock the repository)
  and keeps SQL concerns out of business logic.
* All functions are async and accept an AsyncSession argument — the session
  is always owned by the caller (FastAPI dependency or Celery task), so we
  never manage transactions here.  Callers call session.commit() / rollback().
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.database import Finding, ReviewSession
from app.models.schemas import FindingCreate, ReviewSessionCreate


# ── ReviewSession ─────────────────────────────────────────────────────────────

async def create_review_session(
    db: AsyncSession, data: ReviewSessionCreate
) -> ReviewSession:
    session = ReviewSession(**data.model_dump())
    db.add(session)
    await db.flush()  # Gets the generated UUID without committing.
    return session


async def get_review_session(
    db: AsyncSession, session_id: uuid.UUID
) -> Optional[ReviewSession]:
    result = await db.execute(
        select(ReviewSession)
        .where(ReviewSession.id == session_id)
        .options(selectinload(ReviewSession.findings))
    )
    return result.scalar_one_or_none()


async def list_review_sessions(
    db: AsyncSession, limit: int = 50
) -> list[ReviewSession]:
    result = await db.execute(
        select(ReviewSession)
        .options(selectinload(ReviewSession.findings))
        .order_by(ReviewSession.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_session_status(
    db: AsyncSession,
    session_id: uuid.UUID,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    session = await db.get(ReviewSession, session_id)
    if session:
        session.status = status
        if error_message:
            session.error_message = error_message
        await db.flush()


# ── Finding ───────────────────────────────────────────────────────────────────

async def create_finding(db: AsyncSession, data: FindingCreate) -> Finding:
    finding = Finding(**data.model_dump())
    db.add(finding)
    await db.flush()
    return finding


async def update_finding_comment_id(
    db: AsyncSession, finding_id: uuid.UUID, comment_id: int
) -> None:
    finding = await db.get(Finding, finding_id)
    if finding:
        finding.comment_id = comment_id
        await db.flush()
