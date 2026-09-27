from sqlalchemy import func, select, update

from app.models import Booking, Payment
from tests.conftest import auth


def pay(client, user, booking_id, simulate="success", key=None):
    headers = auth(user)
    if key:
        headers["Idempotency-Key"] = key
    return client.post("/payments/", json={"booking_id": booking_id, "simulate": simulate}, headers=headers)


def booking_status(client, user, booking_id):
    return client.get(f"/bookings/{booking_id}", headers=auth(user)).json()["status"]


def test_payment_success_confirms_booking(client, user, make_booking):
    b = make_booking(user)
    r = pay(client, user, b["id"])
    assert r.status_code == 201
    p = r.json()
    assert p["status"] == "SUCCESS"
    assert p["amount"] == "500.00"
    assert p["booking_status"] == "CONFIRMED"
    assert p["provider_payment_id"].startswith("pay_")


def test_payment_failure_then_retry(client, user, make_booking, db):
    b = make_booking(user)
    assert pay(client, user, b["id"], "failure").json()["booking_status"] == "FAILED"
    assert pay(client, user, b["id"], "failure").json()["booking_status"] == "FAILED"
    assert pay(client, user, b["id"], "success").json()["booking_status"] == "CONFIRMED"

    statuses = db.scalars(
        select(Payment.status).where(Payment.booking_id == b["id"]).order_by(Payment.id)
    ).all()
    assert [s.value for s in statuses] == ["FAILED", "FAILED", "SUCCESS"]


def test_cannot_pay_twice(client, user, make_booking):
    b = make_booking(user)
    pay(client, user, b["id"])
    r = pay(client, user, b["id"])
    assert r.status_code == 409
    assert r.json()["detail"] == "Booking is already paid"


def test_cannot_pay_for_cancelled_booking(client, user, make_booking):
    b = make_booking(user)
    client.post(f"/bookings/{b['id']}/cancel", headers=auth(user))
    assert pay(client, user, b["id"]).status_code == 409


def test_cannot_pay_for_past_appointment(client, user, make_booking, db):
    b = make_booking(user)
    db.execute(
        update(Booking)
        .where(Booking.id == b["id"])
        .values(appointment_at=func.now() - func.make_interval(0, 0, 0, 0, 1))
    )
    db.commit()
    assert pay(client, user, b["id"]).status_code == 400


def test_cannot_pay_for_someone_elses_booking(client, user, other_user, make_booking):
    b = make_booking(user)
    assert pay(client, other_user, b["id"]).status_code == 404
    assert booking_status(client, user, b["id"]) == "PENDING"


def test_payment_validation(client, user, make_booking):
    b = make_booking(user)
    assert pay(client, user, 9999).status_code == 404
    assert pay(client, user, b["id"], simulate="maybe").status_code == 422
    assert client.post("/payments/", json={"booking_id": b["id"]}).status_code == 401


def test_idempotency_key_returns_same_payment(client, user, make_booking, db):
    b = make_booking(user)
    first = pay(client, user, b["id"], key="key-1")
    again = pay(client, user, b["id"], key="key-1")
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert db.scalar(select(func.count()).select_from(Payment).where(Payment.booking_id == b["id"])) == 1


def test_idempotency_key_cannot_be_reused_for_another_booking(client, user, make_booking):
    b1, b2 = make_booking(user), make_booking(user)
    pay(client, user, b1["id"], key="key-1")
    assert pay(client, user, b2["id"], key="key-1").status_code == 409


def test_pending_payment_blocks_second_payment_and_cancel(client, user, make_booking):
    b = make_booking(user)
    r = pay(client, user, b["id"], "pending")
    assert r.json()["status"] == "PENDING"
    assert r.json()["booking_status"] == "PENDING"

    assert pay(client, user, b["id"]).status_code == 409
    assert client.post(f"/bookings/{b['id']}/cancel", headers=auth(user)).status_code == 409
