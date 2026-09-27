from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.models import Booking, BookingStatus, CentreTest, User

MAX_DAYS_AHEAD = 90

# which status a booking can move to from its current status
ALLOWED_TRANSITIONS = {
    BookingStatus.PENDING: {BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED},
    # payment failed, user can retry payment or give up
    BookingStatus.FAILED: {BookingStatus.CONFIRMED, BookingStatus.CANCELLED},
    # cancelling a paid booking would need a refund flow, not supported
    BookingStatus.CONFIRMED: set(),
    BookingStatus.CANCELLED: set(),
}


def can_transition(current: BookingStatus, new: BookingStatus) -> bool:
    return new in ALLOWED_TRANSITIONS[current]


def change_status(booking: Booking, new: BookingStatus) -> None:
    if not can_transition(booking.status, new):
        raise ConflictError(f"Cannot change booking from {booking.status.value} to {new.value}")
    booking.status = new


def _load_options():
    return (joinedload(Booking.centre), joinedload(Booking.test))


def create_booking(
    db: Session, user: User, centre_id: int, test_id: int, appointment_at: datetime
) -> Booking:
    now = datetime.now(UTC)
    if appointment_at <= now:
        raise BadRequestError("Appointment time must be in the future")
    if appointment_at > now + timedelta(days=MAX_DAYS_AHEAD):
        raise BadRequestError(f"Appointments can be booked at most {MAX_DAYS_AHEAD} days ahead")

    offering = db.get(CentreTest, (centre_id, test_id))
    if offering is None or not offering.is_active:
        raise NotFoundError("This test is not available at this centre")

    booking = Booking(
        user_id=user.id,
        centre_id=centre_id,
        test_id=test_id,
        appointment_at=appointment_at,
        amount=offering.price,
        status=BookingStatus.PENDING,
    )
    db.add(booking)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError("You already have a booking for this test at this time") from None
    return get_booking(db, user, booking.id)


def get_booking(db: Session, user: User, booking_id: int, for_update: bool = False) -> Booking:
    query = select(Booking).where(Booking.id == booking_id)
    if for_update:
        # lock the row so a payment and a cancel can't change it at the same time
        query = query.with_for_update()
    else:
        # populate_existing: always return what is in the db, not a cached object
        query = query.options(*_load_options()).execution_options(populate_existing=True)

    booking = db.scalar(query)
    # someone else's booking looks the same as a missing one, so ids can't be probed
    if booking is None or (booking.user_id != user.id and not user.is_admin):
        raise NotFoundError("Booking not found")
    return booking


def list_bookings(
    db: Session, user: User, status: BookingStatus | None, limit: int, offset: int
) -> tuple[list[Booking], int]:
    query = select(Booking)
    if not user.is_admin:
        query = query.where(Booking.user_id == user.id)
    if status is not None:
        query = query.where(Booking.status == status)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(
        query.options(*_load_options())
        .order_by(Booking.created_at.desc(), Booking.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(items), total


def cancel_booking(db: Session, user: User, booking_id: int) -> Booking:
    booking = get_booking(db, user, booking_id, for_update=True)
    # imported here to avoid a circular import (payments uses this module)
    from app.services.payments import has_pending_payment

    if has_pending_payment(db, booking.id):
        raise ConflictError("A payment for this booking is in progress, try again shortly")
    change_status(booking, BookingStatus.CANCELLED)
    db.commit()
    return get_booking(db, user, booking_id)


def expire_unpaid_bookings(db: Session) -> int:
    """Cancels PENDING/FAILED bookings whose appointment time has passed without payment.
    Bookings with a payment still in progress are left alone."""
    from app.models import Payment, PaymentStatus

    in_progress = select(Payment.id).where(
        Payment.booking_id == Booking.id, Payment.status == PaymentStatus.PENDING
    )
    stale = db.scalars(
        select(Booking)
        .where(
            Booking.status.in_([BookingStatus.PENDING, BookingStatus.FAILED]),
            Booking.appointment_at < func.now(),
            ~in_progress.exists(),
        )
        .with_for_update(skip_locked=True)
    ).all()
    for booking in stale:
        change_status(booking, BookingStatus.CANCELLED)
    db.commit()
    return len(stale)
