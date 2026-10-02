import asyncio
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import anthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.agent.model import AgentModel, ModelAPIError
from app.agent.prompts import CORRECTION, SYSTEM_PROMPT, render_ticket
from app.agent.tools import ToolContext, execute_tool, tool_definitions
from app.core.settings import Settings
from app.db.models import (
    ActionType,
    AgentRun,
    AgentStep,
    ProposedAction,
    RunStatus,
    Ticket,
    TicketStatus,
)

log = logging.getLogger(__name__)
MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Limits:
    max_turns: int
    max_total_tokens: int
    timeout_seconds: float
    refund_cap: Decimal
    input_price_per_mtok: Decimal
    output_price_per_mtok: Decimal

    @classmethod
    def from_settings(cls, s: Settings) -> "Limits":
        return cls(
            max_turns=s.agent_max_turns,
            max_total_tokens=s.agent_max_total_tokens,
            timeout_seconds=s.agent_timeout_seconds,
            refund_cap=s.refund_cap,
            input_price_per_mtok=s.input_price_per_mtok,
            output_price_per_mtok=s.output_price_per_mtok,
        )


class _Trace:
    def __init__(self, session: AsyncSession, run: AgentRun) -> None:
        self.session = session
        self.run = run
        self.index = 0

    def add(self, kind: str, duration_ms: int, **fields: Any) -> None:
        self.session.add(
            AgentStep(
                run_id=self.run.id, index=self.index, kind=kind, duration_ms=duration_ms, **fields
            )
        )
        self.index += 1


async def _proposal_types(session: AsyncSession, run_id: int) -> set[ActionType]:
    rows = await session.scalars(select(ProposedAction.type).where(ProposedAction.run_id == run_id))
    return set(rows.all())


def _is_complete(types: set[ActionType]) -> bool:
    return ActionType.triage in types and bool({ActionType.reply, ActionType.escalate} & types)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


async def run_agent(
    session_factory: async_sessionmaker[AsyncSession],
    model: AgentModel,
    run_id: int,
    limits: Limits,
) -> None:
    async with session_factory() as session:
        run = await session.get(
            AgentRun,
            run_id,
            options=[selectinload(AgentRun.ticket).selectinload(Ticket.attachments)],
        )
        if run is None:
            return
        ticket = run.ticket
        prompt = render_ticket(
            ticket.customer_email,
            ticket.subject,
            ticket.body,
            [(a.filename, a.pages) for a in ticket.attachments],
        )
        run.status = RunStatus.running
        ticket.status = TicketStatus.triaging
        await session.commit()

        trace = _Trace(session, run)
        ctx = ToolContext(session=session, run=run, refund_cap=limits.refund_cap, model=model)
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        started = time.monotonic()
        limit_reason: str | None = None
        error: str | None = None
        corrected = False

        try:
            async with asyncio.timeout(limits.timeout_seconds):
                for _ in range(limits.max_turns):
                    if run.input_tokens + run.output_tokens >= limits.max_total_tokens:
                        limit_reason = "token budget reached"
                        break
                    t0 = time.monotonic()
                    turn = await model.create(SYSTEM_PROMPT, tool_definitions(), messages)
                    run.input_tokens += turn.input_tokens
                    run.output_tokens += turn.output_tokens
                    run.cost_usd += (
                        Decimal(turn.input_tokens) * limits.input_price_per_mtok
                        + Decimal(turn.output_tokens) * limits.output_price_per_mtok
                    ) / MILLION
                    if turn.model:
                        run.model = turn.model
                    trace.add(
                        "model",
                        _elapsed_ms(t0),
                        output={
                            "text": turn.text,
                            "stop_reason": turn.stop_reason,
                            "tool_calls": [t["name"] for t in turn.tool_uses],
                            "input_tokens": turn.input_tokens,
                            "output_tokens": turn.output_tokens,
                        },
                    )
                    await session.commit()

                    if turn.stop_reason == "refusal":
                        limit_reason = "model declined the request"
                        break
                    if turn.stop_reason == "max_tokens":
                        limit_reason = "model output limit reached"
                        break

                    messages.append({"role": "assistant", "content": turn.content})
                    tool_uses = turn.tool_uses
                    if not tool_uses:
                        if _is_complete(await _proposal_types(session, run.id)):
                            break
                        if corrected:
                            error = "agent finished without the required proposals"
                            break
                        corrected = True
                        trace.add("guardrail", 0, output={"note": CORRECTION})
                        messages.append({"role": "user", "content": CORRECTION})
                        continue

                    results: list[dict[str, Any]] = []
                    for call in tool_uses:
                        t1 = time.monotonic()
                        output, is_error = await execute_tool(ctx, call["name"], call.get("input"))
                        trace.add(
                            "tool",
                            _elapsed_ms(t1),
                            tool_name=call["name"],
                            input=call.get("input"),
                            output=json.loads(json.dumps(output, default=str)),
                            is_error=is_error,
                        )
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": call["id"],
                                "content": json.dumps(output, default=str),
                                "is_error": is_error,
                            }
                        )
                    await session.commit()
                    messages.append({"role": "user", "content": results})
                else:
                    limit_reason = f"turn limit of {limits.max_turns} reached"
        except TimeoutError:
            limit_reason = f"time limit of {limits.timeout_seconds:.0f}s reached"
        except (anthropic.APIError, ModelAPIError) as exc:
            log.exception("model call failed for run %s", run_id)
            error = f"model API error: {type(exc).__name__}"
        except Exception as exc:
            log.exception("agent run %s crashed", run_id)
            await session.rollback()
            error = f"internal error: {type(exc).__name__}"
            reloaded = await session.get(AgentRun, run_id, options=[selectinload(AgentRun.ticket)])
            if reloaded is None:
                return
            run, ticket = reloaded, reloaded.ticket
            trace.run = run

        types = await _proposal_types(session, run.id)
        if error is None and limit_reason is not None and ActionType.escalate not in types:
            trace.add("guardrail", 0, output={"note": f"Automatic escalation: {limit_reason}"})
            session.add(
                ProposedAction(
                    run_id=run.id,
                    type=ActionType.escalate,
                    payload={"reason": f"Automatic escalation: {limit_reason}"},
                )
            )
            types.add(ActionType.escalate)

        if error is not None:
            run.status = RunStatus.failed
            run.error = error
            ticket.status = TicketStatus.failed
        elif ActionType.escalate in types:
            run.status = RunStatus.escalated
            ticket.status = TicketStatus.escalated
        else:
            run.status = RunStatus.awaiting_review
            ticket.status = TicketStatus.awaiting_review
        run.duration_ms = _elapsed_ms(started)
        await session.commit()
