"""
test_github_formatting.py — Tests for GitHub comment body formatting and
the webhook FastAPI endpoint.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.models.schemas import AIFinding, Severity, VulnerabilityCategory
from app.services.github_service import (
    _determine_review_event,
    _format_inline_comment,
)
from app.webhooks.validation import compute_signature


# ── _format_inline_comment ────────────────────────────────────────────────────

def _make_finding(**kwargs) -> AIFinding:
    defaults = dict(
        file_path="app/auth.py",
        diff_line_number=10,
        severity=Severity.HIGH,
        category=VulnerabilityCategory.SQL_INJECTION,
        explanation="SQL injection via f-string.",
        suggested_fix="Use parameterised queries.",
    )
    defaults.update(kwargs)
    return AIFinding(**defaults)


def test_format_inline_comment_contains_severity():
    finding = _make_finding(severity=Severity.CRITICAL)
    body = _format_inline_comment(finding)
    assert "Critical" in body


def test_format_inline_comment_contains_category():
    finding = _make_finding(category=VulnerabilityCategory.HARDCODED_SECRET)
    body = _format_inline_comment(finding)
    assert "Hardcoded Secret" in body


def test_format_inline_comment_contains_explanation():
    finding = _make_finding(explanation="Unique explanation XYZ")
    body = _format_inline_comment(finding)
    assert "Unique explanation XYZ" in body


def test_format_inline_comment_contains_suggested_fix():
    finding = _make_finding(suggested_fix="Use %s placeholder")
    body = _format_inline_comment(finding)
    assert "Use %s placeholder" in body


def test_format_inline_comment_critical_has_red_emoji():
    finding = _make_finding(severity=Severity.CRITICAL)
    body = _format_inline_comment(finding)
    assert "🔴" in body


def test_format_inline_comment_low_has_blue_emoji():
    finding = _make_finding(severity=Severity.LOW)
    body = _format_inline_comment(finding)
    assert "🔵" in body


# ── _determine_review_event ───────────────────────────────────────────────────

def test_no_findings_returns_approve():
    assert _determine_review_event([]) == "APPROVE"


def test_critical_returns_request_changes():
    f = _make_finding(severity=Severity.CRITICAL)
    assert _determine_review_event([f]) == "REQUEST_CHANGES"


def test_high_returns_request_changes():
    f = _make_finding(severity=Severity.HIGH)
    assert _determine_review_event([f]) == "REQUEST_CHANGES"


def test_medium_returns_comment():
    f = _make_finding(severity=Severity.MEDIUM)
    assert _determine_review_event([f]) == "COMMENT"


def test_low_returns_comment():
    f = _make_finding(severity=Severity.LOW)
    assert _determine_review_event([f]) == "COMMENT"


def test_mixed_severities_worst_wins():
    findings = [
        _make_finding(severity=Severity.LOW),
        _make_finding(severity=Severity.CRITICAL),
        _make_finding(severity=Severity.MEDIUM),
    ]
    assert _determine_review_event(findings) == "REQUEST_CHANGES"


# ── Webhook endpoint ──────────────────────────────────────────────────────────

@pytest.fixture
def test_client():
    """
    FastAPI TestClient with:
    - Celery task mocked at the import site in the router module
      (patching the tasks module alone is insufficient because the router
      already holds a reference to the original function at import time).
    - SQLAlchemy async engine mocked with AsyncMock so lifespan startup/
      shutdown don't try to connect to a real database.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    # Build an async-compatible engine mock.
    mock_conn = AsyncMock()
    mock_conn.run_sync = AsyncMock()
    mock_conn.execute = AsyncMock()

    async_cm = AsyncMock()
    async_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    async_cm.__aexit__ = AsyncMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(return_value=async_cm)
    mock_engine.dispose = AsyncMock()

    mock_task_result = MagicMock()
    mock_task_result.id = "test-celery-task-id"

    with patch("app.main.engine", mock_engine), \
         patch("app.webhooks.router.process_pull_request") as mock_task:
        mock_task.delay.return_value = mock_task_result

        # Import and create the app *inside* the patches so it picks them up.
        from app.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client, mock_task


def test_webhook_valid_signature_accepted(test_client, sample_pr_payload, webhook_secret):
    client, _ = test_client
    body = json.dumps(sample_pr_payload).encode()
    sig = compute_signature(body, webhook_secret)

    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert data["pr"] == 42


def test_webhook_invalid_signature_rejected(test_client, sample_pr_payload):
    client, _ = test_client
    body = json.dumps(sample_pr_payload).encode()

    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeef",
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


def test_webhook_missing_signature_rejected(test_client, sample_pr_payload):
    client, _ = test_client
    body = json.dumps(sample_pr_payload).encode()

    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
    )
    assert response.status_code == 401


def test_webhook_non_pr_event_ignored(test_client, webhook_secret):
    client, _ = test_client
    body = json.dumps({"zen": "Keep it logically awesome."}).encode()
    sig = compute_signature(body, webhook_secret)

    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "ping",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_irrelevant_action_ignored(test_client, sample_pr_payload, webhook_secret):
    client, _ = test_client
    sample_pr_payload["action"] = "labeled"
    body = json.dumps(sample_pr_payload).encode()
    sig = compute_signature(body, webhook_secret)

    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_enqueues_celery_task(test_client, sample_pr_payload, webhook_secret):
    client, mock_task = test_client
    body = json.dumps(sample_pr_payload).encode()
    sig = compute_signature(body, webhook_secret)

    client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )
    mock_task.delay.assert_called_once_with(
        repo_full_name="org/repo",
        pr_number=42,
        pr_title="Add user deletion endpoint",
        pr_url="https://github.com/org/repo/pull/42",
        head_sha="abc123def456",
    )


def test_webhook_synchronize_action_triggers_review(test_client, sample_pr_payload, webhook_secret):
    client, mock_task = test_client
    sample_pr_payload["action"] = "synchronize"
    body = json.dumps(sample_pr_payload).encode()
    sig = compute_signature(body, webhook_secret)

    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202
    mock_task.delay.assert_called_once()


def test_health_endpoint(test_client):
    client, _ = test_client
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
