import json
import logging

from app.core.logging import JsonFormatter, request_id_var


def test_request_id_is_generated_and_returned(client):
    r = client.get("/health")
    assert len(r.headers["X-Request-ID"]) == 32


def test_request_id_from_caller_is_kept(client):
    r = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"


def test_request_id_on_error_responses(client):
    r = client.get("/centres/9999", headers={"X-Request-ID": "err-1"})
    assert r.status_code == 404
    assert r.headers["X-Request-ID"] == "err-1"


def test_every_request_is_logged_with_details(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.request"):
        client.get("/centres/9999", headers={"X-Request-ID": "req-42"})

    record = next(r for r in caplog.records if r.name == "app.request")
    assert record.status == 404
    assert record.path == "/centres/9999"
    assert record.method == "GET"
    assert record.duration_ms >= 0


def test_json_formatter_includes_request_id_and_extra_fields():
    token = request_id_var.set("req-7")
    try:
        record = logging.makeLogRecord(
            {"name": "app.x", "levelname": "INFO", "msg": "paid %s", "args": ("b1",), "booking_id": 5}
        )
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert line["msg"] == "paid b1"
    assert line["request_id"] == "req-7"
    assert line["booking_id"] == 5
    assert line["level"] == "INFO"


def test_unhandled_error_returns_500_with_request_id(offering, monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import centres

    def boom(*args, **kwargs):
        raise RuntimeError("something broke")

    monkeypatch.setattr(centres, "_offering_out", boom)

    with caplog.at_level(logging.ERROR, logger="app.request"):
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get(f"/centres/{offering.centre_id}", headers={"X-Request-ID": "boom-1"})

    assert r.status_code == 500
    # no internal details leak to the client
    assert r.json() == {"detail": "Internal server error", "request_id": "boom-1"}
    assert r.headers["X-Request-ID"] == "boom-1"
    assert any("something broke" in (rec.exc_text or "") or rec.exc_info for rec in caplog.records)
