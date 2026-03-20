"""
ai_service.py — Provider-agnostic AI integration for security analysis.

Design decisions:

Provider abstraction
  Both Anthropic and Qwen expose a text-in / text-out interface, so we define
  a minimal _AIProvider protocol with a single `.call(system, user, max_tokens)
  → str` method.  `_AnthropicProvider` wraps the Anthropic SDK; `_QwenProvider`
  wraps the OpenAI SDK pointed at Alibaba DashScope's OpenAI-compatible endpoint
  (https://dashscope.aliyuncs.com/compatible-mode/v1).  Callers only ever
  interact with `_call_model()`, which dispatches to whichever provider is
  configured.  Switching providers is a one-line .env change.

Why the OpenAI SDK for Qwen?
  DashScope exposes a fully OpenAI-compatible REST API — same paths, same JSON
  shapes.  The `openai` Python SDK accepts a custom `base_url`, so we get all
  of OpenAI's error types, retry logic, and timeout handling for free without
  installing a separate Alibaba SDK.

Error handling
  `_call_model()` lets all exceptions propagate; `analyse_chunk()` catches them
  at its boundary and returns [] so a single bad chunk doesn't abort the whole
  review.  This is provider-agnostic: APIConnectionError, RateLimitError, and
  any Qwen-specific errors are all subclasses of Exception.

Test seam
  `_call_model` is the public-ish boundary that tests patch.  Patching it
  returns a raw JSON string, making tests completely provider-neutral.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

import anthropic
from openai import OpenAI

from app.config import get_settings
from app.models.schemas import AIFinding, AIReviewResponse
from app.prompts import SECURITY_REVIEW_SYSTEM_PROMPT, SUMMARY_PROMPT_TEMPLATE
from app.services.diff_parser import DiffChunk, build_prompt_for_chunk

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Provider protocol & implementations ───────────────────────────────────────

class _AIProvider(Protocol):
    def call(self, system: str, user: str, max_tokens: int) -> str:
        """Send a system + user message; return the model's text response."""
        ...


class _AnthropicProvider:
    """Wraps the official Anthropic Python SDK."""

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def call(self, system: str, user: str, max_tokens: int) -> str:
        response = self._client.messages.create(
            model=settings.anthropic_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text


class _QwenProvider:
    """
    Wraps the OpenAI SDK pointed at Alibaba DashScope's compatible endpoint.

    System messages are sent as a separate {"role": "system"} message rather
    than a top-level `system` parameter, which is the OpenAI convention and
    what DashScope expects.
    """

    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
        )

    def call(self, system: str, user: str, max_tokens: int) -> str:
        response = self._client.chat.completions.create(
            model=settings.qwen_model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


def _make_provider() -> _AIProvider:
    """Instantiate the correct provider based on settings."""
    if settings.ai_provider == "qwen":
        logger.info("AI provider: Qwen (%s)", settings.qwen_model)
        return _QwenProvider()
    logger.info("AI provider: Anthropic (%s)", settings.anthropic_model)
    return _AnthropicProvider()


# Module-level singleton — one per process.
_provider: _AIProvider = _make_provider()


# ── Core internal function (the test seam) ────────────────────────────────────

def _call_model(system: str, user: str, max_tokens: int) -> str:
    """
    Single dispatch point for all AI calls.

    Patching `app.services.ai_service._call_model` in tests gives full
    control over AI responses without coupling tests to a specific provider.
    """
    return _provider.call(system, user, max_tokens)


# ── Public API ────────────────────────────────────────────────────────────────

def analyse_chunk(chunk: DiffChunk) -> list[AIFinding]:
    """
    Send a single DiffChunk to the configured AI provider and return findings.

    Returns [] on any provider error or unparseable response — a partial review
    is better than a crashed task.
    """
    user_message = build_prompt_for_chunk(chunk)

    try:
        raw_text = _call_model(
            SECURITY_REVIEW_SYSTEM_PROMPT,
            user_message,
            settings.ai_max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 — intentionally provider-agnostic
        logger.error(
            "AI provider error for %s [provider=%s]: %s",
            chunk.file_path,
            settings.ai_provider,
            exc,
        )
        return []

    logger.debug("AI raw response for %s:\n%s", chunk.file_path, raw_text)
    raw_text = raw_text.strip()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.warning(
            "Could not parse AI JSON for %s. Raw text: %.200s",
            chunk.file_path,
            raw_text,
        )
        return []

    try:
        review = AIReviewResponse.model_validate(data)
        return review.findings
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pydantic validation failed for %s: %s", chunk.file_path, exc)
        return []


def analyse_chunks(chunks: list[DiffChunk]) -> list[AIFinding]:
    """Analyse all chunks and return a merged flat list of findings."""
    all_findings: list[AIFinding] = []
    for chunk in chunks:
        findings = analyse_chunk(chunk)
        logger.info("Chunk %s → %d finding(s)", chunk.file_path, len(findings))
        all_findings.extend(findings)
    return all_findings


def generate_summary(findings: list[AIFinding]) -> str:
    """
    Ask the AI provider to write a Markdown summary for the top-level PR
    review comment.  Falls back to a plain enumeration on API failure.
    """
    if not findings:
        return (
            "## ✅ SecureCommit Security Review\n\n"
            "No security vulnerabilities were detected in this pull request.\n\n"
            "*Reviewed by [SecureCommit](https://github.com/apps/securecommit)*"
        )

    findings_json = json.dumps(
        [f.model_dump() for f in findings], indent=2, default=str
    )
    prompt = SUMMARY_PROMPT_TEMPLATE.format(findings_json=findings_json)

    try:
        summary = _call_model(
            "You are SecureCommit, a security-focused pull request reviewer.",
            prompt,
            1024,
        )
        return summary.strip()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to generate summary [provider=%s]: %s", settings.ai_provider, exc)
        lines = ["## 🔒 SecureCommit Security Review\n"]
        for f in findings:
            lines.append(
                f"- **[{f.severity.value}]** `{f.file_path}:{f.diff_line_number}` — "
                f"{f.category.value}: {f.explanation[:120]}"
            )
        return "\n".join(lines)
