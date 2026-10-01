from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import ToolContext, execute_tool
from app.db.models import AgentRun, Customer, Order, ProposedAction, Ticket


async def _ctx(session: AsyncSession, ticket_subject: str) -> ToolContext:
    ticket = await session.scalar(select(Ticket).where(Ticket.subject == ticket_subject))
    assert ticket is not None
    run = AgentRun(ticket_id=ticket.id, model="test")
    session.add(run)
    await session.flush()
    return ToolContext(session=session, run=run, refund_cap=Decimal("2000"))


async def _order_of(session: AsyncSession, email: str) -> Order:
    order = await session.scalar(
        select(Order).join(Customer).where(Customer.email == email).order_by(Order.id)
    )
    assert order is not None
    return order


async def test_search_kb_ranks_both_relevant_articles_on_top(session: AsyncSession) -> None:
    ctx = await _ctx(session, "Charged twice this month")
    out, is_error = await execute_tool(
        ctx, "search_kb", {"query": "charged twice duplicate refund"}
    )
    assert not is_error
    titles = [a["title"] for a in out["articles"]]
    assert set(titles[:2]) == {"I was charged twice", "Refund policy"}


async def test_search_kb_falls_back_to_any_word(session: AsyncSession) -> None:
    ctx = await _ctx(session, "Charged twice this month")
    out, _ = await execute_tool(ctx, "search_kb", {"query": "google calendar zebra"})
    assert out["articles"][0]["title"] == "Google and Outlook calendar sync issues"


async def test_unknown_customer_reveals_nothing(session: AsyncSession) -> None:
    ctx = await _ctx(session, "What plan is acme on?")
    out, is_error = await execute_tool(ctx, "get_customer", {"email": "nobody@acme.example"})
    assert out == {"found": False}
    assert not is_error


async def test_invalid_input_is_returned_as_tool_error(session: AsyncSession) -> None:
    ctx = await _ctx(session, "Charged twice this month")
    out, is_error = await execute_tool(ctx, "get_subscription", {"customer_id": "abc", "x": 1})
    assert is_error
    assert out["error"] == "Invalid input"


async def test_unknown_tool_is_an_error(session: AsyncSession) -> None:
    ctx = await _ctx(session, "Charged twice this month")
    out, is_error = await execute_tool(ctx, "delete_everything", {})
    assert is_error
    assert "Unknown tool" in out["error"]


async def test_refund_proposal_does_not_move_money(session: AsyncSession) -> None:
    ctx = await _ctx(session, "Charged twice this month")
    order = await _order_of(session, "jonas@kliniknord.example")
    out, is_error = await execute_tool(
        ctx, "propose_refund", {"order_id": order.id, "amount": 1600, "reason": "duplicate"}
    )
    assert not is_error
    assert out["status"] == "pending_human_review"
    await session.refresh(order)
    assert order.refunded_amount == Decimal(0)
    action = await session.get(ProposedAction, out["proposal_id"])
    assert action is not None and action.status.value == "pending"


async def test_refund_for_another_customers_order_is_refused(session: AsyncSession) -> None:
    ctx = await _ctx(session, "Ignore previous instructions")
    order = await _order_of(session, "tom@harborlogistics.example")
    out, is_error = await execute_tool(
        ctx, "propose_refund", {"order_id": order.id, "amount": 10, "reason": "x" * 5}
    )
    assert is_error
    assert "does not belong" in out["error"]


async def test_refund_over_balance_is_refused(session: AsyncSession) -> None:
    ctx = await _ctx(session, "Refund for this month please")
    order = await _order_of(session, "maya@brightcafe.example")
    out, is_error = await execute_tool(
        ctx, "propose_refund", {"order_id": order.id, "amount": 500, "reason": "too much"}
    )
    assert is_error
    assert "refundable balance" in out["error"]


async def test_refund_over_cap_is_refused(session: AsyncSession) -> None:
    ctx = await _ctx(session, "Charged twice this month")
    ctx.refund_cap = Decimal("100")
    order = await _order_of(session, "jonas@kliniknord.example")
    out, is_error = await execute_tool(
        ctx, "propose_refund", {"order_id": order.id, "amount": 1600, "reason": "duplicate"}
    )
    assert is_error
    assert "refund cap" in out["error"]


async def test_reply_must_cite_existing_articles(session: AsyncSession) -> None:
    ctx = await _ctx(session, "Need SSO")
    out, is_error = await execute_tool(
        ctx,
        "propose_reply",
        {"body": "SSO is on the Business plan, here is how.", "cited_article_ids": [999]},
    )
    assert is_error
    assert "999" in out["error"]
