import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.api.deps import get_db
from app.api.schemas import RunOut, StepOut
from app.db.models import AgentRun, AgentStep, RunStatus

router = APIRouter(prefix="/runs", tags=["runs"])

TERMINAL = {RunStatus.awaiting_review, RunStatus.escalated, RunStatus.failed, RunStatus.done}
POLL_SECONDS = 0.5
STREAM_MAX_SECONDS = 180


async def _load_run(session: AsyncSession, run_id: int) -> AgentRun | None:
    return await session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .options(selectinload(AgentRun.steps), selectinload(AgentRun.actions))
        .execution_options(populate_existing=True)
    )


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: int, session: AsyncSession = Depends(get_db)) -> RunOut:
    run = await _load_run(session, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return RunOut.model_validate(run)


def _event(name: str, data: str) -> str:
    return f"event: {name}\ndata: {data}\n\n"


async def _stream(
    factory: async_sessionmaker[AsyncSession], run_id: int, request: Request
) -> AsyncIterator[str]:
    last_index = -1
    last_status: RunStatus | None = None
    deadline = time.monotonic() + STREAM_MAX_SECONDS
    while time.monotonic() < deadline:
        if await request.is_disconnected():
            return
        async with factory() as session:
            run_status = await session.scalar(select(AgentRun.status).where(AgentRun.id == run_id))
            if run_status is None:
                yield _event("error", json.dumps({"detail": "Run not found"}))
                return
            steps = (
                await session.scalars(
                    select(AgentStep)
                    .where(AgentStep.run_id == run_id, AgentStep.index > last_index)
                    .order_by(AgentStep.index)
                )
            ).all()
            for step in steps:
                last_index = step.index
                yield _event("step", StepOut.model_validate(step).model_dump_json())
            if run_status != last_status:
                last_status = run_status
                yield _event("status", json.dumps({"status": run_status.value}))
            if run_status in TERMINAL:
                run = await _load_run(session, run_id)
                if run is not None:
                    yield _event("done", RunOut.model_validate(run).model_dump_json())
                return
        yield ": keep-alive\n\n"
        await asyncio.sleep(POLL_SECONDS)


@router.get("/{run_id}/stream")
async def stream_run(run_id: int, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _stream(request.app.state.session_factory, run_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
