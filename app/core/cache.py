"""Cache for the public catalog (centres and tests).

Every key includes a version number. Any admin change bumps the version,
so all old entries are skipped at once instead of deleting keys one by one.
Old entries just expire on their own.
"""
from app.config import settings
from app.core.store import store

VERSION_KEY = "catalog:version"


def _key(name: str) -> str:
    version = store.get(VERSION_KEY) or "0"
    return f"cache:v{version}:{name}"


def get(name: str) -> str | None:
    if not settings.cache_enabled:
        return None
    return store.get(_key(name))


def set(name: str, value: str) -> None:
    if settings.cache_enabled:
        store.set(_key(name), value, settings.cache_ttl_seconds)


def invalidate_catalog() -> None:
    store.incr(VERSION_KEY)
