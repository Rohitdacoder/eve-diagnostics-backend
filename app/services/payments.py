import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.models import Booking, BookingStatus, Payment, PaymentStatus, User, WebhookEvent
from app.services.bookings import change_status, get_booking

log = logging.getLogger(__name__)

PAYABLE = {BookingStatus.PENDING, BookingStatus.FAILED}


def has_pending_payment(db: Session, booking_id: int) -> bool:
    return db.scalar(
        select(Payment.id).where(
            Payment.booking_id == booking_id, Payment.status == PaymentStatus.PENDING
        )
    ) is not None


def _apply_result(payment: Payment, booking: Booking, result: PaymentStatus) -> None:
    """Moves a PENDING payment to SUCCESS/FAILED and updates the booking to match."""
    payment.status = result
    if result == PaymentStatus.SUCCESS:
        change_status(booking, BookingStatus.CONFIRMED)
    elif booking.status == BookingStatus.PENDING:
        change_status(booking, BookingStatus.FAILED)
    # booking already FAILED from an earlier attempt: stays FAILED


def create_payment(
    db: Session,
    user: User,
    booking_id: int,
    simulate: str,
    idempotency_key: str | None = None,
) -> tuple[Payment, bool]:
    """Returns (payment, created). created is False when an idempotency key was reused."""
    # lock the booking first; every payment/cancel path locks booking before payment
    booking = get_booking(db, user, booking_id, for_update=True)

    if idempotency_key:
        existing = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.booking_id != booking.id:
                raise ConflictError("Idempotency-Key was already used for a different booking")
            db.rollback()
            return existing, False

    if booking.status == BookingStatus.CONFIRMED:
        raise ConflictError("Booking is already paid")
    if booking.status not in PAYABLE:
        raise ConflictError(f"Cannot pay for a {booking.status.value} booking")
    if booking.appointment_at <= datetime.now(timezone.utc):
        raise BadRequestError("Appointment time has already passed")
    if has_pending_payment(db, booking.id):
        raise ConflictError("A payment for this booking is already in progress")

    payment = Payment(
        booking_id=booking.id,
        amount=booking.amount,
        status=PaymentStatus.PENDING,
        provider_payment_id=f"pay_{uuid.uuid4().hex}",
        idempotency_key=idempotency_key,
    )
    db.add(payment)

    # mock provider: decide the outcome right away unless asked to leave it pending
    if simulate == "success":
        _apply_result(payment, booking, PaymentStatus.SUCCESS)
    elif simulate == "failure":
        _apply_result(payment, booking, PaymentStatus.FAILED)

    db.commit()
    log.info("payment %s for booking %s -> %s", payment.provider_payment_id, booking.id, payment.status.value)
    return payment, True


# ---------- webhook ----------

def sign(body: bytes) -> str:
    return hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign(body), signature)


def handle_webhook(
    db: Session, event_id: str, provider_payment_id: str, status: PaymentStatus, payload: dict
) -> dict:
    # 1. record the event. if event_id is already there this inserts nothing,
    #    and a second copy arriving at the same moment waits on the unique index
    inserted = db.scalar(
        insert(WebhookEvent)
        .values(event_id=event_id, provider_payment_id=provider_payment_id, payload=payload)
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(WebhookEvent.id)
    )
    if inserted is None:
        db.rollback()
        log.info("webhook %s already received, skipping", event_id)
        return {"result": "duplicate"}

    payment = db.scalar(select(Payment).where(Payment.provider_payment_id == provider_payment_id))
    if payment is None:
        # rolled back so the event isn't marked as seen; provider can retry later
        db.rollback()
        raise NotFoundError("Unknown payment")

    # 2. lock booking then payment (same order as create_payment, avoids deadlocks)
    booking = db.scalar(select(Booking).where(Booking.id == payment.booking_id).with_for_update())
    db.refresh(payment, with_for_update=True)

    event = db.get(WebhookEvent, inserted)
    event.processed_at = datetime.now(timezone.utc)

    # 3. only a PENDING payment can change. a different event with the same final
    #    status is harmless, a conflicting one (SUCCESS after FAILED etc.) is ignored
    if payment.status != PaymentStatus.PENDING:
        if payment.status == status:
            event.result = "ignored: payment already " + status.value
        else:
            event.result = f"ignored: payment is {payment.status.value}, got {status.value}"
            log.warning("webhook %s conflicts with payment %s: %s", event_id, payment.id, event.result)
        db.commit()
        return {"result": event.result, "payment_status": payment.status, "booking_status": booking.status}

    _apply_result(payment, booking, status)
    event.result = "processed"
    db.commit()
    log.info("webhook %s: payment %s -> %s, booking %s -> %s",
             event_id, payment.id, payment.status.value, booking.id, booking.status.value)
    return {"result": "processed", "payment_status": payment.status, "booking_status": booking.status}
