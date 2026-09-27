import logging
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.logging import request_id_var

log = logging.getLogger("app.request")


async def request_context(request: Request, call_next):
    # reuse the caller's id if it sent one, so logs can be matched across services
    request_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error")
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "request_id": request_id},
            )
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "%s %s %s",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
    finally:
        request_id_var.reset(token)
