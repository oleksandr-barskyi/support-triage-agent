from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runner import RunScheduler
from app.core.settings import Settings
from app.db.models import AgentRun


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_scheduler(request: Request) -> RunScheduler:
    scheduler: RunScheduler = request.app.state.scheduler
    return scheduler


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def guard_new_run(
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> str:
    if not settings.model_api_key and request.app.state.requires_api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Model API key is not configured.")
    ip = client_ip(request)
    now = datetime.now(UTC)
    recent = await session.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.client_ip == ip, AgentRun.created_at > now - timedelta(hours=1)
        )
    )
    if (recent or 0) >= settings.runs_per_ip_per_hour:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit reached for this demo. Try again later."
        )
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    spent = await session.scalar(
        select(func.coalesce(func.sum(AgentRun.cost_usd), 0)).where(
            AgentRun.created_at >= start_of_day
        )
    )
    if Decimal(spent or 0) >= settings.daily_spend_cap_usd:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The demo's daily model budget is used up. Seeded tickets stay browsable.",
        )
    return ip
