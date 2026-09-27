from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.config import settings
from app.core.security import create_access_token
from tests.conftest import auth

SIGNUP = {"email": "Rohit@Example.com", "full_name": "  Rohit  ", "password": "secret123"}


def test_signup(client):
    r = client.post("/auth/signup", json=SIGNUP)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "rohit@example.com"
    assert body["full_name"] == "Rohit"
    assert body["is_admin"] is False
    assert "password" not in body and "hashed_password" not in body


def test_signup_duplicate_email_is_case_insensitive(client):
    client.post("/auth/signup", json=SIGNUP)
    r = client.post("/auth/signup", json={**SIGNUP, "email": "ROHIT@example.com"})
    assert r.status_code == 409


def test_signup_cannot_make_itself_admin(client):
    r = client.post("/auth/signup", json={**SIGNUP, "is_admin": True})
    assert r.status_code == 201
    assert r.json()["is_admin"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"email": "not-an-email"},
        {"password": "short1"},
        {"password": "onlyletters"},
        {"password": "12345678"},
        {"password": "a1" * 40},
        {"full_name": "   "},
        {"email": None},
    ],
)
def test_signup_validation(client, change):
    r = client.post("/auth/signup", json={**SIGNUP, **change})
    assert r.status_code == 422


def test_login(client):
    client.post("/auth/signup", json=SIGNUP)
    r = client.post("/auth/login", json={"email": "ROHIT@example.com", "password": "secret123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == "rohit@example.com"


def test_login_wrong_password_and_unknown_email_look_the_same(client):
    client.post("/auth/signup", json=SIGNUP)
    wrong_pw = client.post("/auth/login", json={"email": "rohit@example.com", "password": "wrong1234"})
    no_user = client.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong1234"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()


def _token(sub, exp_delta, secret=None, alg="HS256"):
    now = datetime.now(timezone.utc)
    payload = {"sub": sub, "iat": now, "exp": now + exp_delta}
    return jwt.encode(payload, secret or settings.secret_key, algorithm=alg)


def test_me_requires_token(client):
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        _token("1", timedelta(hours=-1)),  # expired
        _token("1", timedelta(hours=1), secret="some-other-secret-that-is-also-32-bytes-long"),  # wrong key
        _token("not-a-number", timedelta(hours=1)),
    ],
)
def test_me_rejects_bad_tokens(client, user, token):
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_me_rejects_unsigned_alg_none_token(client, user):
    token = jwt.encode({"sub": str(user.id)}, key=None, algorithm="none")
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_me_rejects_token_for_deleted_user(client):
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {create_access_token(9999)}"})
    assert r.status_code == 401


def test_me(client, user):
    r = client.get("/auth/me", headers=auth(user))
    assert r.status_code == 200
    assert r.json()["id"] == user.id
