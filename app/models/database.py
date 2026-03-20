"""
database.py — SQLAlchemy async ORM models.

Design decisions:
* UUIDs as primary keys: avoids sequential-ID enumeration attacks and makes
  records safe to expose in URLs.
* Separate ReviewSession and Finding tables: a session captures the PR-level
  context once; each finding is a row, making it trivial to query
  "all Critical findings across all PRs" with a single SELECT.
* String enum columns store the Pydantic enum *value* (e.g. "Critical") so
  the DB is human-readable without joining a lookup table.
* created_at / updated_at use server_default so the DB clock governs
  timestamps, avoiding clock-skew issues between worker containers.
"""

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class ReviewSession(Base):
    """One row per pull_request webhook event processed."""

    __tablename__ = "review_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_full_name = Column(String(255), nullable=False, index=True)
    pr_number = Column(Integer, nullable=False)
    pr_title = Column(Text, nullable=False)
    pr_url = Column(Text, nullable=False)
    head_sha = Column(String(40), nullable=False)

    # Lifecycle state machine: pending → processing → completed | failed
    status = Column(String(20), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    findings = relationship(
        "Finding", back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ReviewSession {self.repo_full_name}#{self.pr_number} [{self.status}]>"


class Finding(Base):
    """One row per security issue identified by Claude."""

    __tablename__ = "findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("review_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    file_path = Column(Text, nullable=False)
    diff_line_number = Column(Integer, nullable=False)
    github_position = Column(Integer, nullable=False)

    # Stored as their string value so the DB is readable.
    severity = Column(String(20), nullable=False)
    category = Column(String(60), nullable=False)
    explanation = Column(Text, nullable=False)
    suggested_fix = Column(Text, nullable=False)

    # GitHub comment ID populated after the comment is successfully posted.
    comment_id = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("ReviewSession", back_populates="findings")

    def __repr__(self) -> str:
        return f"<Finding [{self.severity}] {self.file_path}:{self.diff_line_number}>"
