from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol

from app.core.config import get_settings


class RedisUnavailableError(RuntimeError):
    pass


class RedisLike(Protocol):
    def ping(self) -> Any: ...
    def lpush(self, name: str, value: Any) -> Any: ...
    def brpop(self, keys: str | list[str], timeout: int = 0) -> Any: ...
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
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001
        raise RedisUnavailableError(f"Redis is unavailable at {settings.redis_url}: {exc}") from exc


def redis_key(*parts: object) -> str:
    return ":".join(str(part).strip(":") for part in parts if str(part))
