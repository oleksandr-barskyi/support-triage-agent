from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.agent.loop import Limits, run_agent
from app.agent.model import AgentModel
from app.db.models import AgentRun, Ticket
from app.evals.cases import EvalCase
from app.evals.scoring import CaseResult, score_case


async def run_case(
    factory: async_sessionmaker[AsyncSession], model: AgentModel, limits: Limits, case: EvalCase
) -> CaseResult:
    async with factory() as session:
        ticket = await session.scalar(select(Ticket).where(Ticket.subject == case.subject))
        if ticket is None:
            return CaseResult(case.subject, {"completed": False}, status="missing ticket")
        run = AgentRun(ticket_id=ticket.id, model=model.name, client_ip="eval")
        session.add(run)
        await session.commit()
        run_id = run.id
    await run_agent(factory, model, run_id, limits)
    async with factory() as session:
        done = await session.get(
            AgentRun,
            run_id,
            options=[selectinload(AgentRun.actions), selectinload(AgentRun.steps)],
        )
        assert done is not None
        actions = [(a.type.value, a.payload) for a in done.actions]
        tools = [s.tool_name for s in done.steps if s.tool_name]
        return CaseResult(
            case.subject,
            score_case(case, done.status.value, actions, tools),
            status=done.status.value,
            tokens=done.input_tokens + done.output_tokens,
            duration_ms=done.duration_ms,
            model=done.model,
        )


async def run_evals(
    factory: async_sessionmaker[AsyncSession],
    model: AgentModel,
    limits: Limits,
    cases: list[EvalCase],
) -> list[CaseResult]:
    return [await run_case(factory, model, limits, case) for case in cases]
