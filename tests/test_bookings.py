from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.models import Booking, BookingStatus
from app.services.bookings import can_transition
from tests.conftest import auth, future

S = BookingStatus


@pytest.mark.parametrize(
    "current,new,allowed",
    [
        (S.PENDING, S.CONFIRMED, True),
        (S.PENDING, S.FAILED, True),
        (S.PENDING, S.CANCELLED, True),
        (S.FAILED, S.CONFIRMED, True),
        (S.FAILED, S.CANCELLED, True),
        (S.FAILED, S.PENDING, False),
        (S.CONFIRMED, S.CANCELLED, False),
        (S.CONFIRMED, S.FAILED, False),
        (S.CONFIRMED, S.PENDING, False),
        (S.CANCELLED, S.PENDING, False),
        (S.CANCELLED, S.CONFIRMED, False),
    ],
)
def test_status_transitions(current, new, allowed):
    assert can_transition(current, new) is allowed


def test_create_booking(client, user, offering):
    r = client.post(
        "/bookings/",
        json={"centre_id": offering.centre_id, "test_id": offering.test_id, "appointment_at": future()},
        headers=auth(user),
    )
    assert r.status_code == 201
    b = r.json()
    assert b["status"] == "PENDING"
    assert b["amount"] == "500.00"
    assert b["centre_name"] == "City Diagnostics"
    assert b["appointment_at"].endswith("Z")


def test_create_booking_needs_login(client, offering):
    r = client.post("/bookings/", json={"centre_id": 1, "test_id": 1, "appointment_at": future()})
    assert r.status_code == 401


def test_client_cannot_set_amount_or_status(client, user, make_booking):
    b = make_booking(user, amount="1.00", status="CONFIRMED")
    assert b["amount"] == "500.00"
    assert b["status"] == "PENDING"


def test_duplicate_booking_rejected(client, user, other_user, offering):
    body = {"centre_id": offering.centre_id, "test_id": offering.test_id, "appointment_at": future()}
    assert client.post("/bookings/", json=body, headers=auth(user)).status_code == 201
    assert client.post("/bookings/", json=body, headers=auth(user)).status_code == 409
    # another person can book the same slot
    assert client.post("/bookings/", json=body, headers=auth(other_user)).status_code == 201


def test_can_rebook_after_cancelling(client, user, offering):
    body = {"centre_id": offering.centre_id, "test_id": offering.test_id, "appointment_at": future()}
    b = client.post("/bookings/", json=body, headers=auth(user)).json()
    client.post(f"/bookings/{b['id']}/cancel", headers=auth(user))
    assert client.post("/bookings/", json=body, headers=auth(user)).status_code == 201


@pytest.mark.parametrize(
    "when,code",
    [
        ((datetime.now(UTC) - timedelta(hours=1)).isoformat(), 400),
        ((datetime.now(UTC) + timedelta(days=120)).isoformat(), 400),
        ("2026-10-01T10:00:00", 422),  # no timezone
        ("tomorrow", 422),
    ],
)
def test_appointment_time_validation(client, user, offering, when, code):
    r = client.post(
        "/bookings/",
        json={"centre_id": offering.centre_id, "test_id": offering.test_id, "appointment_at": when},
        headers=auth(user),
    )
    assert r.status_code == code


def test_cannot_book_test_not_offered_or_inactive(client, user, admin, offering):
    body = {"centre_id": offering.centre_id, "test_id": 9999, "appointment_at": future()}
    assert client.post("/bookings/", json=body, headers=auth(user)).status_code == 404

    body = {"centre_id": 9999, "test_id": offering.test_id, "appointment_at": future()}
    assert client.post("/bookings/", json=body, headers=auth(user)).status_code == 404

    client.patch(
        f"/centres/{offering.centre_id}/tests/{offering.test_id}",
        json={"is_active": False},
        headers=auth(admin),
    )
    body = {"centre_id": offering.centre_id, "test_id": offering.test_id, "appointment_at": future()}
    assert client.post("/bookings/", json=body, headers=auth(user)).status_code == 404


def test_price_change_does_not_affect_existing_booking(client, user, admin, offering, make_booking):
    b = make_booking(user)
    client.patch(
        f"/centres/{offering.centre_id}/tests/{offering.test_id}", json={"price": "999"}, headers=auth(admin)
    )
    assert client.get(f"/bookings/{b['id']}", headers=auth(user)).json()["amount"] == "500.00"


def test_users_only_see_their_own_bookings(client, user, other_user, admin, make_booking):
    mine = make_booking(user)
    theirs = make_booking(other_user)

    ids = [b["id"] for b in client.get("/bookings/", headers=auth(user)).json()["items"]]
    assert ids == [mine["id"]]

    # someone else's booking looks like it doesn't exist
    assert client.get(f"/bookings/{theirs['id']}", headers=auth(user)).status_code == 404
    assert client.get("/bookings/9999", headers=auth(user)).status_code == 404

    assert client.get(f"/bookings/{theirs['id']}", headers=auth(admin)).status_code == 200
    assert client.get("/bookings/", headers=auth(admin)).json()["total"] == 2


def test_list_filter_and_order(client, user, make_booking):
    first = make_booking(user)
    second = make_booking(user)
    client.post(f"/bookings/{first['id']}/cancel", headers=auth(user))

    items = client.get("/bookings/", headers=auth(user)).json()["items"]
    assert [b["id"] for b in items] == [second["id"], first["id"]]

    cancelled = client.get("/bookings/?status=CANCELLED", headers=auth(user)).json()["items"]
    assert [b["id"] for b in cancelled] == [first["id"]]

    assert client.get("/bookings/?status=DONE", headers=auth(user)).status_code == 422


def test_cancel(client, user, make_booking):
    b = make_booking(user)
    r = client.post(f"/bookings/{b['id']}/cancel", headers=auth(user))
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    # second cancel is not allowed
    assert client.post(f"/bookings/{b['id']}/cancel", headers=auth(user)).status_code == 409


def test_cannot_cancel_someone_elses_booking(client, user, other_user, make_booking):
    b = make_booking(user)
    assert client.post(f"/bookings/{b['id']}/cancel", headers=auth(other_user)).status_code == 404
    assert client.get(f"/bookings/{b['id']}", headers=auth(user)).json()["status"] == "PENDING"


def test_cannot_cancel_confirmed_booking(client, user, make_booking, db):
    b = make_booking(user)
    db.execute(update(Booking).where(Booking.id == b["id"]).values(status=BookingStatus.CONFIRMED))
    db.commit()
    assert client.post(f"/bookings/{b['id']}/cancel", headers=auth(user)).status_code == 409
