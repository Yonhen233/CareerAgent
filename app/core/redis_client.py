from __future__ import annotations

from functools import lru_cache
import re
import threading
import time
from typing import Any, Protocol

from app.core.config import get_settings


class RedisUnavailableError(RuntimeError):
    pass


_FAILURE_LOCK = threading.Lock()
_FAILURE_UNTIL = 0.0
_FAILURE_MESSAGE = ""


class RedisLike(Protocol):
    def ping(self) -> Any: ...
    def lpush(self, name: str, value: Any) -> Any: ...
    def rpush(self, name: str, value: Any) -> Any: ...
    def brpop(self, keys: str | list[str], timeout: int = 0) -> Any: ...
    def llen(self, name: str) -> Any: ...
    def lrange(self, name: str, start: int, end: int) -> Any: ...
    def lrem(self, name: str, count: int, value: Any) -> Any: ...
    def set(
        self,
        name: str,
        value: Any,
        nx: bool = False,
        ex: int | None = None,
        px: int | None = None,
    ) -> Any: ...
    def get(self, name: str) -> Any: ...
    def delete(self, *names: str) -> Any: ...
    def publish(self, channel: str, message: str) -> Any: ...
    def incr(self, name: str) -> Any: ...
    def expire(self, name: str, time: int) -> Any: ...


@lru_cache(maxsize=4)
def get_redis_client(*, socket_timeout_seconds: float | None = None) -> RedisLike:
    global _FAILURE_UNTIL, _FAILURE_MESSAGE
    settings = get_settings()
    if not settings.redis_enabled:
        raise RedisUnavailableError("Redis is disabled. Set REDIS_ENABLED=true to use Redis coordination.")
    now = time.monotonic()
    with _FAILURE_LOCK:
        if _FAILURE_UNTIL > now:
            remaining = round(_FAILURE_UNTIL - now, 2)
            raise RedisUnavailableError(
                f"Redis is in failure cooldown for {remaining}s: {_FAILURE_MESSAGE or 'connection failed'}"
            )
    try:
        import redis
    except Exception as exc:  # noqa: BLE001
        raise RedisUnavailableError("redis package is not installed. Install redis>=5 to enable Redis.") from exc
    try:
        socket_timeout = socket_timeout_seconds or settings.redis_socket_timeout_seconds
        if settings.redis_mode.lower() == "sentinel":
            sentinel = redis.sentinel.Sentinel(
                settings.redis_sentinel_endpoints,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_timeout,
                decode_responses=True,
            )
            client = sentinel.master_for(
                settings.redis_sentinel_master_name,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_timeout,
                decode_responses=True,
            )
        elif settings.redis_mode.lower() == "standalone":
            client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_timeout,
            )
        else:
            raise RedisUnavailableError(f"Unsupported REDIS_MODE={settings.redis_mode}.")
        client.ping()
    except Exception as exc:  # noqa: BLE001
        safe_urls = re.sub(r"//[^/@]+@", "//***@", settings.redis_sentinel_urls)
        safe_url = re.sub(r"//[^/@]+@", "//***@", settings.redis_url)
        target = (
            f"sentinel:{settings.redis_sentinel_master_name}@{safe_urls}"
            if settings.redis_mode.lower() == "sentinel"
            else safe_url
        )
        message = f"Redis is unavailable at {target}: {exc}"
        with _FAILURE_LOCK:
            _FAILURE_MESSAGE = message
            _FAILURE_UNTIL = time.monotonic() + settings.redis_failure_cooldown_seconds
        get_redis_client.cache_clear()
        raise RedisUnavailableError(message) from exc
    else:
        with _FAILURE_LOCK:
            _FAILURE_MESSAGE = ""
            _FAILURE_UNTIL = 0.0
        return client


def mark_redis_unavailable(exc: Exception | str, *, cooldown_seconds: float | None = None) -> None:
    """Open the shared short-lived Redis circuit after a transport failure."""

    global _FAILURE_UNTIL, _FAILURE_MESSAGE
    settings = get_settings()
    cooldown = settings.redis_failure_cooldown_seconds if cooldown_seconds is None else cooldown_seconds
    with _FAILURE_LOCK:
        _FAILURE_MESSAGE = str(exc)
        _FAILURE_UNTIL = time.monotonic() + max(0.0, float(cooldown))
    get_redis_client.cache_clear()


def is_redis_transport_error(exc: Exception) -> bool:
    """Return whether an exception is safe to absorb for best-effort events."""

    if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        return True
    try:
        import redis

        return isinstance(exc, redis.exceptions.RedisError)
    except Exception:
        return False


def redis_key(*parts: object) -> str:
    return ":".join(str(part).strip(":") for part in parts if str(part))
