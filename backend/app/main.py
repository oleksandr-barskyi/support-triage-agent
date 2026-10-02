import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.loop import Limits
from app.agent.model import AgentModel, build_model
from app.agent.runner import RunScheduler
from app.api import actions, runs, tickets
from app.core.settings import Settings, get_settings
from app.db.models import AgentRun, RunStatus, Ticket, TicketStatus

logging.basicConfig(level=logging.INFO)


def create_app(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    model_factory: Callable[[], AgentModel] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if session_factory is None:
        from app.db.session import SessionFactory

        session_factory = SessionFactory
    factory = session_factory

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with factory() as session:
            interrupted = {RunStatus.queued, RunStatus.running}
            await session.execute(
                update(AgentRun)
                .where(AgentRun.status.in_(interrupted))
                .values(status=RunStatus.failed, error="interrupted by a server restart")
            )
            await session.execute(
                update(Ticket)
                .where(Ticket.status == TicketStatus.triaging)
                .values(status=TicketStatus.failed)
            )
            await session.commit()
        yield
        await app.state.scheduler.wait_idle()

    app = FastAPI(title="Support Triage Agent", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.requires_api_key = model_factory is None
    app.state.scheduler = RunScheduler(
        factory,
        model_factory or (lambda: build_model(settings)),
        Limits.from_settings(settings),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(tickets.router)
    app.include_router(runs.router)
    app.include_router(actions.router)
    return app


app = create_app()
