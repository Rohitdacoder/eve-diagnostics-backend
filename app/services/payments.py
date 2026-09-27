import hashlib
import hmac
import logging
import uuid
from datetime import UTC, datetime, timedelta

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
    return (
        db.scalar(
            select(Payment.id).where(
                Payment.booking_id == booking_id, Payment.status == PaymentStatus.PENDING
            )
        )
        is not None
    )


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
    if booking.appointment_at <= datetime.now(UTC):
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


MAX_ATTEMPTS = 5
RETRY_BASE_SECONDS = 30


def _retry_delay(attempts: int) -> timedelta:
    # 30s, 60s, 2m, 4m, ...
    return timedelta(seconds=RETRY_BASE_SECONDS * 2 ** (attempts - 1))


def record_event(
    db: Session, event_id: str, provider_payment_id: str, status: PaymentStatus, payload: dict
) -> int | None:
    """Saves the event and commits. Returns its id, or None if this event_id was seen before."""
    inserted = db.scalar(
        insert(WebhookEvent)
        .values(
            event_id=event_id,
            provider_payment_id=provider_payment_id,
            status=status.value,
            payload=payload,
            # if the inline processing below crashes, the retry job picks it up after this
            next_retry_at=datetime.now(UTC) + _retry_delay(1),
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(WebhookEvent.id)
    )
    db.commit()
    return inserted


def process_event(db: Session, event_pk: int) -> dict:
    """Applies a saved event to its payment and booking. Safe to call more than once."""
    # lock the event so the request and the retry job can't process it at the same time
    event = db.scalar(select(WebhookEvent).where(WebhookEvent.id == event_pk).with_for_update())
    if event.processed_at is not None:
        db.rollback()
        return {"result": event.result}

    payment = db.scalar(select(Payment).where(Payment.provider_payment_id == event.provider_payment_id))
    # lock booking then payment (same order as create_payment, avoids deadlocks)
    booking = db.scalar(select(Booking).where(Booking.id == payment.booking_id).with_for_update())
    db.refresh(payment, with_for_update=True)
    status = PaymentStatus(event.status)

    # only a PENDING payment can change. a different event with the same final
    # status is harmless, a conflicting one (SUCCESS after FAILED etc.) is ignored
    if payment.status != PaymentStatus.PENDING:
        if payment.status == status:
            event.result = "ignored: payment already " + status.value
        else:
            event.result = f"ignored: payment is {payment.status.value}, got {status.value}"
            log.warning("webhook %s conflicts with payment %s: %s", event.event_id, payment.id, event.result)
    else:
        _apply_result(payment, booking, status)
        event.result = "processed"
        log.info(
            "webhook %s: payment %s -> %s, booking %s -> %s",
            event.event_id,
            payment.id,
            payment.status.value,
            booking.id,
            booking.status.value,
        )

    event.processed_at = datetime.now(UTC)
    event.next_retry_at = None
    db.commit()
    return {"result": event.result, "payment_status": payment.status, "booking_status": booking.status}


def _mark_failed_attempt(db: Session, event_pk: int, error: Exception) -> None:
    db.rollback()
    event = db.get(WebhookEvent, event_pk, with_for_update=True)
    event.attempts += 1
    event.last_error = f"{type(error).__name__}: {error}"[:1000]
    if event.attempts >= MAX_ATTEMPTS:
        # give up; kept in the table with the error so someone can look at it
        event.processed_at = datetime.now(UTC)
        event.next_retry_at = None
        event.result = f"failed after {event.attempts} attempts"
        log.error("webhook %s gave up: %s", event.event_id, event.last_error)
    else:
        event.next_retry_at = datetime.now(UTC) + _retry_delay(event.attempts)
        log.warning(
            "webhook %s attempt %s failed, retry at %s: %s",
            event.event_id,
            event.attempts,
            event.next_retry_at,
            event.last_error,
        )
    db.commit()


def handle_webhook(
    db: Session, event_id: str, provider_payment_id: str, status: PaymentStatus, payload: dict
) -> dict:
    # unknown payment: don't save the event, so the provider's own retry still works
    # (e.g. webhook arrived before our payment row was committed)
    known = db.scalar(select(Payment.id).where(Payment.provider_payment_id == provider_payment_id))
    if known is None:
        raise NotFoundError("Unknown payment")

    event_pk = record_event(db, event_id, provider_payment_id, status, payload)
    if event_pk is None:
        log.info("webhook %s already received, skipping", event_id)
        return {"result": "duplicate"}

    try:
        return process_event(db, event_pk)
    except Exception as e:
        # event is saved, the retry job will pick it up
        _mark_failed_attempt(db, event_pk, e)
        return {"result": "queued for retry"}


def retry_pending_events(db: Session, batch_size: int = 50) -> int:
    """Called by the background job. Returns how many events were retried."""
    now = datetime.now(UTC)
    # SKIP LOCKED: events another worker (or a request) is busy with are skipped, not waited for
    ids = db.scalars(
        select(WebhookEvent.id)
        .where(WebhookEvent.processed_at.is_(None), WebhookEvent.next_retry_at <= now)
        .order_by(WebhookEvent.next_retry_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    ).all()
    db.rollback()

    for event_pk in ids:
        try:
            process_event(db, event_pk)
        except Exception as e:
            _mark_failed_attempt(db, event_pk, e)
    return len(ids)
