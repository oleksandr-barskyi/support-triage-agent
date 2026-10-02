from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.runner import RunScheduler
from app.api.deps import get_db, get_scheduler, guard_new_run
from app.api.schemas import (
    AttachmentOut,
    RunOut,
    RunSummary,
    TicketCreate,
    TicketListItem,
    TicketOut,
)
from app.db.models import AgentRun, Attachment, RunStatus, Ticket, TicketStatus
from app.documents import MAX_BYTES, DocumentError, extract_pdf_text

router = APIRouter(prefix="/tickets", tags=["tickets"])

ACTIVE = {RunStatus.queued, RunStatus.running}
MAX_ATTACHMENTS = 3


async def _load_ticket(session: AsyncSession, ticket_id: int) -> TicketOut:
    ticket = await session.get(
        Ticket, ticket_id, options=[selectinload(Ticket.attachments)], populate_existing=True
    )
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


@router.post(
    "/{ticket_id}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    ticket_id: int, file: UploadFile, session: AsyncSession = Depends(get_db)
) -> AttachmentOut:
    if await session.get(Ticket, ticket_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    existing = await session.scalar(
        select(func.count(Attachment.id)).where(Attachment.ticket_id == ticket_id)
    )
    if (existing or 0) >= MAX_ATTACHMENTS:
        raise HTTPException(status.HTTP_409_CONFLICT, "A ticket holds at most 3 attachments")
    if file.content_type != "application/pdf":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only PDF files are accepted")
    data = await file.read(MAX_BYTES + 1)
    try:
        text, pages = extract_pdf_text(data)
    except DocumentError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    attachment = Attachment(
        ticket_id=ticket_id,
        filename=(file.filename or "document.pdf")[:255],
        content_type="application/pdf",
        size_bytes=len(data),
        pages=pages,
        text=text,
    )
    session.add(attachment)
    await session.commit()
    return AttachmentOut.model_validate(attachment)


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
