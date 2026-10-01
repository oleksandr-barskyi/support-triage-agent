from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class TicketStatus(enum.StrEnum):
    new = "new"
    triaging = "triaging"
    awaiting_review = "awaiting_review"
    escalated = "escalated"
    answered = "answered"
    failed = "failed"


class RunStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    awaiting_review = "awaiting_review"
    escalated = "escalated"
    failed = "failed"
    done = "done"


class ActionType(enum.StrEnum):
    triage = "triage"
    reply = "reply"
    refund = "refund"
    escalate = "escalate"


class ActionStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


def _enum(e: type[enum.Enum]) -> Enum:
    return Enum(e, native_enum=False, length=32, values_callable=lambda x: [m.value for m in x])


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    company: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    subscription: Mapped[Subscription | None] = relationship(back_populates="customer")
    orders: Mapped[list[Order]] = relationship(back_populates="customer", order_by="Order.id")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), unique=True)
    plan: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30))
    seats: Mapped[int] = mapped_column(Integer)
    monthly_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    renews_on: Mapped[date] = mapped_column(Date)

    customer: Mapped[Customer] = relationship(back_populates="subscription")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    description: Mapped[str] = mapped_column(String(300))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(30))
    charged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="orders")


class KbArticle(Base):
    __tablename__ = "kb_articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', title), 'A') || "
            "setweight(to_tsvector('english', body), 'B')",
            persisted=True,
        ),
    )

    __table_args__ = (Index("ix_kb_articles_search", "search_vector", postgresql_using="gin"),)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_email: Mapped[str] = mapped_column(String(320), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(_enum(TicketStatus), default=TicketStatus.new)
    category: Mapped[str | None] = mapped_column(String(50))
    priority: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', subject || ' ' || body)", persisted=True),
    )

    runs: Mapped[list[AgentRun]] = relationship(
        back_populates="ticket", order_by="AgentRun.id", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_tickets_search", "search_vector", postgresql_using="gin"),)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus), default=RunStatus.queued)
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 5), default=Decimal("0"))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped[Ticket] = relationship(back_populates="runs")
    steps: Mapped[list[AgentStep]] = relationship(
        back_populates="run", order_by="AgentStep.index", cascade="all, delete-orphan"
    )
    actions: Mapped[list[ProposedAction]] = relationship(
        back_populates="run", order_by="ProposedAction.id", cascade="all, delete-orphan"
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    tool_name: Mapped[str | None] = mapped_column(String(80))
    input: Mapped[Any] = mapped_column(JsonType, nullable=True)
    output: Mapped[Any] = mapped_column(JsonType, nullable=True)
    is_error: Mapped[bool] = mapped_column(default=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[AgentRun] = relationship(back_populates="steps")


class ProposedAction(Base):
    __tablename__ = "proposed_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    type: Mapped[ActionType] = mapped_column(_enum(ActionType))
    payload: Mapped[Any] = mapped_column(JsonType)
    status: Mapped[ActionStatus] = mapped_column(_enum(ActionStatus), default=ActionStatus.pending)
    decision_note: Mapped[str | None] = mapped_column(Text)
    edited: Mapped[bool] = mapped_column(default=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[AgentRun] = relationship(back_populates="actions")
