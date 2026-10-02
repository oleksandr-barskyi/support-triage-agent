from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(value: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if value.startswith(prefix):
            value = "postgresql+asyncpg://" + value[len(prefix) :]
    parts = urlsplit(value)
    params = dict(parse_qsl(parts.query))
    sslmode = params.pop("sslmode", None)
    params.pop("channel_binding", None)
    if sslmode and sslmode != "disable":
        params["ssl"] = "require"
    return urlunsplit(parts._replace(query=urlencode(params)))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres@localhost:54329/triage"
    agent_provider: Literal["anthropic", "gemini"] = "anthropic"
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_reasoning_effort: str = "low"
    agent_model: str = "claude-opus-5-5"
    agent_effort: str = "medium"
    agent_fallbacks: bool = True
    agent_max_turns: int = 8
    agent_max_total_tokens: int = 120_000
    agent_timeout_seconds: float = 90.0
    refund_cap: Decimal = Decimal("2000")
    daily_spend_cap_usd: Decimal = Decimal("5")
    runs_per_ip_per_hour: int = 10
    cors_origins: str = "http://localhost:3000"
    input_price_per_mtok: Decimal = Decimal("4")
    output_price_per_mtok: Decimal = Decimal("20")

    @field_validator("database_url")
    @classmethod
    def _asyncpg_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @property
    def model_api_key(self) -> str:
        if self.agent_provider == "gemini":
            return self.gemini_api_key
        return self.anthropic_api_key

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
