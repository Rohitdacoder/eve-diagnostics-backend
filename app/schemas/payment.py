from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import BookingStatus, PaymentStatus


class PaymentCreate(BaseModel):
    booking_id: int
    # what the mock provider should do. "pending" leaves it for the webhook to settle
    simulate: Literal["success", "failure", "pending"] = "success"


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    amount: Decimal
    status: PaymentStatus
    provider_payment_id: str
    booking_status: BookingStatus
    created_at: datetime

    @classmethod
    def from_model(cls, p) -> "PaymentOut":
        return cls(
            id=p.id,
            booking_id=p.booking_id,
            amount=p.amount,
            status=p.status,
            provider_payment_id=p.provider_payment_id,
            booking_status=p.booking.status,
            created_at=p.created_at,
        )


class WebhookPayload(BaseModel):
    event_id: str = Field(min_length=1, max_length=64)
    provider_payment_id: str = Field(min_length=1, max_length=64)
    status: Literal["SUCCESS", "FAILED"]


class WebhookResponse(BaseModel):
    result: str
    payment_status: PaymentStatus | None = None
    booking_status: BookingStatus | None = None
