import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        # a booking can have many failed attempts but only one successful payment
        Index(
            "uq_payments_one_success_per_booking",
            "booking_id",
            unique=True,
            postgresql_where=text("status = 'SUCCESS'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False, length=20, create_constraint=True),
        default=PaymentStatus.PENDING,
        server_default=PaymentStatus.PENDING.value,
    )
    # id the (mock) provider gives us, used to match webhooks to payments
    provider_payment_id: Mapped[str] = mapped_column(String(64), unique=True)
    # optional key sent by the client so a retried request doesn't pay twice
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)

    booking: Mapped["Booking"] = relationship(back_populates="payments")  # noqa: F821


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique so the same event can never be processed twice
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    provider_payment_id: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # what we did with it: processed / ignored, with a short reason
    result: Mapped[str | None] = mapped_column(String(255))
