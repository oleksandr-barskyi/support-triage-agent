import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer, KbArticle, Order, Subscription, Ticket
from app.seed_data import CUSTOMERS, KB_ARTICLES, PLAN_PRICES, TICKETS


async def seed(session: AsyncSession, now: datetime | None = None) -> bool:
    if await session.scalar(select(func.count(KbArticle.id))):
        return False
    now = now or datetime.now(UTC)
    for slug, title, body in KB_ARTICLES:
        session.add(KbArticle(slug=slug, title=title, body=body))
    for c in CUSTOMERS:
        customer = Customer(
            name=c.name,
            email=c.email,
            company=c.company,
            created_at=now - timedelta(days=400),
        )
        customer.subscription = Subscription(
            plan=c.plan,
            status=c.status,
            seats=c.seats,
            monthly_price=Decimal(PLAN_PRICES[c.plan]) * c.seats,
            renews_on=date.fromordinal(now.date().toordinal() + 30),
        )
        customer.orders = [
            Order(
                description=f"Lumora {c.plan} plan, {c.seats} seats, monthly",
                amount=Decimal(amount),
                refunded_amount=Decimal(0),
                status="paid",
                charged_at=now - timedelta(days=days_ago, hours=3),
            )
            for days_ago, amount in c.orders
        ]
        session.add(customer)
    for offset, (email, subject, body) in enumerate(TICKETS):
        session.add(
            Ticket(
                customer_email=email,
                subject=subject,
                body=body,
                created_at=now - timedelta(minutes=10 * (len(TICKETS) - offset)),
            )
        )
    await session.commit()
    return True


async def main() -> None:
    from app.db.session import SessionFactory, engine

    async with SessionFactory() as session:
        created = await seed(session)
    await engine.dispose()
    print("seeded" if created else "already seeded")


if __name__ == "__main__":
    asyncio.run(main())
