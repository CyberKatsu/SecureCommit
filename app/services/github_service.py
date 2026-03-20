"""
github_service.py — All GitHub API interactions via PyGithub.

Design decisions:
* We wrap PyGithub behind our own thin service layer.  This means the rest of
  the application never imports `github` directly, making it easy to swap the
  underlying library or mock it in tests.
* Review comments are posted as a *pull request review* (create_review) rather
  than individual pull_request.create_review_comment calls.  This groups all
  findings into a single review event in the GitHub UI, which is less noisy
  and matches how human reviewers work.
* The summary is submitted as the review body with event=REQUEST_CHANGES when
  Critical/High findings exist, COMMENT for Medium/Low, APPROVE when there are
  no findings.
* diff_position must be the GitHub "position" integer, not a file line number.
  We carry this through from the DiffChunk.diff_position_map.
"""

from __future__ import annotations

import logging
from typing import Optional

import github
from github import Github, GithubException

from app.config import get_settings
from app.models.schemas import AIFinding, DiffChunk, Severity

logger = logging.getLogger(__name__)
settings = get_settings()


def _get_client() -> Github:
    """Return an authenticated PyGithub client."""
    return Github(settings.github_token)


def get_pr_files(repo_full_name: str, pr_number: int) -> list:
    """Return the list of PullRequestFile objects for a PR."""
    g = _get_client()
    repo = g.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    return list(pr.get_files())


def post_review(
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    findings: list[AIFinding],
    chunks: list[DiffChunk],
    summary_body: str,
) -> Optional[int]:
    """
    Post a unified pull request review with all findings as inline comments.

    Returns the GitHub review ID, or None on failure.
    """
    g = _get_client()
    repo = g.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)

    # Build a position map across *all* chunks: file_path → {diff_line_number: position}
    position_lookup: dict[str, dict[int, int]] = {}
    for chunk in chunks:
        if chunk.file_path not in position_lookup:
            position_lookup[chunk.file_path] = {}
        position_lookup[chunk.file_path].update(chunk.diff_position_map)

    # Construct inline comment objects for PyGithub's create_review.
    comments: list[dict] = []
    for finding in findings:
        file_positions = position_lookup.get(finding.file_path, {})
        position = file_positions.get(finding.diff_line_number)
        if position is None:
            logger.warning(
                "No GitHub position found for %s line %d — skipping inline comment",
                finding.file_path,
                finding.diff_line_number,
            )
            continue

        body = _format_inline_comment(finding)
        comments.append(
            {
                "path": finding.file_path,
                "position": position,
                "body": body,
            }
        )

    # Determine review event based on highest severity found.
    event = _determine_review_event(findings)

    try:
        review = pr.create_review(
            commit=repo.get_commit(head_sha),
            body=summary_body,
            event=event,
            comments=comments,
        )
        logger.info(
            "Posted review %d to %s#%d (%d inline comments)",
            review.id,
            repo_full_name,
            pr_number,
            len(comments),
        )
        return review.id
    except GithubException as exc:
        logger.error("Failed to post review: %s", exc)
        return None


def _format_inline_comment(finding: AIFinding) -> str:
    """Format a single finding as a Markdown inline comment body."""
    severity_emoji = {
        Severity.CRITICAL: "🔴",
        Severity.HIGH: "🟠",
        Severity.MEDIUM: "🟡",
        Severity.LOW: "🔵",
    }
    emoji = severity_emoji.get(finding.severity, "⚪")

    return (
        f"{emoji} **SecureCommit [{finding.severity.value}] — "
        f"{finding.category.value.replace('_', ' ').title()}**\n\n"
        f"{finding.explanation}\n\n"
        f"**Suggested fix:**\n```\n{finding.suggested_fix}\n```\n\n"
        f"*Automated finding by [SecureCommit](https://github.com/apps/securecommit)*"
    )


def _determine_review_event(findings: list[AIFinding]) -> str:
    """Return the GitHub review event string based on the worst severity found."""
    if not findings:
        return "APPROVE"
    severities = {f.severity for f in findings}
    if Severity.CRITICAL in severities or Severity.HIGH in severities:
        return "REQUEST_CHANGES"
    return "COMMENT"
