import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from itertools import count
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.agent.loop import Limits
from app.agent.model import ModelTurn
from app.core.settings import Settings, normalize_database_url
from app.db.models import Base
from app.main import create_app
from app.seed import seed

TEST_DATABASE_URL = normalize_database_url(
    os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://postgres@localhost:54329/triage_test")
)
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_ids = count(1)


def tool_call(name: str, **tool_input: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": tool_input}


def tool_turn(*calls: dict[str, Any], text: str = "") -> ModelTurn:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    return ModelTurn(
        content=content + list(calls), stop_reason="tool_use", input_tokens=1000, output_tokens=200
    )


def final_turn(text: str = "Done.", stop_reason: str = "end_turn") -> ModelTurn:
    return ModelTurn(
        content=[{"type": "text", "text": text}],
        stop_reason=stop_reason,
        input_tokens=1000,
        output_tokens=50,
    )


@dataclass
class ScriptedModel:
    turns: list[ModelTurn]
    name: str = "scripted"
    calls: list[list[dict[str, Any]]] = field(default_factory=list)
    error: Exception | None = None

    async def create(
        self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]
    ) -> ModelTurn:
        self.calls.append([dict(m) for m in messages])
        if self.error is not None:
            raise self.error
        if not self.turns:
            return tool_turn(tool_call("search_kb", query="anything"))
        return self.turns.pop(0)


LIMITS = Limits(
    max_turns=6,
    max_total_tokens=100_000,
    timeout_seconds=10,
    refund_cap=Decimal("2000"),
    input_price_per_mtok=Decimal("4"),
    output_price_per_mtok=Decimal("20"),
)


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await seed(session, now=NOW)
    yield maker


@pytest.fixture
async def session(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as s:
        yield s


@pytest.fixture
def model() -> ScriptedModel:
    return ScriptedModel(turns=[])


@pytest.fixture
def app(factory: async_sessionmaker[AsyncSession], model: ScriptedModel) -> FastAPI:
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        agent_max_turns=LIMITS.max_turns,
        agent_timeout_seconds=LIMITS.timeout_seconds,
        runs_per_ip_per_hour=3,
    )
    return create_app(settings=settings, session_factory=factory, model_factory=lambda: model)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
