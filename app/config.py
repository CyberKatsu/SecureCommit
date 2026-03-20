"""
config.py — Centralised settings via pydantic-settings.

Design decision: A single Settings class loaded once at import time keeps
every magic string in one place and enforces type-safety via Pydantic.
`model_config = SettingsConfigDict(env_file=".env")` means the same class
works in development (reading from .env) and production (real env vars).

AI provider selection:
  Set AI_PROVIDER=anthropic (default) or AI_PROVIDER=qwen.
  Only the key for the selected provider is required at runtime.
  Switching providers requires no code changes — only .env changes.
"""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "SecureCommit"
    debug: bool = False

    # ── GitHub App ───────────────────────────────────────────────────────────
    github_webhook_secret: str
    github_token: str
    github_app_id: str = ""
    github_private_key: str = ""

    # ── AI provider selector ─────────────────────────────────────────────────
    # Which LLM backend to use.  Switching is purely a config change.
    ai_provider: Literal["anthropic", "qwen"] = "anthropic"

    # Shared token budget for analysis calls (used by both providers).
    ai_max_tokens: int = 4096

    # ── Anthropic ────────────────────────────────────────────────────────────
    # Required when ai_provider="anthropic"; ignored otherwise.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    # ── Qwen (Alibaba DashScope — OpenAI-compatible endpoint) ────────────────
    # Required when ai_provider="qwen"; ignored otherwise.
    # Models: qwen-plus (balanced), qwen-turbo (fast), qwen-max (best quality)
    qwen_api_key: str = ""
    qwen_model: str = "qwen-plus"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # ── Celery / Redis ───────────────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # ── PostgreSQL ───────────────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://securecommit:securecommit@postgres:5432/securecommit"
    )
    database_url_sync: str = (
        "postgresql+psycopg2://securecommit:securecommit@postgres:5432/securecommit"
    )

    # ── Diff processing ──────────────────────────────────────────────────────
    max_diff_lines_per_chunk: int = 300

    @model_validator(mode="after")
    def _require_provider_key(self) -> "Settings":
        """Fail fast if the selected provider has no API key configured."""
        if self.ai_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY must be set when AI_PROVIDER=anthropic"
            )
        if self.ai_provider == "qwen" and not self.qwen_api_key:
            raise ValueError(
                "QWEN_API_KEY must be set when AI_PROVIDER=qwen"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton.  FastAPI's Depends(get_settings) is safe."""
    return Settings()  # type: ignore[call-arg]
