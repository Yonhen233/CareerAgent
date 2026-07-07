from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol

from app.core.config import get_settings


class RedisUnavailableError(RuntimeError):
    pass


class RedisLike(Protocol):
    def ping(self) -> Any: ...
    def lpush(self, name: str, value: Any) -> Any: ...
    def rpush(self, name: str, value: Any) -> Any: ...
    def brpop(self, keys: str | list[str], timeout: int = 0) -> Any: ...
    def llen(self, name: str) -> Any: ...
    def lrange(self, name: str, start: int, end: int) -> Any: ...
    def lrem(self, name: str, count: int, value: Any) -> Any: ...
    def set(self, name: str, value: Any, nx: bool = False, ex: int | None = None) -> Any: ...
    def get(self, name: str) -> Any: ...
    def delete(self, *names: str) -> Any: ...
    def publish(self, channel: str, message: str) -> Any: ...
    def incr(self, name: str) -> Any: ...
    def expire(self, name: str, time: int) -> Any: ...


@lru_cache(maxsize=1)
def get_redis_client() -> RedisLike:
    settings = get_settings()
    if not settings.redis_enabled:
        raise RedisUnavailableError("Redis is disabled. Set REDIS_ENABLED=true to use Redis coordination.")
    try:
        import redis
    except Exception as exc:  # noqa: BLE001
        raise RedisUnavailableError("redis package is not installed. Install redis>=5 to enable Redis.") from exc
    try:
        if settings.redis_mode.lower() == "sentinel":
            sentinel = redis.sentinel.Sentinel(
                settings.redis_sentinel_endpoints,
                socket_timeout=settings.redis_socket_timeout_seconds,
                decode_responses=True,
            )
            client = sentinel.master_for(
                settings.redis_sentinel_master_name,
                socket_timeout=settings.redis_socket_timeout_seconds,
                decode_responses=True,
            )
        elif settings.redis_mode.lower() == "standalone":
            client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=settings.redis_socket_timeout_seconds,
            )
        else:
            raise RedisUnavailableError(f"Unsupported REDIS_MODE={settings.redis_mode}.")
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001
        target = (
            f"sentinel:{settings.redis_sentinel_master_name}@{settings.redis_sentinel_urls}"
            if settings.redis_mode.lower() == "sentinel"
            else settings.redis_url
        )
        raise RedisUnavailableError(f"Redis is unavailable at {target}: {exc}") from exc


def redis_key(*parts: object) -> str:
    return ":".join(str(part).strip(":") for part in parts if str(part))
