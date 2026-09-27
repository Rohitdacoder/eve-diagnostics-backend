from app.models import CentreTest
from tests.conftest import auth


def test_list_centres_is_public_and_paginated(client, admin):
    for i in range(5):
        client.post("/centres/", json={"name": f"Centre {i}", "location": "Delhi"}, headers=auth(admin))

    r = client.get("/centres/?limit=2&offset=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert [c["name"] for c in body["items"]] == ["Centre 2", "Centre 3"]


def test_pagination_limits(client):
    assert client.get("/centres/?limit=0").status_code == 422
    assert client.get("/centres/?limit=101").status_code == 422
    assert client.get("/centres/?offset=-1").status_code == 422


def test_filter_by_location(client, admin):
    client.post("/centres/", json={"name": "A", "location": "New Delhi"}, headers=auth(admin))
    client.post("/centres/", json={"name": "B", "location": "Mumbai"}, headers=auth(admin))

    names = [c["name"] for c in client.get("/centres/?location=delhi").json()["items"]]
    assert names == ["A"]


def test_location_filter_treats_wildcards_literally(client, admin):
    client.post("/centres/", json={"name": "A", "location": "Delhi"}, headers=auth(admin))
    assert client.get("/centres/?location=%25").json()["total"] == 0
    assert client.get("/centres/?location=_").json()["total"] == 0


def test_centre_detail_shows_active_tests_only(client, admin, offering, db):
    t2 = client.post("/tests/", json={"name": "MRI"}, headers=auth(admin)).json()
    client.post(f"/centres/{offering.centre_id}/tests", json={"test_id": t2["id"], "price": "4000"}, headers=auth(admin))
    client.patch(f"/centres/{offering.centre_id}/tests/{t2['id']}", json={"is_active": False}, headers=auth(admin))

    r = client.get(f"/centres/{offering.centre_id}")
    assert r.status_code == 200
    assert [(t["test_name"], t["price"]) for t in r.json()["tests"]] == [("CBC", "500.00")]


def test_filter_by_test_ignores_inactive(client, admin, offering):
    assert client.get(f"/centres/?test_id={offering.test_id}").json()["total"] == 1
    client.patch(
        f"/centres/{offering.centre_id}/tests/{offering.test_id}",
        json={"is_active": False},
        headers=auth(admin),
    )
    assert client.get(f"/centres/?test_id={offering.test_id}").json()["total"] == 0


def test_centre_not_found(client):
    assert client.get("/centres/9999").status_code == 404


def test_writes_need_admin(client, user, offering):
    cid, tid = offering.centre_id, offering.test_id
    calls = [
        ("post", "/centres/", {"name": "X", "location": "Y"}),
        ("patch", f"/centres/{cid}", {"name": "X"}),
        ("post", "/tests/", {"name": "X"}),
        ("post", f"/centres/{cid}/tests", {"test_id": tid, "price": "1"}),
        ("patch", f"/centres/{cid}/tests/{tid}", {"price": "1"}),
    ]
    for method, url, body in calls:
        assert getattr(client, method)(url, json=body).status_code == 401
        assert getattr(client, method)(url, json=body, headers=auth(user)).status_code == 403


def test_create_centre_trims_and_rejects_duplicates(client, admin):
    r = client.post("/centres/", json={"name": "  Goa Labs ", "location": " Panaji "}, headers=auth(admin))
    assert r.status_code == 201
    assert r.json()["name"] == "Goa Labs"

    r = client.post("/centres/", json={"name": "Goa Labs", "location": "Panaji"}, headers=auth(admin))
    assert r.status_code == 409

    r = client.post("/centres/", json={"name": "   ", "location": "Panaji"}, headers=auth(admin))
    assert r.status_code == 422


def test_add_offering(client, admin, offering):
    t2 = client.post("/tests/", json={"name": "MRI"}, headers=auth(admin)).json()
    url = f"/centres/{offering.centre_id}/tests"

    r = client.post(url, json={"test_id": t2["id"], "price": "199.50"}, headers=auth(admin))
    assert r.status_code == 201
    assert r.json()["price"] == "199.50"

    assert client.post(url, json={"test_id": t2["id"], "price": "1"}, headers=auth(admin)).status_code == 409
    assert client.post(url, json={"test_id": 9999, "price": "1"}, headers=auth(admin)).status_code == 404
    assert client.post("/centres/9999/tests", json={"test_id": t2["id"], "price": "1"}, headers=auth(admin)).status_code == 404


def test_price_validation(client, admin, offering):
    url = f"/centres/{offering.centre_id}/tests/{offering.test_id}"
    for bad in ["0", "-5", "10.999", "abc"]:
        assert client.patch(url, json={"price": bad}, headers=auth(admin)).status_code == 422


def test_update_price_returns_stored_value(client, admin, offering, db):
    url = f"/centres/{offering.centre_id}/tests/{offering.test_id}"
    r = client.patch(url, json={"price": "220"}, headers=auth(admin))
    assert r.json()["price"] == "220.00"
    db.expire_all()
    assert str(db.get(CentreTest, (offering.centre_id, offering.test_id)).price) == "220.00"
