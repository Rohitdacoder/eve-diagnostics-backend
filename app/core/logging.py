import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from app.config import settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# attributes every LogRecord has; anything else was passed with extra={...}
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            data["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                data[key] = value
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get() or "-"
        return True


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s")
        )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    # we log every request ourselves with more detail
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
