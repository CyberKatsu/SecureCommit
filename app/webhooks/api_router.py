"""
api_router.py — Internal REST API consumed by the Reflex dashboard.

This is separate from the webhook router.  The dashboard calls these endpoints
to display review history.  In a production system you would add authentication
here; for a portfolio project, network isolation (Docker internal network) is
sufficient.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_db
from app.database.repositories import get_review_session, list_review_sessions
from app.models.schemas import ReviewSessionRead

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/sessions", response_model=list[ReviewSessionRead])
async def get_sessions(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> list[ReviewSessionRead]:
    """Return the most recent N review sessions with their findings."""
    sessions = await list_review_sessions(db, limit=limit)
    return [ReviewSessionRead.model_validate(s) for s in sessions]


@router.get("/sessions/{session_id}", response_model=Optional[ReviewSessionRead])
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Optional[ReviewSessionRead]:
    session = await get_review_session(db, session_id)
    if session:
        return ReviewSessionRead.model_validate(session)
    return None
