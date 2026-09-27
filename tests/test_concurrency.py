"""Requests arriving at the same moment. Each thread has its own db session,
like separate requests would, and a barrier makes them start together."""

import threading
from collections import Counter

from sqlalchemy import func, select

from app.core.errors import AppError
from app.database import SessionLocal
from app.models import Booking, Payment, PaymentStatus, User, WebhookEvent
from app.services import bookings as booking_service
from app.services import payments as payment_service
from tests.conftest import auth

N = 10


def run_together(fn, n=N):
    barrier = threading.Barrier(n)
    results = []
    lock = threading.Lock()

    def worker(i):
        with SessionLocal() as db:
            barrier.wait()
            try:
                out = fn(db, i)
            except AppError as e:
                out = type(e).__name__
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def create_pending(client, user, make_booking):
    b = make_booking(user)
    r = client.post("/payments/", json={"booking_id": b["id"], "simulate": "pending"}, headers=auth(user))
    return b["id"], r.json()["provider_payment_id"]


def test_same_webhook_at_the_same_time(client, user, make_booking, db):
    booking_id, pid = create_pending(client, user, make_booking)
    payload = {"event_id": "evt_race", "provider_payment_id": pid, "status": "SUCCESS"}

    results = run_together(
        lambda s, i: payment_service.handle_webhook(s, "evt_race", pid, PaymentStatus.SUCCESS, payload)[
            "result"
        ]
    )

    assert Counter(results) == {"processed": 1, "duplicate": N - 1}
    assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 1
    assert db.get(Booking, booking_id).status.value == "CONFIRMED"


def test_conflicting_webhooks_at_the_same_time(client, user, make_booking, db):
    booking_id, pid = create_pending(client, user, make_booking)

    def send(s, i):
        status = PaymentStatus.SUCCESS if i % 2 else PaymentStatus.FAILED
        return payment_service.handle_webhook(s, f"evt_{i}", pid, status, {})["result"]

    results = run_together(send)

    assert results.count("processed") == 1
    payment = db.scalar(select(Payment).where(Payment.provider_payment_id == pid))
    booking = db.get(Booking, booking_id)
    # whichever won, payment and booking agree
    expected = {"SUCCESS": "CONFIRMED", "FAILED": "FAILED"}[payment.status.value]
    assert booking.status.value == expected


def test_many_payments_for_one_booking_at_the_same_time(client, user, make_booking, db):
    booking_id = make_booking(user)["id"]
    user_id = user.id

    results = run_together(
        lambda s, i: (
            payment_service.create_payment(s, s.get(User, user_id), booking_id, "success")[0].status.value
        )
    )

    assert Counter(results) == {"SUCCESS": 1, "ConflictError": N - 1}
    assert db.scalar(select(func.count()).select_from(Payment)) == 1


def test_same_idempotency_key_at_the_same_time(client, user, make_booking, db):
    booking_id = make_booking(user)["id"]
    user_id = user.id

    def send(s, i):
        payment, created = payment_service.create_payment(
            s, s.get(User, user_id), booking_id, "success", idempotency_key="same-key"
        )
        return payment.id, created

    results = run_together(send)

    assert len({pid for pid, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert db.scalar(select(func.count()).select_from(Payment)) == 1


def test_cancel_and_pay_at_the_same_time(client, user, make_booking, db):
    user_id = user.id
    for _ in range(5):
        booking_id = make_booking(user)["id"]

        def go(s, i, booking_id=booking_id):
            u = s.get(User, user_id)
            if i == 0:
                return booking_service.cancel_booking(s, u, booking_id).status.value
            return payment_service.create_payment(s, u, booking_id, "success")[0].status.value

        run_together(go, n=2)

        db.expire_all()
        booking = db.get(Booking, booking_id)
        payments = db.scalars(select(Payment.status).where(Payment.booking_id == booking_id)).all()
        if booking.status.value == "CANCELLED":
            assert payments == []
        else:
            assert booking.status.value == "CONFIRMED"
            assert [p.value for p in payments] == ["SUCCESS"]
