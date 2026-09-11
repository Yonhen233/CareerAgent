import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core import redis_client
from app.core.config import get_settings
from app.services.trace_service import TraceService


def _reset_redis_state() -> None:
    redis_client.get_redis_client.cache_clear()
    redis_client.mark_redis_unavailable("test reset", cooldown_seconds=0)
    redis_client.get_redis_client.cache_clear()


def test_redis_connection_failure_enters_short_circuit(monkeypatch):
    import redis

    monkeypatch.setenv("REDIS_ENABLED", "true")
    monkeypatch.setenv("REDIS_MODE", "standalone")
    monkeypatch.setenv("REDIS_FAILURE_COOLDOWN_SECONDS", "15")
    get_settings.cache_clear()

    class BrokenRedis:
        ping_calls = 0

        @classmethod
        def from_url(cls, *args, **kwargs):
            return cls()

        def ping(self):
            type(self).ping_calls += 1
            raise OSError("connection refused")

    monkeypatch.setattr(redis, "Redis", BrokenRedis)
    redis_client.get_redis_client.cache_clear()
    started = time.perf_counter()
    with pytest.raises(redis_client.RedisUnavailableError):
        redis_client.get_redis_client(socket_timeout_seconds=0.05)
    with pytest.raises(redis_client.RedisUnavailableError, match="failure cooldown"):
        redis_client.get_redis_client(socket_timeout_seconds=0.05)

    assert BrokenRedis.ping_calls == 1
    assert time.perf_counter() - started < 1.0
    _reset_redis_state()
    get_settings.cache_clear()


def test_trace_event_publish_absorbs_transport_failure(monkeypatch):
    monkeypatch.setenv("REDIS_ENABLED", "true")
    get_settings.cache_clear()

    class BrokenRedis:
        def publish(self, channel, message):
            raise OSError("connection reset")

    monkeypatch.setattr(
        "app.services.trace_service.get_redis_client",
        lambda **kwargs: BrokenRedis(),
    )
    event = SimpleNamespace(
        id=1,
        run_id=1,
        event_type="test",
        node_name="test",
        event_json={},
        created_at=datetime.now(timezone.utc),
    )

    started = time.perf_counter()
    TraceService()._publish_event(event)

    assert time.perf_counter() - started < 1.0
    _reset_redis_state()
    get_settings.cache_clear()
