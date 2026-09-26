from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel

from app.models import BookingStatus


class BookingCreate(BaseModel):
    centre_id: int
    test_id: int
    # must include a timezone, e.g. 2026-10-01T10:30:00+05:30
    appointment_at: AwareDatetime


class BookingOut(BaseModel):
    id: int
    centre_id: int
    centre_name: str
    test_id: int
    test_name: str
    appointment_at: datetime
    amount: Decimal
    status: BookingStatus
    created_at: datetime

    @classmethod
    def from_model(cls, b) -> "BookingOut":
        return cls(
            id=b.id,
            centre_id=b.centre_id,
            centre_name=b.centre.name,
            test_id=b.test_id,
            test_name=b.test.name,
            appointment_at=b.appointment_at,
            amount=b.amount,
            status=b.status,
            created_at=b.created_at,
        )
