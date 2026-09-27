from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.models import Booking, WebhookEvent
from app.services import payments as payment_service
from app.services.bookings import expire_unpaid_bookings
from app.services.payments import MAX_ATTEMPTS, retry_pending_events
from tests.conftest import auth, send_webhook


@pytest.fixture
def pending(client, user, make_booking):
    b = make_booking(user)
    r = client.post("/payments/", json={"booking_id": b["id"], "simulate": "pending"}, headers=auth(user))
    return b["id"], r.json()["provider_payment_id"]


@pytest.fixture
def broken_processing(monkeypatch):
    """Makes processing crash, like a db error half way through."""
    calls = {"n": 0}
    original = payment_service._apply_result

    def fail(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(payment_service, "_apply_result", fail)
    calls["restore"] = lambda: monkeypatch.setattr(payment_service, "_apply_result", original)
    return calls


def get_event(db, event_id):
    db.expire_all()
    return db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event_id))


def make_due(db, event_id):
    db.execute(
        update(WebhookEvent)
        .where(WebhookEvent.event_id == event_id)
        .values(next_retry_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    db.commit()


def booking_status(db, booking_id):
    db.expire_all()
    return db.get(Booking, booking_id).status.value


def test_failed_processing_is_saved_and_queued(client, db, pending, broken_processing):
    booking_id, pid = pending
    r = send_webhook(client, pid, "SUCCESS", event_id="evt_1")

    assert r.status_code == 202
    assert r.json()["result"] == "queued for retry"
    event = get_event(db, "evt_1")
    assert event.processed_at is None
    assert event.attempts == 1
    assert event.last_error == "RuntimeError: database hiccup"
    assert event.next_retry_at > datetime.now(timezone.utc)
    # nothing half applied
    assert booking_status(db, booking_id) == "PENDING"


def test_retry_job_processes_it_later(client, db, pending, broken_processing):
    booking_id, pid = pending
    send_webhook(client, pid, "SUCCESS", event_id="evt_1")
    broken_processing["restore"]()

    # not due yet
    assert retry_pending_events(db) == 0
    assert booking_status(db, booking_id) == "PENDING"

    make_due(db, "evt_1")
    assert retry_pending_events(db) == 1
    assert booking_status(db, booking_id) == "CONFIRMED"
    event = get_event(db, "evt_1")
    assert event.result == "processed"
    assert event.next_retry_at is None

    # nothing left to do
    make_due(db, "evt_1")
    assert retry_pending_events(db) == 0


def test_duplicate_of_queued_event_is_not_processed_again(client, db, pending, broken_processing):
    _, pid = pending
    send_webhook(client, pid, "SUCCESS", event_id="evt_1")
    r = send_webhook(client, pid, "SUCCESS", event_id="evt_1")
    assert r.status_code == 200
    assert r.json()["result"] == "duplicate"
    assert broken_processing["n"] == 1


def test_backoff_grows_between_attempts(client, db, pending, broken_processing):
    _, pid = pending
    send_webhook(client, pid, "SUCCESS", event_id="evt_1")
    gaps = []
    for _ in range(2):
        make_due(db, "evt_1")
        before = datetime.now(timezone.utc)
        retry_pending_events(db)
        gaps.append((get_event(db, "evt_1").next_retry_at - before).total_seconds())
    assert gaps[0] == pytest.approx(60, abs=2)
    assert gaps[1] == pytest.approx(120, abs=2)


def test_gives_up_after_max_attempts(client, db, pending, broken_processing):
    booking_id, pid = pending
    send_webhook(client, pid, "SUCCESS", event_id="evt_1")
    for _ in range(MAX_ATTEMPTS + 2):
        make_due(db, "evt_1")
        retry_pending_events(db)

    event = get_event(db, "evt_1")
    assert event.attempts == MAX_ATTEMPTS
    assert event.result == f"failed after {MAX_ATTEMPTS} attempts"
    assert event.processed_at is not None
    assert booking_status(db, booking_id) == "PENDING"


def test_retry_after_payment_was_settled_another_way(client, user, db, pending, broken_processing):
    booking_id, pid = pending
    send_webhook(client, pid, "FAILED", event_id="evt_1")
    broken_processing["restore"]()
    # a second event (e.g. provider resent with a new id) succeeds meanwhile
    send_webhook(client, pid, "SUCCESS", event_id="evt_2")
    assert booking_status(db, booking_id) == "CONFIRMED"

    make_due(db, "evt_1")
    retry_pending_events(db)
    # the late FAILED retry must not undo the confirmed booking
    assert booking_status(db, booking_id) == "CONFIRMED"
    assert get_event(db, "evt_1").result == "ignored: payment is SUCCESS, got FAILED"


def _set_appointment(db, booking_id, delta):
    db.execute(
        update(Booking)
        .where(Booking.id == booking_id)
        .values(appointment_at=datetime.now(timezone.utc) + delta)
    )
    db.commit()


def test_expire_unpaid_bookings(client, user, db, make_booking):
    past_pending = make_booking(user)["id"]
    past_failed = make_booking(user)["id"]
    past_paid = make_booking(user)["id"]
    past_paying = make_booking(user)["id"]
    future_pending = make_booking(user)["id"]

    client.post("/payments/", json={"booking_id": past_failed, "simulate": "failure"}, headers=auth(user))
    client.post("/payments/", json={"booking_id": past_paid, "simulate": "success"}, headers=auth(user))
    client.post("/payments/", json={"booking_id": past_paying, "simulate": "pending"}, headers=auth(user))
    for b in (past_pending, past_failed, past_paid, past_paying):
        _set_appointment(db, b, timedelta(hours=-1))

    assert expire_unpaid_bookings(db) == 2
    assert booking_status(db, past_pending) == "CANCELLED"
    assert booking_status(db, past_failed) == "CANCELLED"
    assert booking_status(db, past_paid) == "CONFIRMED"
    assert booking_status(db, past_paying) == "PENDING"  # payment still in progress
    assert booking_status(db, future_pending) == "PENDING"
    assert expire_unpaid_bookings(db) == 0


def test_celery_tasks_run(db, client, user, make_booking):
    from app import worker

    b = make_booking(user)["id"]
    _set_appointment(db, b, timedelta(hours=-1))
    # calling the task function directly runs it in this process, no broker needed
    assert worker.expire_bookings() == 1
    assert worker.retry_webhook_events() == 0
