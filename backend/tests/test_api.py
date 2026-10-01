from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Customer, Order
from tests.conftest import ScriptedModel, final_turn, tool_call, tool_turn

NEW_TICKET = {
    "customer_email": "maya@brightcafe.example",
    "subject": "Refund please",
    "body": "Please refund this month's charge, we will not use it.",
}


async def _maya_latest_order(factory: async_sessionmaker[AsyncSession]) -> Order:
    async with factory() as s:
        order = await s.scalar(
            select(Order)
            .join(Customer)
            .where(Customer.email == "maya@brightcafe.example")
            .order_by(Order.charged_at.desc())
        )
        assert order is not None
        return order


async def _create_and_finish(client: AsyncClient, app: FastAPI) -> dict[str, Any]:
    created = await client.post("/tickets", json=NEW_TICKET)
    assert created.status_code == 201, created.text
    await app.state.scheduler.wait_idle()
    response = await client.get(f"/tickets/{created.json()['id']}")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _script_refund(model: ScriptedModel, order_id: int) -> None:
    model.turns.extend(
        [
            tool_turn(
                tool_call("propose_refund", order_id=order_id, amount=192, reason="within 14 days")
            ),
            tool_turn(
                tool_call("propose_triage", category="billing", priority="normal", reason="refund"),
                tool_call(
                    "propose_reply",
                    body="We have refunded this month's charge per our 14 day policy.",
                    cited_article_ids=[1],
                ),
            ),
            final_turn(),
        ]
    )


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.json() == {"status": "ok"}


async def test_inbox_lists_seeded_tickets(client: AsyncClient) -> None:
    response = await client.get("/tickets")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 12
    assert items[0]["subject"] == "Wrong shift times for my team"
    assert items[0]["latest_run"] is None


async def test_create_runs_agent_and_approving_refund_moves_money(
    client: AsyncClient,
    app: FastAPI,
    model: ScriptedModel,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    order = await _maya_latest_order(factory)
    _script_refund(model, order.id)
    ticket = await _create_and_finish(client, app)

    run = ticket["latest_run"]
    assert ticket["status"] == "awaiting_review"
    assert run["status"] == "awaiting_review"
    refund = next(a for a in run["actions"] if a["type"] == "refund")

    approved = await client.post(f"/actions/{refund['id']}/approve", json={})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    order = await _maya_latest_order(factory)
    assert order.refunded_amount == Decimal("192.00")
    assert order.status == "refunded"

    again = await client.post(f"/actions/{refund['id']}/approve", json={})
    assert again.status_code == 409


async def test_edited_payload_is_validated_and_marked(
    client: AsyncClient,
    app: FastAPI,
    model: ScriptedModel,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    order = await _maya_latest_order(factory)
    _script_refund(model, order.id)
    ticket = await _create_and_finish(client, app)
    triage = next(a for a in ticket["latest_run"]["actions"] if a["type"] == "triage")

    bad = await client.post(
        f"/actions/{triage['id']}/approve",
        json={"payload": {"category": "nonsense", "priority": "low", "reason": "x"}},
    )
    assert bad.status_code == 422

    good = await client.post(
        f"/actions/{triage['id']}/approve",
        json={"payload": {"category": "billing", "priority": "high", "reason": "VIP customer"}},
    )
    assert good.status_code == 200
    assert good.json()["edited"] is True
    detail = (await client.get(f"/tickets/{ticket['id']}")).json()
    assert detail["priority"] == "high"


async def test_run_is_done_when_every_proposal_is_decided(
    client: AsyncClient,
    app: FastAPI,
    model: ScriptedModel,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    order = await _maya_latest_order(factory)
    _script_refund(model, order.id)
    ticket = await _create_and_finish(client, app)
    actions = ticket["latest_run"]["actions"]
    for action in actions:
        response = await client.post(f"/actions/{action['id']}/reject", json={"note": "no"})
        assert response.status_code == 200
    run = (await client.get(f"/runs/{ticket['latest_run']['id']}")).json()
    assert run["status"] == "done"
    order = await _maya_latest_order(factory)
    assert order.refunded_amount == Decimal(0)


async def test_rerun_while_running_is_rejected_and_rate_limit_applies(
    client: AsyncClient, app: FastAPI, model: ScriptedModel
) -> None:
    model.turns.extend([final_turn(), final_turn()] * 3)
    ticket = await _create_and_finish(client, app)
    await client.post(f"/tickets/{ticket['id']}/rerun")
    await app.state.scheduler.wait_idle()
    await client.post(f"/tickets/{ticket['id']}/rerun")
    await app.state.scheduler.wait_idle()
    limited = await client.post(f"/tickets/{ticket['id']}/rerun")
    assert limited.status_code == 429


async def test_stream_replays_steps_and_finishes(
    client: AsyncClient,
    app: FastAPI,
    model: ScriptedModel,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    order = await _maya_latest_order(factory)
    _script_refund(model, order.id)
    ticket = await _create_and_finish(client, app)
    response = await client.get(f"/runs/{ticket['latest_run']['id']}/stream")
    events = [
        line.split(": ", 1)[1] for line in response.text.splitlines() if line.startswith("event:")
    ]
    assert events.count("step") == len(ticket["latest_run"]["steps"])
    assert events[-1] == "done"


async def test_invalid_ticket_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/tickets", json={**NEW_TICKET, "customer_email": "not-an-email"})
    assert response.status_code == 422
