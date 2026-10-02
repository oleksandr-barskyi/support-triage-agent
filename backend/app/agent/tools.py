import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import schemas
from app.agent.model import AgentModel, ModelAPIError
from app.db.models import (
    ActionType,
    AgentRun,
    Attachment,
    Customer,
    KbArticle,
    Order,
    ProposedAction,
    Subscription,
    Ticket,
)
from app.documents import DocumentError, extract_fields

PROPOSAL_TOOLS = {"propose_triage", "propose_reply", "propose_refund", "escalate"}


@dataclass
class ToolContext:
    session: AsyncSession
    run: AgentRun
    refund_cap: Decimal
    model: AgentModel | None = None


class ToolError(Exception):
    pass


Handler = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    input_schema: dict[str, Any]
    handler: Handler

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _any_word_query(text: str) -> Any:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", text) if len(w) > 2]
    return func.to_tsquery("english", " | ".join(words)) if words else None


async def search_kb(ctx: ToolContext, data: schemas.SearchInput) -> dict[str, Any]:
    query = _any_word_query(data.query)
    if query is None:
        return {"articles": []}
    rank = func.ts_rank_cd(KbArticle.search_vector, query)
    rows = (
        await ctx.session.execute(
            select(KbArticle, rank.label("rank"))
            .where(KbArticle.search_vector.op("@@")(query))
            .order_by(rank.desc(), KbArticle.id)
            .limit(4)
        )
    ).all()
    return {
        "articles": [
            {"id": a.id, "title": a.title, "body": a.body, "rank": round(float(r), 4)}
            for a, r in rows
        ]
    }


async def get_customer(ctx: ToolContext, data: schemas.EmailInput) -> dict[str, Any]:
    customer = await ctx.session.scalar(
        select(Customer).where(func.lower(Customer.email) == data.email.lower())
    )
    if customer is None:
        return {"found": False}
    return {
        "found": True,
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "email": customer.email,
            "company": customer.company,
            "customer_since": customer.created_at.date().isoformat(),
        },
    }


async def get_subscription(ctx: ToolContext, data: schemas.CustomerIdInput) -> dict[str, Any]:
    sub = await ctx.session.scalar(
        select(Subscription).where(Subscription.customer_id == data.customer_id)
    )
    if sub is None:
        return {"found": False}
    return {
        "found": True,
        "subscription": {
            "plan": sub.plan,
            "status": sub.status,
            "seats": sub.seats,
            "monthly_price": str(sub.monthly_price),
            "renews_on": sub.renews_on.isoformat(),
        },
    }


async def get_order_history(ctx: ToolContext, data: schemas.CustomerIdInput) -> dict[str, Any]:
    orders = (
        await ctx.session.scalars(
            select(Order)
            .where(Order.customer_id == data.customer_id)
            .order_by(Order.charged_at.desc())
            .limit(20)
        )
    ).all()
    return {
        "orders": [
            {
                "id": o.id,
                "description": o.description,
                "amount": str(o.amount),
                "refunded_amount": str(o.refunded_amount),
                "status": o.status,
                "charged_at": o.charged_at.isoformat(),
            }
            for o in orders
        ]
    }


async def find_similar_tickets(ctx: ToolContext, data: schemas.SearchInput) -> dict[str, Any]:
    query = _any_word_query(data.query)
    if query is None:
        return {"tickets": []}
    rank = func.ts_rank_cd(Ticket.search_vector, query)
    rows = (
        await ctx.session.execute(
            select(Ticket, rank.label("rank"))
            .where(Ticket.search_vector.op("@@")(query), Ticket.id != ctx.run.ticket_id)
            .order_by(rank.desc())
            .limit(3)
        )
    ).all()
    return {
        "tickets": [
            {
                "id": t.id,
                "subject": t.subject,
                "status": t.status.value,
                "category": t.category,
            }
            for t, _ in rows
        ]
    }


async def read_attachments(ctx: ToolContext, data: schemas.AttachmentsInput) -> dict[str, Any]:
    attachments = (
        await ctx.session.scalars(
            select(Attachment)
            .where(Attachment.ticket_id == ctx.run.ticket_id)
            .order_by(Attachment.id)
        )
    ).all()
    out = []
    for a in attachments:
        item: dict[str, Any] = {"id": a.id, "filename": a.filename, "pages": a.pages}
        if a.fields is None and ctx.model is not None:
            try:
                fields = await extract_fields(ctx.model, a.text)
                a.fields = fields.model_dump(mode="json")
                a.extraction_model = ctx.model.name
                await ctx.session.flush()
            except (DocumentError, ModelAPIError, anthropic.APIError) as exc:
                item["extraction_error"] = str(exc)[:300]
        item["fields"] = a.fields
        if data.include_text:
            item["text"] = a.text[:3000]
        out.append(item)
    return {"attachments": out}


async def _propose(ctx: ToolContext, kind: ActionType, payload: dict[str, Any]) -> dict[str, Any]:
    action = ProposedAction(run_id=ctx.run.id, type=kind, payload=payload)
    ctx.session.add(action)
    await ctx.session.flush()
    return {"proposal_id": action.id, "status": "pending_human_review"}


async def propose_triage(ctx: ToolContext, data: schemas.TriageInput) -> dict[str, Any]:
    return await _propose(ctx, ActionType.triage, data.model_dump())


async def propose_reply(ctx: ToolContext, data: schemas.ReplyInput) -> dict[str, Any]:
    if data.cited_article_ids:
        found = set(
            (
                await ctx.session.scalars(
                    select(KbArticle.id).where(KbArticle.id.in_(data.cited_article_ids))
                )
            ).all()
        )
        missing = sorted(set(data.cited_article_ids) - found)
        if missing:
            raise ToolError(f"Unknown article ids: {missing}. Cite only ids returned by search_kb.")
    return await _propose(ctx, ActionType.reply, data.model_dump())


async def propose_refund(ctx: ToolContext, data: schemas.RefundInput) -> dict[str, Any]:
    order = await ctx.session.get(Order, data.order_id)
    if order is None:
        raise ToolError(f"Order {data.order_id} does not exist.")
    ticket = await ctx.session.get(Ticket, ctx.run.ticket_id)
    customer = await ctx.session.get(Customer, order.customer_id)
    if (
        ticket is None
        or customer is None
        or customer.email.lower() != ticket.customer_email.lower()
    ):
        raise ToolError("Order does not belong to the customer who opened this ticket.")
    refundable = order.amount - order.refunded_amount
    if data.amount > refundable:
        raise ToolError(f"Amount exceeds refundable balance {refundable}.")
    if data.amount > ctx.refund_cap:
        raise ToolError(f"Amount exceeds the agent refund cap {ctx.refund_cap}. Escalate instead.")
    return await _propose(ctx, ActionType.refund, data.model_dump(mode="json"))


async def escalate(ctx: ToolContext, data: schemas.EscalateInput) -> dict[str, Any]:
    return await _propose(ctx, ActionType.escalate, data.model_dump())


CATEGORIES = ["billing", "bug", "account", "how_to", "cancellation", "other"]
PRIORITIES = ["low", "normal", "high", "urgent"]

TOOLS: list[Tool] = [
    Tool(
        "search_kb",
        "Full-text search over the help center. Returns up to 4 articles with id, title "
        "and body. Use it before writing any reply that states policy or steps.",
        schemas.SearchInput,
        _obj({"query": {"type": "string", "description": "Plain-language search query"}}),
        search_kb,
    ),
    Tool(
        "get_customer",
        "Look up a customer by email. Returns found=false for unknown emails.",
        schemas.EmailInput,
        _obj({"email": {"type": "string"}}),
        get_customer,
    ),
    Tool(
        "get_subscription",
        "Current plan, status, seats, price and renewal date for a customer id.",
        schemas.CustomerIdInput,
        _obj({"customer_id": {"type": "integer"}}),
        get_subscription,
    ),
    Tool(
        "get_order_history",
        "Recent charges for a customer id, newest first, with refunded amounts.",
        schemas.CustomerIdInput,
        _obj({"customer_id": {"type": "integer"}}),
        get_order_history,
    ),
    Tool(
        "read_attachments",
        "Documents attached to this ticket (PDF invoices, receipts, statements) with fields "
        "extracted into a fixed schema: type, issuer, number, date, currency, total, line "
        "items. Call it whenever the ticket lists attachments.",
        schemas.AttachmentsInput,
        _obj({"include_text": {"type": "boolean", "description": "Also return raw text"}}),
        read_attachments,
    ),
    Tool(
        "find_similar_tickets",
        "Find up to 3 earlier tickets with similar wording, to spot known issues.",
        schemas.SearchInput,
        _obj({"query": {"type": "string"}}),
        find_similar_tickets,
    ),
    Tool(
        "propose_triage",
        "Propose category and priority for this ticket. Required once per run. "
        "Creates a proposal for a human; changes nothing by itself.",
        schemas.TriageInput,
        _obj(
            {
                "category": {"type": "string", "enum": CATEGORIES},
                "priority": {"type": "string", "enum": PRIORITIES},
                "reason": {"type": "string"},
            }
        ),
        propose_triage,
    ),
    Tool(
        "propose_reply",
        "Propose the reply to send to the customer. Cite the KB article ids the reply "
        "relies on. A human reviews it before anything is sent.",
        schemas.ReplyInput,
        _obj(
            {
                "body": {"type": "string"},
                "cited_article_ids": {"type": "array", "items": {"type": "integer"}},
            }
        ),
        propose_reply,
    ),
    Tool(
        "propose_refund",
        "Propose a refund for one order of this customer. Validated against the order "
        "balance and the refund cap. A human approves before money moves.",
        schemas.RefundInput,
        _obj(
            {
                "order_id": {"type": "integer"},
                "amount": {"type": "number"},
                "reason": {"type": "string"},
            }
        ),
        propose_refund,
    ),
    Tool(
        "escalate",
        "Hand the ticket to a senior human: legal threats, security, data loss, anything "
        "outside policy or the refund cap, or when you are not confident.",
        schemas.EscalateInput,
        _obj({"reason": {"type": "string"}}),
        escalate,
    ),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def tool_definitions() -> list[dict[str, Any]]:
    return [t.definition() for t in TOOLS]


async def execute_tool(ctx: ToolContext, name: str, raw_input: Any) -> tuple[dict[str, Any], bool]:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return {"error": f"Unknown tool {name}"}, True
    try:
        data = tool.input_model.model_validate(raw_input)
    except ValidationError as exc:
        return {
            "error": "Invalid input",
            "details": exc.errors(include_url=False, include_context=False, include_input=False),
        }, True
    try:
        return await tool.handler(ctx, data), False
    except ToolError as exc:
        return {"error": str(exc)}, True
