import asyncio
import sys

from sqlalchemy import select

from app.agent.loop import Limits, run_agent
from app.agent.model import build_model
from app.core.settings import get_settings
from app.db.models import AgentRun, RunStatus, Ticket


async def main(limit: int | None) -> None:
    from app.db.session import SessionFactory, engine

    settings = get_settings()
    model = build_model(settings)
    limits = Limits.from_settings(settings)
    async with SessionFactory() as session:
        without_runs = (
            await session.scalars(
                select(Ticket.id)
                .where(~Ticket.runs.any(AgentRun.status != RunStatus.failed))
                .order_by(Ticket.id)
                .limit(limit)
            )
        ).all()
        run_ids = []
        for ticket_id in without_runs:
            run = AgentRun(ticket_id=ticket_id, model=model.name, client_ip="prerun")
            session.add(run)
            await session.flush()
            run_ids.append(run.id)
        await session.commit()
    for run_id in run_ids:
        await run_agent(SessionFactory, model, run_id, limits)
        async with SessionFactory() as session:
            done = await session.get(AgentRun, run_id)
            if done is not None:
                print(f"run {run_id}: {done.status.value}, ${done.cost_usd}, {done.duration_ms} ms")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else None))
