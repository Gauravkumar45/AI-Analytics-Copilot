"""Central settings, read from environment variables.

The LLM client speaks the OpenAI-compatible Chat Completions API, so one code
path works with Groq (default), OpenAI, Gemini's OpenAI endpoint or a local
Ollama server (LLM_BASE_URL=http://localhost:11434/v1).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DEMO_DB_PATH = BASE_DIR / "demo" / "sales.db"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # LLM
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://api.groq.com/openai/v1"))
    llm_api_key: str | None = field(default_factory=lambda: _env("LLM_API_KEY") or _env("GROQ_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "llama-3.3-70b-versatile"))
    llm_timeout_seconds: int = field(default_factory=lambda: int(_env("LLM_TIMEOUT_SECONDS", "60")))
    # USD per 1M tokens, for cost tracking. Set to your provider's current price.
    price_in_per_m: float = field(default_factory=lambda: float(_env("LLM_PRICE_IN_PER_M", "0.59")))
    price_out_per_m: float = field(default_factory=lambda: float(_env("LLM_PRICE_OUT_PER_M", "0.79")))

    # Database: the SQLite demo DB by default, or PostgreSQL via DATABASE_URL.
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{DEMO_DB_PATH}"))

    # Agent and SQL safety limits
    max_sql_attempts: int = field(default_factory=lambda: int(_env("MAX_SQL_ATTEMPTS", "3")))
    max_tool_calls: int = field(default_factory=lambda: int(_env("MAX_TOOL_CALLS", "6")))
    max_rows: int = field(default_factory=lambda: int(_env("MAX_RESULT_ROWS", "500")))
    query_timeout_seconds: float = field(default_factory=lambda: float(_env("QUERY_TIMEOUT_SECONDS", "5")))
    # Comma-separated allowlist; empty means every table in the database.
    allowed_tables: tuple[str, ...] = field(default_factory=lambda: tuple(
        t.strip().lower() for t in (_env("ALLOWED_TABLES", "") or "").split(",") if t.strip()
    ))

    # Public demo protection
    rate_limit_per_minute: int = field(default_factory=lambda: int(_env("RATE_LIMIT_PER_MINUTE", "10")))

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sql_dialect(self) -> str:
        return "SQLite" if self.is_sqlite else "PostgreSQL"


def get_settings() -> Settings:
    return Settings()
