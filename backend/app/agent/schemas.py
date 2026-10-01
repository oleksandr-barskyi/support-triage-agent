from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Category = Literal["billing", "bug", "account", "how_to", "cancellation", "other"]
Priority = Literal["low", "normal", "high", "urgent"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchInput(Strict):
    query: str = Field(min_length=2, max_length=300)


class EmailInput(Strict):
    email: EmailStr


class CustomerIdInput(Strict):
    customer_id: int = Field(gt=0)


class TriageInput(Strict):
    category: Category
    priority: Priority
    reason: str = Field(min_length=3, max_length=600)


class ReplyInput(Strict):
    body: str = Field(min_length=20, max_length=4000)
    cited_article_ids: list[int] = Field(max_length=5)


class RefundInput(Strict):
    order_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=3, max_length=600)


class EscalateInput(Strict):
    reason: str = Field(min_length=3, max_length=600)
