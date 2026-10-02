from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.db.models import ActionStatus, ActionType, RunStatus, TicketStatus


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TicketCreate(BaseModel):
    customer_email: EmailStr
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=10, max_length=4000)


class StepOut(ORM):
    index: int
    kind: str
    tool_name: str | None
    input: Any
    output: Any
    is_error: bool
    duration_ms: int


class ActionOut(ORM):
    id: int
    type: ActionType
    payload: dict[str, Any]
    status: ActionStatus
    edited: bool
    decision_note: str | None
    decided_at: datetime | None


class RunSummary(ORM):
    id: int
    status: RunStatus
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    duration_ms: int
    error: str | None
    created_at: datetime


class RunOut(RunSummary):
    ticket_id: int
    steps: list[StepOut]
    actions: list[ActionOut]


class TicketBase(ORM):
    id: int
    customer_email: str
    subject: str
    status: TicketStatus
    category: str | None
    priority: str | None
    created_at: datetime


class TicketListItem(TicketBase):
    latest_run: RunSummary | None = None


class AttachmentOut(ORM):
    id: int
    filename: str
    size_bytes: int
    pages: int
    fields: dict[str, Any] | None
    extraction_model: str | None
    created_at: datetime


class TicketOut(TicketBase):
    body: str
    attachments: list[AttachmentOut] = []
    latest_run: RunOut | None = None


class ApproveIn(BaseModel):
    payload: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=1000)


class RejectIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
