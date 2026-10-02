import io
from decimal import Decimal

import pytest
from httpx import AsyncClient
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import render_ticket
from app.agent.tools import ToolContext, execute_tool
from app.db.models import AgentRun, Attachment, Ticket
from app.documents import DocumentError, extract_fields, extract_pdf_text
from app.seed_data import DOCUMENT_TICKET
from tests.conftest import ScriptedModel, final_turn, tool_call, tool_turn

FIELDS = {
    "document_type": "invoice",
    "issuer": "Lumora Inc.",
    "document_number": "INV-2026-0917",
    "issue_date": "2026-09-22",
    "currency": "USD",
    "total_amount": "1,240.00",
    "line_items": [
        {"description": "Business plan, 30 seats", "amount": "1,200.00"},
        {"description": "Priority support add-on", "amount": "40.00"},
    ],
    "summary": "Monthly invoice for the Business plan with a support add-on.",
}


def pdf_bytes(*lines: str) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    for i, line in enumerate(lines):
        c.drawString(72, 750 - 16 * i, line)
    c.showPage()
    c.save()
    return buffer.getvalue()


def test_extracts_text_and_page_count() -> None:
    text, pages = extract_pdf_text(pdf_bytes("Invoice INV-1", "Total due 99.00"))
    assert pages == 1
    assert "Invoice INV-1" in text
    assert "Total due 99.00" in text


@pytest.mark.parametrize("data", [b"not a pdf at all", pdf_bytes()])
def test_rejects_unreadable_or_empty_pdf(data: bytes) -> None:
    with pytest.raises(DocumentError):
        extract_pdf_text(data)


async def test_structured_extraction_parses_amounts() -> None:
    model = ScriptedModel(turns=[tool_turn(tool_call("record_document", **FIELDS))])
    fields = await extract_fields(model, "Invoice text")
    assert fields.total_amount == Decimal("1240.00")
    assert [i.amount for i in fields.line_items] == [Decimal("1200.00"), Decimal("40.00")]
    assert "<document>" in model.calls[0][0]["content"]


async def test_structured_extraction_retries_after_invalid_output() -> None:
    model = ScriptedModel(
        turns=[
            tool_turn(tool_call("record_document", **{**FIELDS, "document_type": "poem"})),
            tool_turn(tool_call("record_document", **FIELDS)),
        ]
    )
    fields = await extract_fields(model, "Invoice text")
    assert fields.document_number == "INV-2026-0917"
    retry = model.calls[1][-1]["content"][0]
    assert retry["type"] == "tool_result"
    assert retry["is_error"] is True


async def test_structured_extraction_gives_up_without_a_tool_call() -> None:
    model = ScriptedModel(turns=[final_turn("Sure"), final_turn("Still no")])
    with pytest.raises(DocumentError):
        await extract_fields(model, "Invoice text")


async def test_read_attachments_extracts_once_and_caches(session: AsyncSession) -> None:
    ticket = await session.scalar(select(Ticket).where(Ticket.subject == DOCUMENT_TICKET[1]))
    assert ticket is not None
    run = AgentRun(ticket_id=ticket.id, model="scripted")
    session.add(run)
    await session.flush()
    model = ScriptedModel(turns=[tool_turn(tool_call("record_document", **FIELDS))])
    ctx = ToolContext(session=session, run=run, refund_cap=Decimal("2000"), model=model)

    first, is_error = await execute_tool(ctx, "read_attachments", {"include_text": True})
    assert not is_error
    [doc] = first["attachments"]
    assert doc["fields"]["total_amount"] == "1240.00"
    assert "Total due" in doc["text"]

    second, _ = await execute_tool(ctx, "read_attachments", {"include_text": False})
    assert second["attachments"][0]["fields"]["document_number"] == "INV-2026-0917"
    assert "text" not in second["attachments"][0]
    assert len(model.calls) == 1
    stored = await session.scalar(select(Attachment).where(Attachment.ticket_id == ticket.id))
    assert stored is not None
    assert stored.extraction_model == "scripted"


def test_ticket_prompt_lists_attachments() -> None:
    prompt = render_ticket("a@b.co", "Invoice", "See attached", [("inv<1>.pdf", 2)])
    assert '<attachment pages="2">inv&lt;1&gt;.pdf</attachment>' in prompt
    assert "<attachments>" not in render_ticket("a@b.co", "Hi", "No files here")


async def test_upload_attachment_endpoint(client: AsyncClient) -> None:
    pdf = pdf_bytes("Receipt R-77", "Total 12.00")
    response = await client.post(
        "/tickets/1/attachments", files={"file": ("receipt.pdf", pdf, "application/pdf")}
    )
    assert response.status_code == 201
    assert response.json()["pages"] == 1
    assert response.json()["fields"] is None

    ticket = (await client.get("/tickets/1")).json()
    assert [a["filename"] for a in ticket["attachments"]] == ["receipt.pdf"]

    wrong = await client.post(
        "/tickets/1/attachments", files={"file": ("a.txt", b"hello", "text/plain")}
    )
    assert wrong.status_code == 415
    for _ in range(2):
        await client.post(
            "/tickets/1/attachments", files={"file": ("r.pdf", pdf, "application/pdf")}
        )
    full = await client.post(
        "/tickets/1/attachments", files={"file": ("r.pdf", pdf, "application/pdf")}
    )
    assert full.status_code == 409
