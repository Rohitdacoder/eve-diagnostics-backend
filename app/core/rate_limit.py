import time

from fastapi import HTTPException, Request, status

from app.config import settings
from app.core.store import store


def _now() -> int:
    return int(time.time())


def rate_limit(name: str, limit: int, window: int = 60):
    """Dependency: at most `limit` requests per `window` seconds per client IP.

    Fixed window counter, one key per (name, ip, window number).
    """

    def dependency(request: Request):
        if not settings.rate_limit_enabled:
            return
        ip = request.client.host if request.client else "unknown"
        now = _now()
        window_no = now // window
        count = store.incr(f"rl:{name}:{ip}:{window_no}", ttl=window)
        if count > limit:
            retry_after = window - (now % window)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, try again later",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency
