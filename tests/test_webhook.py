import json

import pytest
from sqlalchemy import func, select

from app.models import WebhookEvent
from app.services.payments import sign
from tests.conftest import auth, send_webhook


@pytest.fixture
def pending(client, user, make_booking):
    """A booking with a PENDING payment. Returns (booking_id, provider_payment_id)."""
    b = make_booking(user)
    r = client.post("/payments/", json={"booking_id": b["id"], "simulate": "pending"}, headers=auth(user))
    return b["id"], r.json()["provider_payment_id"]


def status_of(client, user, booking_id):
    return client.get(f"/bookings/{booking_id}", headers=auth(user)).json()["status"]


def event_count(db, **filters):
    return db.scalar(select(func.count()).select_from(WebhookEvent).filter_by(**filters))


def test_webhook_success_confirms_booking(client, user, pending):
    booking_id, pid = pending
    r = send_webhook(client, pid, "SUCCESS")
    assert r.status_code == 200
    assert r.json() == {"result": "processed", "payment_status": "SUCCESS", "booking_status": "CONFIRMED"}
    assert status_of(client, user, booking_id) == "CONFIRMED"


def test_webhook_failure_then_user_can_retry(client, user, pending):
    booking_id, pid = pending
    assert send_webhook(client, pid, "FAILED").json()["booking_status"] == "FAILED"
    r = client.post("/payments/", json={"booking_id": booking_id}, headers=auth(user))
    assert r.json()["booking_status"] == "CONFIRMED"


def test_same_event_twice_is_processed_once(client, user, pending, db):
    booking_id, pid = pending
    first = send_webhook(client, pid, "SUCCESS", event_id="evt_same")
    second = send_webhook(client, pid, "SUCCESS", event_id="evt_same")
    third = send_webhook(client, pid, "SUCCESS", event_id="evt_same")

    assert first.json()["result"] == "processed"
    assert second.status_code == third.status_code == 200
    assert second.json()["result"] == third.json()["result"] == "duplicate"
    assert event_count(db, event_id="evt_same") == 1
    assert status_of(client, user, booking_id) == "CONFIRMED"


def test_late_failure_does_not_undo_success(client, user, pending, db):
    booking_id, pid = pending
    send_webhook(client, pid, "SUCCESS", event_id="evt_1")
    r = send_webhook(client, pid, "FAILED", event_id="evt_2")

    assert r.status_code == 200
    assert r.json()["result"].startswith("ignored")
    assert status_of(client, user, booking_id) == "CONFIRMED"
    event = db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == "evt_2"))
    assert event.result == "ignored: payment is SUCCESS, got FAILED"


def test_new_event_with_same_status_is_ignored(client, pending):
    _, pid = pending
    send_webhook(client, pid, "SUCCESS", event_id="evt_1")
    assert send_webhook(client, pid, "SUCCESS", event_id="evt_2").json()["result"] == "ignored: payment already SUCCESS"


def test_unknown_payment(client, db):
    r = send_webhook(client, "pay_does_not_exist", "SUCCESS", event_id="evt_x")
    assert r.status_code == 404
    # not stored, so a retry from the provider would still be processed
    assert event_count(db, event_id="evt_x") == 0


def _post_raw(client, body: bytes, signature):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Signature"] = signature
    return client.post("/payments/webhook/", content=body, headers=headers)


def test_signature_required(client, user, pending, db):
    booking_id, pid = pending
    body = json.dumps({"event_id": "e", "provider_payment_id": pid, "status": "SUCCESS"}).encode()

    assert _post_raw(client, body, None).status_code == 401
    assert _post_raw(client, body, "deadbeef").status_code == 401

    # signature made for a different body
    tampered = body.replace(b"SUCCESS", b"FAILED")
    assert _post_raw(client, tampered, sign(body)).status_code == 401

    assert event_count(db) == 0
    assert status_of(client, user, booking_id) == "PENDING"


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'{"provider_payment_id": "p", "status": "SUCCESS"}',
        b'{"event_id": "e", "provider_payment_id": "p", "status": "PENDING"}',
        b'{"event_id": "e", "provider_payment_id": "p", "status": "success"}',
        b'{"event_id": "", "provider_payment_id": "p", "status": "SUCCESS"}',
    ],
)
def test_invalid_payload(client, body):
    assert _post_raw(client, body, sign(body)).status_code == 422
