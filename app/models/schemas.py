"""
schemas.py — All Pydantic models for the application.

Design decisions:
* AI response models use strict=False so minor Claude formatting variations
  (e.g. "critical" vs "Critical") don't break parsing — we normalise in the
  validator instead.
* Internal DTOs (DiffChunk, ReviewSession) are separate from DB models so the
  service layer never imports SQLAlchemy directly.
* GitHub webhook payload models only capture the fields we actually use,
  keeping the surface area small and the parsing fast.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Severity ─────────────────────────────────────────────────────────────────

class Severity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"

    @classmethod
    def normalise(cls, value: str) -> "Severity":
        """Case-insensitive lookup so Claude's output doesn't have to be perfect."""
        for member in cls:
            if member.value.lower() == value.strip().lower():
                return member
        return cls.LOW


class VulnerabilityCategory(str, Enum):
    SQL_INJECTION = "SQL_INJECTION"
    XSS = "XSS"
    SSRF = "SSRF"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    COMMAND_INJECTION = "COMMAND_INJECTION"
    INSECURE_DESERIALIZATION = "INSECURE_DESERIALIZATION"
    HARDCODED_SECRET = "HARDCODED_SECRET"
    WEAK_CRYPTO = "WEAK_CRYPTO"
    MISSING_AUTH = "MISSING_AUTH"
    IDOR = "IDOR"
    OPEN_REDIRECT = "OPEN_REDIRECT"
    SENSITIVE_DATA_EXPOSURE = "SENSITIVE_DATA_EXPOSURE"
    INSECURE_DEFAULT = "INSECURE_DEFAULT"
    OTHER = "OTHER"


# ── AI Response Models ────────────────────────────────────────────────────────

class AIFinding(BaseModel):
    """Mirrors exactly what Claude is instructed to return for each finding."""

    file_path: str = Field(..., description="Relative path in the repo")
    diff_line_number: int = Field(
        ..., ge=1, description="1-indexed line within the diff hunk"
    )
    severity: Severity
    category: VulnerabilityCategory = VulnerabilityCategory.OTHER
    explanation: str = Field(..., min_length=10)
    suggested_fix: str = Field(..., min_length=5)

    @field_validator("severity", mode="before")
    @classmethod
    def normalise_severity(cls, v: object) -> Severity:
        # When called programmatically with an already-resolved enum member,
        # return it directly.  When called from JSON (Claude's response), v is
        # a plain string like "critical" or "Critical".
        if isinstance(v, Severity):
            return v
        return Severity.normalise(str(v))

    @field_validator("category", mode="before")
    @classmethod
    def normalise_category(cls, v: object) -> VulnerabilityCategory:
        if isinstance(v, VulnerabilityCategory):
            return v
        try:
            return VulnerabilityCategory(str(v).upper())
        except ValueError:
            return VulnerabilityCategory.OTHER


class AIReviewResponse(BaseModel):
    """Wrapper around the list Claude returns — lets us validate the whole response."""

    findings: list[AIFinding] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_bare_list(cls, data: object) -> object:
        """Claude returns a bare JSON array; wrap it for validation."""
        if isinstance(data, list):
            return {"findings": data}
        return data


# ── Diff Processing DTOs ──────────────────────────────────────────────────────

class DiffLine(BaseModel):
    """A single line within a parsed diff hunk."""

    number: int       # 1-indexed position within this file's diff
    content: str      # Raw line text (including leading +/-/ )
    is_addition: bool # True when the line starts with '+'
    original_line: Optional[int] = None  # Original file line number (from hunk header)
    new_line: Optional[int] = None       # New file line number


class DiffChunk(BaseModel):
    """A single file's worth of diff, ready to send to Claude."""

    file_path: str
    patch: str          # Raw unified diff patch text for this file
    lines: list[DiffLine]
    # These are needed to post the inline comment at the right position.
    # GitHub's pull request review comment API wants the *diff position*,
    # not the file line number.
    diff_position_map: dict[int, int] = Field(
        default_factory=dict,
        description="Maps diff_line_number → GitHub diff position integer",
    )


# ── GitHub Webhook Payload ────────────────────────────────────────────────────

class GitHubRepo(BaseModel):
    id: int
    full_name: str
    html_url: str


class GitHubPullRequest(BaseModel):
    number: int
    title: str
    html_url: str
    head: dict  # contains 'sha' for the commit ref
    base: dict


class GitHubWebhookPayload(BaseModel):
    """Subset of the GitHub pull_request webhook payload we actually use."""

    action: str
    number: int
    pull_request: GitHubPullRequest
    repository: GitHubRepo
    installation: Optional[dict] = None  # Present for GitHub App installs

    @property
    def installation_id(self) -> Optional[int]:
        if self.installation:
            return self.installation.get("id")
        return None


# ── Database / Session DTOs ───────────────────────────────────────────────────

class ReviewSessionCreate(BaseModel):
    repo_full_name: str
    pr_number: int
    pr_title: str
    pr_url: str
    head_sha: str


class FindingCreate(BaseModel):
    session_id: uuid.UUID
    file_path: str
    diff_line_number: int
    github_position: int
    severity: Severity
    category: VulnerabilityCategory
    explanation: str
    suggested_fix: str
    comment_id: Optional[int] = None   # GitHub comment ID once posted


class ReviewSessionRead(ReviewSessionCreate):
    id: uuid.UUID
    status: str
    created_at: datetime
    findings: list["FindingRead"] = []

    model_config = {"from_attributes": True}


class FindingRead(BaseModel):
    id: uuid.UUID
    file_path: str
    diff_line_number: int
    severity: Severity
    category: VulnerabilityCategory
    explanation: str
    suggested_fix: str
    comment_id: Optional[int]

    model_config = {"from_attributes": True}


ReviewSessionRead.model_rebuild()
