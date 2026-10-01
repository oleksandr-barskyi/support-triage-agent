from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.runner import RunScheduler
from app.api.deps import get_db, get_scheduler, guard_new_run
from app.api.schemas import RunOut, RunSummary, TicketCreate, TicketListItem, TicketOut
from app.db.models import AgentRun, RunStatus, Ticket, TicketStatus

router = APIRouter(prefix="/tickets", tags=["tickets"])

ACTIVE = {RunStatus.queued, RunStatus.running}


async def _load_ticket(session: AsyncSession, ticket_id: int) -> TicketOut:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.ticket_id == ticket_id)
        .order_by(AgentRun.id.desc())
        .limit(1)
        .options(selectinload(AgentRun.steps), selectinload(AgentRun.actions))
    )
    out = TicketOut.model_validate(ticket, from_attributes=True)
    out.latest_run = RunOut.model_validate(run) if run else None
    return out


async def _start_run(
    session: AsyncSession, scheduler: RunScheduler, ticket: Ticket, ip: str
) -> None:
    run = AgentRun(ticket_id=ticket.id, model=scheduler.model.name, client_ip=ip)
    session.add(run)
    ticket.status = TicketStatus.triaging
    await session.commit()
    scheduler.start(run.id)


@router.get("", response_model=list[TicketListItem])
async def list_tickets(session: AsyncSession = Depends(get_db)) -> list[TicketListItem]:
    tickets = (
        await session.scalars(
            select(Ticket).order_by(Ticket.created_at.desc(), Ticket.id.desc()).limit(100)
        )
    ).all()
    latest = {
        r.ticket_id: r
        for r in (
            await session.scalars(
                select(AgentRun)
                .where(AgentRun.ticket_id.in_([t.id for t in tickets]))
                .order_by(AgentRun.id)
            )
        ).all()
    }
    items = []
    for t in tickets:
        item = TicketListItem.model_validate(t)
        run = latest.get(t.id)
        item.latest_run = RunSummary.model_validate(run) if run else None
        items.append(item)
    return items


@router.get("/{ticket_id}", response_model=TicketOut)
async def get_ticket(ticket_id: int, session: AsyncSession = Depends(get_db)) -> TicketOut:
    return await _load_ticket(session, ticket_id)


@router.post("", response_model=TicketOut, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    data: TicketCreate,
    ip: str = Depends(guard_new_run),
    session: AsyncSession = Depends(get_db),
    scheduler: RunScheduler = Depends(get_scheduler),
) -> TicketOut:
    ticket = Ticket(customer_email=data.customer_email, subject=data.subject, body=data.body)
    session.add(ticket)
    await session.flush()
    await _start_run(session, scheduler, ticket, ip)
    return await _load_ticket(session, ticket.id)


@router.post("/{ticket_id}/rerun", response_model=TicketOut)
async def rerun_ticket(
    ticket_id: int,
    ip: str = Depends(guard_new_run),
    session: AsyncSession = Depends(get_db),
    scheduler: RunScheduler = Depends(get_scheduler),
) -> TicketOut:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    active = await session.scalar(
        select(AgentRun.id).where(AgentRun.ticket_id == ticket_id, AgentRun.status.in_(ACTIVE))
    )
    if active is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The agent is already working on this ticket")
    await _start_run(session, scheduler, ticket, ip)
    return await _load_ticket(session, ticket_id)
