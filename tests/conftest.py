import os

# must be set before anything from app is imported
os.environ.setdefault("TEST_DATABASE_URL", "postgresql+psycopg://eve:eve@localhost:5432/eve_test")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["WEBHOOK_SECRET"] = "test-webhook-secret-at-least-32-bytes"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-at-least-32-bytes"

from datetime import datetime, timedelta, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402
import json  # noqa: E402

import psycopg  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import make_url, text  # noqa: E402

from app.core.security import create_access_token, hash_password  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest, User  # noqa: E402
from app.services.payments import sign  # noqa: E402

PASSWORD = "secret123"
PASSWORD_HASH = hash_password(PASSWORD)  # bcrypt is slow, hash once


def _create_test_database():
    url = make_url(os.environ["TEST_DATABASE_URL"])
    admin_dsn = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (url.database,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{url.database}"')


@pytest.fixture(scope="session", autouse=True)
def database():
    _create_test_database()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    # build the schema with the real migrations, so they get tested too
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_user(db):
    counter = iter(range(1, 1000))

    def _make(email=None, is_admin=False):
        user = User(
            email=email or f"user{next(counter)}@example.com",
            full_name="Test User",
            hashed_password=PASSWORD_HASH,
            is_admin=is_admin,
        )
        db.add(user)
        db.commit()
        return user

    return _make


def auth(user) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture
def user(make_user):
    return make_user("rohit@example.com")


@pytest.fixture
def other_user(make_user):
    return make_user("priya@example.com")


@pytest.fixture
def admin(make_user):
    return make_user("admin@example.com", is_admin=True)


@pytest.fixture
def offering(db):
    """A centre offering one test at 500.00"""
    centre = DiagnosticCentre(name="City Diagnostics", location="Delhi")
    test = DiagnosticTest(name="CBC")
    db.add_all([centre, test])
    db.flush()
    o = CentreTest(centre_id=centre.id, test_id=test.id, price=Decimal("500.00"))
    db.add(o)
    db.commit()
    return o


def future(days=3, hour=10) -> str:
    t = datetime.now(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)
    return t.isoformat()


@pytest.fixture
def make_booking(client, offering):
    hours = iter(range(0, 24))

    def _make(owner, **overrides):
        body = {
            "centre_id": offering.centre_id,
            "test_id": offering.test_id,
            "appointment_at": future(hour=next(hours)),
            **overrides,
        }
        r = client.post("/bookings/", json=body, headers=auth(owner))
        assert r.status_code == 201, r.text
        return r.json()

    return _make


def send_webhook(client, provider_payment_id, status, event_id="evt_1"):
    body = json.dumps(
        {"event_id": event_id, "provider_payment_id": provider_payment_id, "status": status}
    ).encode()
    return client.post(
        "/payments/webhook/",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": sign(body)},
    )
