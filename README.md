# EVE Diagnostics Backend

[![CI](https://github.com/Rohitdacoder/eve-diagnostics-backend/actions/workflows/ci.yml/badge.svg)](https://github.com/Rohitdacoder/eve-diagnostics-backend/actions/workflows/ci.yml)

Backend for booking diagnostic tests at diagnostic centres, with a simulated payment provider and an idempotent payment webhook.

Built with FastAPI, PostgreSQL, SQLAlchemy 2.0, Alembic, Redis and Celery.

- [Running it](#running-it)
- [API](#api)
- [Example flow](#example-flow)
- [Database design](#database-design)
- [How payments and the webhook work](#how-payments-and-the-webhook-work)
- [Edge cases handled](#edge-cases-handled)
- [Bonus features](#bonus-features)
- [Tests](#tests)
- [Project structure](#project-structure)
- [Assumptions](#assumptions)
- [What I would improve with more time](#what-i-would-improve-with-more-time)

## Running it

### With Docker (recommended)

```bash
git clone https://github.com/Rohitdacoder/eve-diagnostics-backend.git
cd eve-diagnostics-backend
docker compose up -d --build
```

This starts PostgreSQL, Redis, the API and a Celery worker. The API container runs the migrations and loads some sample centres and tests on startup.

- API: http://localhost:8000
- Swagger docs: http://localhost:8000/docs

Create an admin user (needed to add centres/tests):

```bash
docker compose exec api python -m app.scripts.create_admin admin@example.com "Admin" 'admin1234'
```

If ports 8000 / 5432 / 6379 are already in use: `API_PORT=8080 DB_PORT=5433 REDIS_PORT=6380 docker compose up -d --build`

### Without Docker for the app

Needs Python 3.12. Postgres and Redis can still come from compose.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env

docker compose up -d db redis
alembic upgrade head
python -m app.scripts.seed
python -m app.scripts.create_admin admin@example.com "Admin" 'admin1234'

uvicorn app.main:app --reload                    # api
celery -A app.worker worker --beat --loglevel=info  # background jobs (separate terminal)
```

### Configuration

All settings come from environment variables (or `.env`), see `.env.example`.

| Variable | Default | |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://eve:eve@localhost:5432/eve` | |
| `SECRET_KEY` | | JWT signing key |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | |
| `WEBHOOK_SECRET` | | shared secret for webhook signatures |
| `REDIS_URL` | empty | empty = in-memory store (single process only) |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | |
| `RATE_LIMIT_ENABLED` | `true` | |
| `CACHE_ENABLED` / `CACHE_TTL_SECONDS` | `true` / `300` | |
| `LOG_FORMAT` / `LOG_LEVEL` | `json` / `INFO` | `text` for readable local logs |

## API

Full interactive docs at `/docs`. All errors are returned as `{"detail": ...}`.

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/signup` | - | create account |
| POST | `/auth/login` | - | get a JWT |
| GET | `/auth/me` | user | current user |
| GET | `/centres/` | - | list centres. Filters `location`, `test_id`. Paginated |
| GET | `/centres/{id}` | - | centre with the tests it offers and their prices |
| POST | `/centres/` | admin | create centre |
| PATCH | `/centres/{id}` | admin | update centre |
| POST | `/centres/{id}/tests` | admin | offer a test at a centre with a price |
| PATCH | `/centres/{id}/tests/{test_id}` | admin | change price, or enable/disable (`is_active`) |
| GET | `/tests/` | - | list all tests. Paginated |
| POST | `/tests/` | admin | create test |
| POST | `/bookings/` | user | book a test |
| GET | `/bookings/` | user | my bookings (admin: all). Filter `status`. Paginated |
| GET | `/bookings/{id}` | user | one booking |
| POST | `/bookings/{id}/cancel` | user | cancel |
| POST | `/payments/` | user | pay for a booking (simulated). Optional `Idempotency-Key` header |
| POST | `/payments/webhook/` | signature | payment provider sends the final payment status |
| GET | `/health` | - | health check |

Paginated responses: `{"items": [...], "total": 42, "limit": 20, "offset": 0}` with `?limit=` (1-100) and `?offset=`.

Auth is `Authorization: Bearer <token>`. In Swagger, click **Authorize** and paste the token.

## Example flow

```bash
# sign up and log in
curl -X POST localhost:8000/auth/signup -H "Content-Type: application/json" \
  -d '{"email":"rohit@example.com","full_name":"Rohit","password":"secret123"}'

TOKEN=$(curl -s -X POST localhost:8000/auth/login -H "Content-Type: application/json" \
  -d '{"email":"rohit@example.com","password":"secret123"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# browse
curl "localhost:8000/centres/?location=delhi"
curl localhost:8000/centres/1

# book (appointment time must include a timezone)
curl -X POST localhost:8000/bookings/ -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"centre_id":1,"test_id":1,"appointment_at":"2026-10-05T10:30:00+05:30"}'
# -> {"id":1, ..., "amount":"350.00", "status":"PENDING"}

# pay. simulate = success | failure | pending
curl -X POST localhost:8000/payments/ -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: 7f3c2a" -d '{"booking_id":1,"simulate":"pending"}'
# -> {"id":1, "status":"PENDING", "provider_payment_id":"pay_ab12...", "booking_status":"PENDING", ...}

# the "provider" sends the webhook (script signs it with WEBHOOK_SECRET)
docker compose exec api python -m app.scripts.send_webhook pay_ab12... SUCCESS evt_001
# -> 200 {"result":"processed","payment_status":"SUCCESS","booking_status":"CONFIRMED"}

# same event again
docker compose exec api python -m app.scripts.send_webhook pay_ab12... SUCCESS evt_001
# -> 200 {"result":"duplicate", ...}
```

Webhook request format, if you want to call it directly:

```
POST /payments/webhook/
Content-Type: application/json
X-Signature: <hex HMAC-SHA256 of the raw body, key = WEBHOOK_SECRET>

{"event_id": "evt_001", "provider_payment_id": "pay_ab12...", "status": "SUCCESS"}
```

## Database design

```mermaid
erDiagram
    users ||--o{ bookings : makes
    diagnostic_centres ||--o{ centre_tests : offers
    diagnostic_tests ||--o{ centre_tests : "offered as"
    centre_tests ||--o{ bookings : "booked as"
    bookings ||--o{ payments : "paid by"
    payments ||--o{ webhook_events : "updated by"

    users {
        int id PK
        string email UK
        string full_name
        string hashed_password
        bool is_admin
    }
    diagnostic_centres {
        int id PK
        string name
        string location
    }
    diagnostic_tests {
        int id PK
        string name UK
        text description
    }
    centre_tests {
        int centre_id PK,FK
        int test_id PK,FK
        numeric price
        bool is_active
    }
    bookings {
        int id PK
        int user_id FK
        int centre_id FK
        int test_id FK
        timestamptz appointment_at
        numeric amount
        string status
    }
    payments {
        int id PK
        int booking_id FK
        numeric amount
        string status
        string provider_payment_id UK
        string idempotency_key UK
    }
    webhook_events {
        int id PK
        string event_id UK
        string provider_payment_id
        string status
        jsonb payload
        string result
        int attempts
        timestamptz processed_at
        timestamptz next_retry_at
    }
```

All tables also have `created_at` / `updated_at`. Schema is managed with Alembic migrations in `alembic/versions/`.

Main decisions:

- **Price lives on `centre_tests`**, not on the test, because the same test costs different amounts at different centres. `CHECK (price > 0)`.
- **Bookings have a composite foreign key `(centre_id, test_id) -> centre_tests`**. The database itself refuses a booking for a test the centre doesn't offer.
- **Bookings store their own `amount`**, copied from the price when booked. Later price changes don't affect existing bookings.
- **Only one successful payment per booking**: partial unique index on `payments(booking_id) WHERE status = 'SUCCESS'`. Last line of defence against double charging.
- **No duplicate active bookings**: partial unique index on `(user_id, centre_id, test_id, appointment_at) WHERE status IN ('PENDING', 'CONFIRMED')`. Catches double clicks even when two requests arrive together. Cancelled bookings don't block rebooking.
- **`webhook_events.event_id` is unique**, that's what makes the webhook idempotent.
- **Deletes are `RESTRICT`** for users -> bookings -> payments. Booking and payment history shouldn't disappear as a side effect. Offerings are disabled with `is_active` instead of deleted.
- **Money is `NUMERIC(10,2)`** and `Decimal` in Python, returned as strings (`"350.00"`) so there's no float rounding.
- **Statuses are varchar + CHECK constraint** rather than native Postgres enums, easier to change in a migration.
- All timestamps are `timestamptz`, stored in UTC.

### Booking status

```
PENDING   -> CONFIRMED, FAILED, CANCELLED
FAILED    -> CONFIRMED (payment retried), CANCELLED
CONFIRMED -> (final)
CANCELLED -> (final)
```

Every status change goes through one function (`change_status` in `app/services/bookings.py`), used by bookings, payments, the webhook and the background jobs. Anything else returns 409.

## How payments and the webhook work

`POST /payments/` with `simulate: success | failure` settles the payment immediately. `simulate: pending` behaves like a real gateway: the payment stays PENDING until the provider calls the webhook.

```
POST /payments/
  lock the booking row (SELECT ... FOR UPDATE)
  checks: owner, not already paid/cancelled, appointment in the future, no other payment in progress
  create payment with provider_payment_id, amount copied from the booking
  success/failure: update payment + booking in the same transaction

POST /payments/webhook/
  1. verify HMAC signature on the raw bytes                     401 if wrong
  2. validate body                                              422 if wrong
  3. unknown provider_payment_id                                404, not stored, provider can retry
  4. INSERT webhook_events ... ON CONFLICT (event_id) DO NOTHING, commit
       already there                                            200 "duplicate"
  5. process in a new transaction:
       lock event, booking, payment
       payment already final -> don't touch it                  200 "ignored: ..."
       else payment -> SUCCESS/FAILED, booking -> CONFIRMED/FAILED   200 "processed"
     if processing crashes:
       event stays stored, attempts + 1, next_retry_at set       202 "queued for retry"

Celery beat, every 30s:
  retry events with processed_at IS NULL and next_retry_at <= now (FOR UPDATE SKIP LOCKED)
  backoff 30s, 60s, 2m, 4m. Gives up after 5 attempts and keeps the error for manual review.
```

Why the webhook can't corrupt state:

1. **Same event twice**: the unique `event_id` + `ON CONFLICT DO NOTHING` means only one copy is ever stored. If two copies arrive at the same moment, the second waits on the unique index and then inserts nothing.
2. **Different event, payment already final**: a final status is never overwritten, so a late `FAILED` can't undo a `CONFIRMED` booking.
3. **Row locks in a fixed order** (event, booking, payment) so concurrent requests are serialised without deadlocks.
4. **Database constraints** as a backstop (one successful payment per booking).
5. Duplicates and ignored events return **200**, because providers keep retrying on errors.

On the client side, `POST /payments/` accepts an `Idempotency-Key` header. Sending the same key again returns the original payment (200) instead of charging again, which covers a client retrying after a network timeout.

## Edge cases handled

| Case | Response |
|---|---|
| Invalid body, bad email, weak password, bad date, date without timezone | 422 |
| Wrong login (email or password, same message and similar timing) | 401 |
| Missing, expired, tampered or `alg: none` token | 401 |
| Normal user calling an admin endpoint | 403 |
| Another user's booking (read, cancel, pay) | 404, so booking ids can't be probed |
| Booking id / centre id that doesn't exist | 404 |
| Test not offered at that centre, or disabled | 404 |
| Appointment in the past or more than 90 days ahead | 400 |
| Same booking twice (double click) | 409 |
| `amount` or `status` sent by the client | ignored, server decides |
| Pay for a confirmed / cancelled / past booking | 409 / 409 / 400 |
| Second payment while one is in progress | 409 |
| Cancel while a payment is in progress | 409 |
| Cancel a confirmed booking | 409 (would need refunds) |
| Payment failed | booking FAILED, user can pay again, every attempt kept |
| Same `Idempotency-Key` again / for another booking | same payment (200) / 409 |
| Webhook: bad signature / body changed after signing | 401 |
| Webhook: same event again | 200 duplicate |
| Webhook: late FAILED after SUCCESS | 200 ignored, booking stays CONFIRMED |
| Webhook: unknown payment | 404 |
| Webhook: processing crashes | 202, retried in background |
| Duplicate centre / test / offering / email | 409 |
| Too many login / signup / payment requests | 429 with `Retry-After` |
| Unexpected server error | 500 with only a request id, details in the logs |

## Bonus features

- **Docker & docker-compose**: api, worker, postgres, redis. Non-root image, healthchecks, migrations and sample data on startup.
- **Swagger / OpenAPI** at `/docs`.
- **Tests**: 121 tests including concurrency tests, see below.
- **Celery background jobs**: webhook retries with exponential backoff, and cancelling unpaid bookings once their appointment time has passed.
- **Retry handling for webhook processing**: inbox pattern described above.
- **Redis caching** of the public centre/test endpoints (`X-Cache: HIT/MISS` header). Invalidated on any admin change by bumping a version number that's part of every cache key.
- **Rate limiting** with Redis: login 10/min, signup 5/min, payments 20/min per IP.
- **Structured logging**: one JSON object per line, with a request id on every line. The id is returned in `X-Request-ID` (or taken from the caller's header).
- **Pagination** on all list endpoints.
- **CI** with GitHub Actions: lint (ruff), tests against real Postgres and Redis, docker build.

If Redis is down the API keeps working: requests just aren't cached or rate limited, and a warning is logged.

## Tests

```bash
docker compose up -d db redis
pip install -r requirements-dev.txt
pytest                                   # 121 tests, ~40s
pytest --cov=app --cov-report=term-missing
```

Tests use a separate `eve_test` database (created automatically) and build it with the real Alembic migrations, so migrations are tested too. Tables are truncated between tests.

| File | Covers |
|---|---|
| `test_auth.py` | signup, validation, login, token edge cases |
| `test_centres.py` | pagination, filters, admin-only writes, duplicates, prices |
| `test_bookings.py` | status rules, create, validation, ownership, cancel |
| `test_payments.py` | success, failure and retry, idempotency key, blocked cases |
| `test_webhook.py` | processed, duplicates, late events, signature, bad payloads |
| `test_concurrency.py` | parallel requests (threads started together with a barrier) |
| `test_background_jobs.py` | webhook retries, backoff, giving up, expiring bookings |
| `test_rate_limit_and_cache.py`, `test_store.py` | rate limits, caching and invalidation, memory and Redis store |
| `test_logging.py` | request ids, JSON log format, 500 handling |

The concurrency tests fire 10 identical webhooks, 10 payments for one booking, 10 payments with the same idempotency key, and cancel + pay at the same moment, and check that exactly one wins every time. I also checked the important tests actually catch bugs by breaking the code on purpose (removing the duplicate check, allowing final statuses to be overwritten, removing the row lock): each time the relevant tests failed.

Coverage is about 91%. Services, auth, bookings and payments are at 100%. The uncovered lines are mostly the dev scripts.

## Project structure

```
app/
  main.py            app setup, routers, middleware
  config.py          settings from env
  database.py        engine, session
  worker.py          celery app and scheduled jobs
  core/              security (jwt, bcrypt), auth dependencies, errors,
                     logging, request middleware, redis store, cache, rate limit
  models/            SQLAlchemy models
  schemas/           pydantic request/response models
  routers/           endpoints, kept thin
  services/          business logic: bookings (status rules), payments (payments + webhook)
  scripts/           seed data, create admin, send a signed test webhook
alembic/             migrations
tests/
```

Routers only handle HTTP. The rules (status transitions, locking, idempotency) are in `services/`, so the webhook, the payment endpoint and the background jobs all share the same code.

## Assumptions

- The payment provider is simulated. `simulate` in the request decides the outcome, so every path can be tested without randomness.
- A final payment status never changes. A real "success after failure" (money actually taken) would need a refund or manual review, so it's logged as a warning and stored as ignored.
- No refunds, so a CONFIRMED booking can't be cancelled.
- No capacity per slot: different users can book the same centre/test/time. The same user can't book the same slot twice.
- Appointments can be booked up to 90 days ahead.
- Centres and tests are public to browse. Only admins can change them. There's no signup route for admins, they're created with a script.
- The webhook is authenticated by an HMAC signature with a shared secret. There's no timestamp check, since replaying an old event is harmless because of the idempotency.
- Rate limits are per client IP as seen by the app (no proxy in front).
- Unpaid bookings are only cancelled automatically once their appointment time has passed.

## What I would improve with more time

- **Refunds and cancelling paid bookings**, with a REFUNDED status and a refund call to the provider.
- **Slot capacity** per centre (max bookings per time slot) using a slots table and row locks.
- **Real payment gateway** integration (e.g. Razorpay), keeping the same webhook design, plus a periodic job that asks the provider about payments stuck in PENDING.
- **Refresh tokens and logout** (token revocation list in Redis). Right now tokens are only short lived.
- **Timestamp in the webhook signature** to reject very old requests.
- **Rate limiting behind a proxy**: read the client IP from `X-Forwarded-For` only when it comes from a trusted proxy. Sliding window instead of fixed window (fixed windows allow a burst at the boundary).
- **Admin views** for payments and failed webhook events, with a button to retry.
- **Metrics and tracing** (Prometheus, OpenTelemetry), and alerts when webhook events give up.
- **UUIDs or other non-sequential ids** in public URLs.
- **Async SQLAlchemy** if the load needed it. Sync was simpler and FastAPI runs sync endpoints in a thread pool.
