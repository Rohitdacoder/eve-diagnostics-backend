"""Small key-value store used for rate limiting and caching.

Uses Redis when REDIS_URL is set, otherwise an in-memory dict (fine for
local dev and tests, but not shared between workers). If Redis goes down
the calls log a warning and behave as "no data", so the API keeps working.
"""
import logging
import threading
import time

import redis

from app.config import settings

log = logging.getLogger(__name__)


class MemoryStore:
    def __init__(self):
        self._data: dict[str, tuple[str, float | None]] = {}
        self._lock = threading.Lock()

    def _alive(self, key):
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires < time.monotonic():
            del self._data[key]
            return None
        return value

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._alive(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        with self._lock:
            self._data[key] = (value, time.monotonic() + ttl)

    def incr(self, key: str, ttl: int | None = None) -> int:
        with self._lock:
            current = self._alive(key)
            value = int(current or 0) + 1
            expires = self._data[key][1] if current is not None else None
            if current is None and ttl:
                expires = time.monotonic() + ttl
            self._data[key] = (str(value), expires)
            return value

    def clear(self):
        with self._lock:
            self._data.clear()


class RedisStore:
    def __init__(self, url: str):
        self._r = redis.Redis.from_url(url, decode_responses=True, socket_timeout=0.5)

    def get(self, key):
        try:
            return self._r.get(key)
        except redis.RedisError as e:
            log.warning("redis get failed: %s", e)
            return None

    def set(self, key, value, ttl):
        try:
            self._r.set(key, value, ex=ttl)
        except redis.RedisError as e:
            log.warning("redis set failed: %s", e)

    def incr(self, key, ttl=None):
        try:
            pipe = self._r.pipeline()
            pipe.incr(key)
            if ttl:
                # nx: only set the expiry when the key is new, so the window doesn't slide
                pipe.expire(key, ttl, nx=True)
            return pipe.execute()[0]
        except redis.RedisError as e:
            log.warning("redis incr failed: %s", e)
            return 0

    def clear(self):
        self._r.flushdb()


store = RedisStore(settings.redis_url) if settings.redis_url else MemoryStore()
