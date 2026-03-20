"""
test_validation.py — Tests for HMAC webhook signature verification.

These tests are the most security-critical in the project: if signature
verification is broken, an attacker can forge arbitrary webhook events.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.webhooks.validation import compute_signature, verify_github_signature


# ── compute_signature ─────────────────────────────────────────────────────────

def test_compute_signature_format():
    sig = compute_signature(b"hello", "secret")
    assert sig.startswith("sha256=")
    assert len(sig) == len("sha256=") + 64  # SHA-256 hex digest is 64 chars


def test_compute_signature_deterministic():
    body = b'{"action":"opened"}'
    assert compute_signature(body, "s3cr3t") == compute_signature(body, "s3cr3t")


def test_compute_signature_changes_with_body():
    assert compute_signature(b"body1", "secret") != compute_signature(b"body2", "secret")


def test_compute_signature_changes_with_secret():
    assert compute_signature(b"body", "secret1") != compute_signature(b"body", "secret2")


def test_compute_signature_matches_reference():
    """Cross-check against a manually computed reference value."""
    body = b"test payload"
    secret = "my-secret"
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    assert compute_signature(body, secret) == expected


# ── verify_github_signature (via AsyncMock / httpx) ──────────────────────────

@pytest.mark.anyio
async def test_valid_signature_returns_body(webhook_secret):
    body = b'{"action":"opened"}'
    sig = compute_signature(body, webhook_secret)

    from unittest.mock import AsyncMock, MagicMock
    request = MagicMock()
    request.headers = {"X-Hub-Signature-256": sig}
    request.body = AsyncMock(return_value=body)

    result = await verify_github_signature(request, webhook_secret)
    assert result == body


@pytest.mark.anyio
async def test_missing_signature_raises_401(webhook_secret):
    from unittest.mock import AsyncMock, MagicMock
    request = MagicMock()
    request.headers = {}
    request.body = AsyncMock(return_value=b"body")

    with pytest.raises(HTTPException) as exc_info:
        await verify_github_signature(request, webhook_secret)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_wrong_signature_raises_401(webhook_secret):
    from unittest.mock import AsyncMock, MagicMock
    body = b'{"action":"opened"}'
    bad_sig = compute_signature(body, "wrong-secret")

    request = MagicMock()
    request.headers = {"X-Hub-Signature-256": bad_sig}
    request.body = AsyncMock(return_value=body)

    with pytest.raises(HTTPException) as exc_info:
        await verify_github_signature(request, webhook_secret)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_malformed_signature_header_raises_401(webhook_secret):
    from unittest.mock import AsyncMock, MagicMock
    request = MagicMock()
    request.headers = {"X-Hub-Signature-256": "not-sha256-prefixed"}
    request.body = AsyncMock(return_value=b"body")

    with pytest.raises(HTTPException) as exc_info:
        await verify_github_signature(request, webhook_secret)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_tampered_body_raises_401(webhook_secret):
    """Signing a different body than what's received must fail."""
    from unittest.mock import AsyncMock, MagicMock
    original_body = b'{"action":"opened"}'
    tampered_body = b'{"action":"closed"}'
    sig = compute_signature(original_body, webhook_secret)

    request = MagicMock()
    request.headers = {"X-Hub-Signature-256": sig}
    request.body = AsyncMock(return_value=tampered_body)

    with pytest.raises(HTTPException) as exc_info:
        await verify_github_signature(request, webhook_secret)
    assert exc_info.value.status_code == 401
