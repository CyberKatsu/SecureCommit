"""
test_ai_service.py — Tests for the AI service layer.

All tests patch `_call_model` (the provider-neutral seam) so they run
offline and pass identically whether ai_provider is "anthropic" or "qwen".

Provider-specific tests (test_anthropic_provider_*, test_qwen_provider_*)
exercise the individual provider classes in isolation, verifying that each
translates the abstract `.call()` into the correct SDK call shape.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

from app.models.schemas import AIFinding, Severity, VulnerabilityCategory
from app.services.ai_service import (
    _AnthropicProvider,
    _QwenProvider,
    analyse_chunk,
    generate_summary,
)
from app.services.diff_parser import parse_file_patch
from tests.conftest import SAMPLE_FINDINGS_JSON


# ── analyse_chunk — happy path ────────────────────────────────────────────────

def test_analyse_chunk_returns_findings(mock_ai_call, sample_patch):
    chunk = parse_file_patch("app/users.py", sample_patch)
    findings = analyse_chunk(chunk)
    assert len(findings) == 2


def test_analyse_chunk_finding_fields(mock_ai_call, sample_patch):
    chunk = parse_file_patch("app/users.py", sample_patch)
    findings = analyse_chunk(chunk)

    f = findings[0]
    assert isinstance(f, AIFinding)
    assert f.file_path == "app/users.py"
    assert f.severity == Severity.CRITICAL
    assert f.category == VulnerabilityCategory.SQL_INJECTION
    assert len(f.explanation) > 10
    assert len(f.suggested_fix) > 5


def test_analyse_chunk_calls_model_once(mock_ai_call, sample_patch):
    chunk = parse_file_patch("f.py", sample_patch)
    analyse_chunk(chunk)
    mock_ai_call.assert_called_once()


def test_analyse_chunk_passes_security_system_prompt(mock_ai_call, sample_patch):
    """The security review system prompt must be the first argument to _call_model."""
    from app.prompts import SECURITY_REVIEW_SYSTEM_PROMPT
    chunk = parse_file_patch("f.py", sample_patch)
    analyse_chunk(chunk)

    args, _ = mock_ai_call.call_args
    assert args[0] == SECURITY_REVIEW_SYSTEM_PROMPT


def test_analyse_chunk_user_message_contains_file_path(mock_ai_call, sample_patch):
    chunk = parse_file_patch("src/special_file.py", sample_patch)
    analyse_chunk(chunk)

    args, _ = mock_ai_call.call_args
    user_message = args[1]
    assert "src/special_file.py" in user_message


# ── analyse_chunk — error handling ───────────────────────────────────────────

def test_analyse_chunk_returns_empty_on_invalid_json(sample_patch):
    with patch("app.services.ai_service._call_model", return_value="Not JSON!"):
        chunk = parse_file_patch("f.py", sample_patch)
        findings = analyse_chunk(chunk)
    assert findings == []


def test_analyse_chunk_returns_empty_on_provider_error(sample_patch):
    with patch(
        "app.services.ai_service._call_model",
        side_effect=RuntimeError("connection refused"),
    ):
        chunk = parse_file_patch("f.py", sample_patch)
        findings = analyse_chunk(chunk)
    assert findings == []


def test_analyse_chunk_returns_empty_array_response(sample_patch):
    with patch("app.services.ai_service._call_model", return_value="[]"):
        chunk = parse_file_patch("f.py", sample_patch)
        findings = analyse_chunk(chunk)
    assert findings == []


def test_analyse_chunk_handles_lowercase_severity(sample_patch):
    data = json.dumps([{
        "file_path": "f.py",
        "diff_line_number": 1,
        "severity": "critical",
        "category": "SQL_INJECTION",
        "explanation": "SQL injection vulnerability",
        "suggested_fix": "Use parameterised queries",
    }])
    with patch("app.services.ai_service._call_model", return_value=data):
        chunk = parse_file_patch("f.py", sample_patch)
        findings = analyse_chunk(chunk)
    assert findings[0].severity == Severity.CRITICAL


def test_analyse_chunk_unknown_category_becomes_other(sample_patch):
    data = json.dumps([{
        "file_path": "f.py",
        "diff_line_number": 1,
        "severity": "Low",
        "category": "SOME_FUTURE_CATEGORY",
        "explanation": "Some issue",
        "suggested_fix": "Fix it",
    }])
    with patch("app.services.ai_service._call_model", return_value=data):
        chunk = parse_file_patch("f.py", sample_patch)
        findings = analyse_chunk(chunk)
    assert findings[0].category == VulnerabilityCategory.OTHER


# ── generate_summary ──────────────────────────────────────────────────────────

def test_generate_summary_no_findings_skips_api():
    with patch("app.services.ai_service._call_model") as mock_call:
        summary = generate_summary([])
    mock_call.assert_not_called()
    assert "No security vulnerabilities" in summary
    assert "SecureCommit" in summary


def test_generate_summary_calls_model_with_findings_json(mock_ai_call, sample_patch):
    chunk = parse_file_patch("f.py", sample_patch)
    findings = analyse_chunk(chunk)

    mock_ai_call.reset_mock()
    mock_ai_call.return_value = "## Summary\n\nFound 2 issues."

    summary = generate_summary(findings)
    mock_ai_call.assert_called_once()

    _, user_arg, _ = mock_ai_call.call_args[0]
    assert "findings_json" not in user_arg   # template was rendered
    assert "app/users.py" in user_arg        # finding file path present


def test_generate_summary_falls_back_on_provider_error(mock_ai_call, sample_patch):
    chunk = parse_file_patch("f.py", sample_patch)
    findings = analyse_chunk(chunk)

    mock_ai_call.reset_mock()
    mock_ai_call.side_effect = RuntimeError("rate limit")

    summary = generate_summary(findings)
    assert "SecureCommit" in summary
    assert "Critical" in summary or "CRITICAL" in summary.upper()


# ── _AnthropicProvider — SDK call shape ───────────────────────────────────────

def test_anthropic_provider_calls_messages_create(settings_override):
    """_AnthropicProvider must use the Anthropic messages.create() shape."""
    settings_override.anthropic_api_key = "sk-ant-test"
    settings_override.ai_provider = "anthropic"

    mock_content = MagicMock()
    mock_content.text = "response text"
    mock_response = MagicMock()
    mock_response.content = [mock_content]

    with patch("app.services.ai_service.anthropic.Anthropic") as MockAnthropic:
        instance = MockAnthropic.return_value
        instance.messages.create.return_value = mock_response

        provider = _AnthropicProvider()
        result = provider.call("sys", "user", 100)

    instance.messages.create.assert_called_once()
    kwargs = instance.messages.create.call_args.kwargs
    assert kwargs["system"] == "sys"
    assert kwargs["messages"] == [{"role": "user", "content": "user"}]
    assert kwargs["max_tokens"] == 100
    assert result == "response text"


def test_anthropic_provider_uses_configured_model(settings_override):
    mock_content = MagicMock()
    mock_content.text = "ok"
    mock_response = MagicMock()
    mock_response.content = [mock_content]

    with patch("app.services.ai_service.settings") as mock_s, \
         patch("app.services.ai_service.anthropic.Anthropic") as MockAnthropic:
        mock_s.anthropic_model = "claude-opus-4-20250514"
        mock_s.anthropic_api_key = "sk-ant-test"
        MockAnthropic.return_value.messages.create.return_value = mock_response

        provider = _AnthropicProvider()
        provider.call("s", "u", 10)

    kwargs = MockAnthropic.return_value.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-opus-4-20250514"


# ── _QwenProvider — SDK call shape ────────────────────────────────────────────

def test_qwen_provider_calls_chat_completions(settings_override):
    """_QwenProvider must send system as a role message, not a top-level param."""
    settings_override.qwen_api_key = "sk-qwen-test"
    settings_override.qwen_model = "qwen-plus"
    settings_override.qwen_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    mock_message = MagicMock()
    mock_message.content = "qwen response"
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("app.services.ai_service.OpenAI") as MockOpenAI:
        instance = MockOpenAI.return_value
        instance.chat.completions.create.return_value = mock_response

        provider = _QwenProvider()
        result = provider.call("system text", "user text", 200)

    instance.chat.completions.create.assert_called_once()
    kwargs = instance.chat.completions.create.call_args.kwargs
    messages = kwargs["messages"]

    # System must be a message dict, not a top-level kwarg.
    assert {"role": "system", "content": "system text"} in messages
    assert {"role": "user", "content": "user text"} in messages
    assert kwargs["max_tokens"] == 200
    assert result == "qwen response"


def test_qwen_provider_uses_configured_model(settings_override):
    mock_message = MagicMock()
    mock_message.content = "ok"
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch("app.services.ai_service.settings") as mock_s, \
         patch("app.services.ai_service.OpenAI") as MockOpenAI:
        mock_s.qwen_model = "qwen-max"
        mock_s.qwen_api_key = "sk-qwen-test"
        mock_s.qwen_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        MockOpenAI.return_value.chat.completions.create.return_value = mock_response

        provider = _QwenProvider()
        provider.call("s", "u", 10)

    kwargs = MockOpenAI.return_value.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "qwen-max"


def test_qwen_provider_initialised_with_correct_base_url(settings_override):
    with patch("app.services.ai_service.settings") as mock_s, \
         patch("app.services.ai_service.OpenAI") as MockOpenAI:
        mock_s.qwen_api_key = "sk-qwen-test"
        mock_s.qwen_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

        _QwenProvider()

    MockOpenAI.assert_called_once_with(
        api_key="sk-qwen-test",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )


# ── Config validation ─────────────────────────────────────────────────────────

def test_settings_requires_anthropic_key_when_provider_is_anthropic():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        from app.config import Settings
        Settings(
            github_webhook_secret="s",
            github_token="t",
            ai_provider="anthropic",
            anthropic_api_key="",   # missing
            qwen_api_key="sk-qwen",
        )


def test_settings_requires_qwen_key_when_provider_is_qwen():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="QWEN_API_KEY"):
        from app.config import Settings
        Settings(
            github_webhook_secret="s",
            github_token="t",
            ai_provider="qwen",
            anthropic_api_key="sk-ant",
            qwen_api_key="",   # missing
        )


def test_settings_accepts_qwen_provider_with_key():
    from app.config import Settings
    s = Settings(
        github_webhook_secret="s",
        github_token="t",
        ai_provider="qwen",
        anthropic_api_key="",
        qwen_api_key="sk-qwen-real",
    )
    assert s.ai_provider == "qwen"
    assert s.qwen_api_key == "sk-qwen-real"
