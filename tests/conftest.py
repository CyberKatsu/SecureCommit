"""
conftest.py — Shared pytest fixtures.

Design decisions:
* settings_override patches get_settings() so tests never need a real .env
  file or real external services.  It includes both Anthropic and Qwen keys
  (both set to stubs) so either provider path can be tested.
* sample_patch / sample_pr_files provide realistic diff data that exercises
  the parser without hitting GitHub.
* mock_ai_call patches `_call_model` — the provider-neutral seam in
  ai_service — so AI tests work identically regardless of which provider is
  configured, and don't depend on the internal SDK shape of either provider.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.webhooks.validation import compute_signature


# ── Settings ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def settings_override():
    """Replace settings with deterministic test values for every test."""
    mock_settings = Settings(
        github_webhook_secret="test-secret-12345",
        github_token="ghp_test_token",
        # Both keys provided as stubs; ai_provider selects which is used.
        anthropic_api_key="sk-ant-test",
        qwen_api_key="sk-qwen-test",
        ai_provider="anthropic",  # default; individual tests may override
        database_url="postgresql+asyncpg://test:test@localhost/test",
        database_url_sync="postgresql+psycopg2://test:test@localhost/test",
        redis_url="redis://localhost:6379/0",
        celery_result_backend="redis://localhost:6379/1",
        debug=True,
    )
    with patch("app.config.get_settings", return_value=mock_settings):
        yield mock_settings


# ── Diff fixtures ─────────────────────────────────────────────────────────────

SAMPLE_PATCH = """\
@@ -10,6 +10,15 @@ def get_user(user_id):
     db = get_db()
+    query = f"SELECT * FROM users WHERE id = {user_id}"
+    result = db.execute(query)
+    return result.fetchone()
+
+def delete_user(user_id):
+    db = get_db()
+    password = "admin123"
+    query = f"DELETE FROM users WHERE id = {user_id}"
+    db.execute(query)"""


@pytest.fixture
def sample_patch() -> str:
    return SAMPLE_PATCH


@pytest.fixture
def sample_pr_file(sample_patch):
    """Mimic a PyGithub PullRequestFile object."""
    f = MagicMock()
    f.filename = "app/users.py"
    f.patch = sample_patch
    return f


@pytest.fixture
def sample_pr_files(sample_pr_file):
    return [sample_pr_file]


# ── Webhook fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def webhook_secret() -> str:
    return "test-secret-12345"


@pytest.fixture
def sample_pr_payload() -> dict:
    return {
        "action": "opened",
        "number": 42,
        "pull_request": {
            "number": 42,
            "title": "Add user deletion endpoint",
            "html_url": "https://github.com/org/repo/pull/42",
            "head": {"sha": "abc123def456"},
            "base": {"sha": "000000000000"},
        },
        "repository": {
            "id": 12345,
            "full_name": "org/repo",
            "html_url": "https://github.com/org/repo",
        },
    }


@pytest.fixture
def signed_webhook_request(sample_pr_payload, webhook_secret):
    """Return (body_bytes, signature_header) for a valid webhook."""
    body = json.dumps(sample_pr_payload).encode()
    sig = compute_signature(body, webhook_secret)
    return body, sig


# ── AI fixtures ───────────────────────────────────────────────────────────────

SAMPLE_FINDINGS_JSON = json.dumps([
    {
        "file_path": "app/users.py",
        "diff_line_number": 2,
        "severity": "Critical",
        "category": "SQL_INJECTION",
        "explanation": "User input interpolated directly into SQL via f-string.",
        "suggested_fix": "Use cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))",
    },
    {
        "file_path": "app/users.py",
        "diff_line_number": 7,
        "severity": "Critical",
        "category": "HARDCODED_SECRET",
        "explanation": "Hardcoded password literal committed to source code.",
        "suggested_fix": "Load from environment: password = os.environ['ADMIN_PASSWORD']",
    },
])


@pytest.fixture
def mock_ai_call():
    """
    Patch the provider-neutral `_call_model` function in ai_service.

    Returns the mock so tests can change `.return_value` or set `.side_effect`
    for error-path testing.  The default return value is the sample findings
    JSON so happy-path tests work without extra setup.
    """
    with patch(
        "app.services.ai_service._call_model",
        return_value=SAMPLE_FINDINGS_JSON,
    ) as mock:
        yield mock


# Keep the old name as an alias so any test that still references
# `mock_anthropic_client` gets a clear failure message rather than a confusing
# fixture-not-found error.  Remove this alias once all tests use mock_ai_call.
mock_anthropic_client = mock_ai_call
