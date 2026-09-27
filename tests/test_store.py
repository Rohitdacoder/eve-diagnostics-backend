import time

import pytest
import redis

from app.core.store import MemoryStore, RedisStore

TEST_REDIS = "redis://localhost:6379/15"


def _redis_available():
    try:
        return redis.Redis.from_url(TEST_REDIS, socket_timeout=0.5).ping()
    except redis.RedisError:
        return False


@pytest.fixture(params=["memory", "redis"])
def store(request):
    if request.param == "memory":
        s = MemoryStore()
    else:
        if not _redis_available():
            pytest.skip("redis not running")
        s = RedisStore(TEST_REDIS)
    s.clear()
    yield s
    s.clear()


def test_get_set(store):
    assert store.get("a") is None
    store.set("a", "1", ttl=10)
    assert store.get("a") == "1"


def test_incr_counts(store):
    assert [store.incr("c", ttl=10) for _ in range(3)] == [1, 2, 3]


def test_values_expire(store):
    store.set("a", "1", ttl=1)
    store.incr("c", ttl=1)
    time.sleep(1.2)
    assert store.get("a") is None
    assert store.incr("c", ttl=1) == 1


def test_incr_does_not_extend_expiry(store):
    store.incr("c", ttl=1)
    time.sleep(0.6)
    store.incr("c", ttl=1)
    time.sleep(0.6)
    # first expiry still applies, so the counter starts over
    assert store.incr("c", ttl=1) == 1


def test_redis_down_does_not_break_anything():
    s = RedisStore("redis://localhost:1/0")  # nothing listens here
    assert s.get("a") is None
    s.set("a", "1", ttl=10)
    assert s.incr("c", ttl=10) == 0
