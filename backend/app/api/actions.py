from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent import schemas as tool_schemas
from app.api.deps import get_db
from app.api.schemas import ActionOut, ApproveIn, RejectIn
from app.db.models import (
    ActionStatus,
    ActionType,
    AgentRun,
    Order,
    ProposedAction,
    RunStatus,
    TicketStatus,
)

router = APIRouter(prefix="/actions", tags=["actions"])

PAYLOAD_MODELS: dict[ActionType, type[BaseModel]] = {
    ActionType.triage: tool_schemas.TriageInput,
    ActionType.reply: tool_schemas.ReplyInput,
    ActionType.refund: tool_schemas.RefundInput,
    ActionType.escalate: tool_schemas.EscalateInput,
}


async def _pending_action(session: AsyncSession, action_id: int) -> ProposedAction:
    action = await session.scalar(
        select(ProposedAction)
        .where(ProposedAction.id == action_id)
        .options(selectinload(ProposedAction.run).selectinload(AgentRun.ticket))
        .with_for_update(of=ProposedAction)
    )
    if action is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found")
    if action.status != ActionStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Action is already {action.status.value}")
    return action


async def _apply(session: AsyncSession, action: ProposedAction, payload: dict[str, Any]) -> None:
    ticket = action.run.ticket
    if action.type == ActionType.triage:
        ticket.category = payload["category"]
        ticket.priority = payload["priority"]
    elif action.type == ActionType.reply:
        ticket.status = TicketStatus.answered
    elif action.type == ActionType.escalate:
        ticket.status = TicketStatus.escalated
    elif action.type == ActionType.refund:
        order = await session.get(Order, payload["order_id"], with_for_update=True)
        if order is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Order no longer exists")
        amount = Decimal(str(payload["amount"]))
        if amount > order.amount - order.refunded_amount:
            raise HTTPException(status.HTTP_409_CONFLICT, "Amount exceeds refundable balance")
        order.refunded_amount += amount
        order.status = "refunded" if order.refunded_amount == order.amount else "partially_refunded"


async def _close_run_if_reviewed(session: AsyncSession, run: AgentRun) -> None:
    pending = await session.scalar(
        select(ProposedAction.id).where(
            ProposedAction.run_id == run.id, ProposedAction.status == ActionStatus.pending
        )
    )
    if pending is None and run.status in {RunStatus.awaiting_review, RunStatus.escalated}:
        run.status = RunStatus.done


@router.post("/{action_id}/approve", response_model=ActionOut)
async def approve(
    action_id: int, data: ApproveIn, session: AsyncSession = Depends(get_db)
) -> ActionOut:
    action = await _pending_action(session, action_id)
    payload = action.payload
    if data.payload is not None and data.payload != action.payload:
        try:
            payload = (
                PAYLOAD_MODELS[action.type].model_validate(data.payload).model_dump(mode="json")
            )
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                exc.errors(include_url=False, include_context=False, include_input=False),
            ) from exc
        action.payload = payload
        action.edited = True
    await _apply(session, action, payload)
    action.status = ActionStatus.approved
    action.decision_note = data.note
    action.decided_at = datetime.now(UTC)
    await session.flush()
    await _close_run_if_reviewed(session, action.run)
    await session.commit()
    return ActionOut.model_validate(action)


@router.post("/{action_id}/reject", response_model=ActionOut)
async def reject(
    action_id: int, data: RejectIn, session: AsyncSession = Depends(get_db)
) -> ActionOut:
    action = await _pending_action(session, action_id)
    action.status = ActionStatus.rejected
    action.decision_note = data.note
    action.decided_at = datetime.now(UTC)
    await session.flush()
    await _close_run_if_reviewed(session, action.run)
    await session.commit()
    return ActionOut.model_validate(action)
