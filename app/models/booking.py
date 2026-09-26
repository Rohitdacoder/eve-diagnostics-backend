import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class BookingStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        # centre + test must be a real offering, not just any centre and any test
        ForeignKeyConstraint(
            ["centre_id", "test_id"],
            ["centre_tests.centre_id", "centre_tests.test_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_bookings_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    centre_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_centres.id"))
    test_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_tests.id"))
    appointment_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # copied from centre_tests.price at booking time
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, native_enum=False, length=20, create_constraint=True),
        default=BookingStatus.PENDING,
        server_default=BookingStatus.PENDING.value,
    )

    user: Mapped["User"] = relationship(back_populates="bookings")  # noqa: F821
    centre: Mapped["DiagnosticCentre"] = relationship()  # noqa: F821
    test: Mapped["DiagnosticTest"] = relationship()  # noqa: F821
    payments: Mapped[list["Payment"]] = relationship(back_populates="booking")  # noqa: F821
