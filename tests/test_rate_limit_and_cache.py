from app.config import settings
from tests.conftest import auth


def test_login_rate_limit(client, user):
    body = {"email": "rohit@example.com", "password": "wrong1234"}
    codes = [client.post("/auth/login", json=body).status_code for _ in range(10)]
    assert codes == [401] * 10

    r = client.post("/auth/login", json=body)
    assert r.status_code == 429
    assert 1 <= int(r.headers["Retry-After"]) <= 60
    # also blocks correct passwords, otherwise it wouldn't stop guessing
    assert client.post("/auth/login", json={**body, "password": "secret123"}).status_code == 429


def test_signup_rate_limit(client):
    for i in range(5):
        r = client.post("/auth/signup", json={"email": f"u{i}@example.com", "full_name": "U", "password": "secret123"})
        assert r.status_code == 201
    r = client.post("/auth/signup", json={"email": "u9@example.com", "full_name": "U", "password": "secret123"})
    assert r.status_code == 429


def test_rate_limit_can_be_turned_off(client, user, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    body = {"email": "rohit@example.com", "password": "wrong1234"}
    assert all(client.post("/auth/login", json=body).status_code == 401 for _ in range(15))


def test_payments_rate_limit(client, user, make_booking):
    b = make_booking(user)
    for _ in range(20):
        client.post("/payments/", json={"booking_id": b["id"]}, headers=auth(user))
    r = client.post("/payments/", json={"booking_id": b["id"]}, headers=auth(user))
    assert r.status_code == 429


def test_centre_list_is_cached(client, offering):
    first = client.get("/centres/")
    second = client.get("/centres/")
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert first.json() == second.json()


def test_cache_key_ignores_location_case(client, offering):
    client.get("/centres/?location=Delhi")
    assert client.get("/centres/?location=delhi").headers["X-Cache"] == "HIT"


def test_different_params_are_cached_separately(client, offering):
    client.get("/centres/?limit=5")
    assert client.get("/centres/?limit=6").headers["X-Cache"] == "MISS"


def test_admin_change_clears_cache(client, admin, offering):
    cid, tid = offering.centre_id, offering.test_id
    client.get(f"/centres/{cid}")
    client.get("/centres/")
    assert client.get(f"/centres/{cid}").headers["X-Cache"] == "HIT"

    client.patch(f"/centres/{cid}/tests/{tid}", json={"price": "650"}, headers=auth(admin))

    detail = client.get(f"/centres/{cid}")
    assert detail.headers["X-Cache"] == "MISS"
    assert detail.json()["tests"][0]["price"] == "650.00"
    assert client.get("/centres/").headers["X-Cache"] == "MISS"

    client.post("/centres/", json={"name": "New", "location": "Goa"}, headers=auth(admin))
    listing = client.get("/centres/")
    assert listing.headers["X-Cache"] == "MISS"
    assert listing.json()["total"] == 2


def test_failed_admin_write_does_not_matter_for_cache(client, admin, offering):
    client.get("/centres/")
    r = client.post("/centres/", json={"name": "City Diagnostics", "location": "Delhi"}, headers=auth(admin))
    assert r.status_code == 409
    assert client.get("/centres/").headers["X-Cache"] == "HIT"


def test_not_found_is_not_cached(client, admin):
    assert client.get("/centres/1").status_code == 404
    client.post("/centres/", json={"name": "A", "location": "B"}, headers=auth(admin))
    assert client.get("/centres/1").status_code == 200


def test_tests_list_is_cached(client, offering):
    assert client.get("/tests/").headers["X-Cache"] == "MISS"
    r = client.get("/tests/")
    assert r.headers["X-Cache"] == "HIT"
    assert r.json()["items"][0]["name"] == "CBC"
