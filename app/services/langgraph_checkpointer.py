from __future__ import annotations

from typing import Any

import aiosqlite

from app.core.config import Settings, get_settings


class LangGraphCheckpointerLifecycle:
    """Open the configured durable checkpoint backend and own its lifetime."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._sqlite_connection: aiosqlite.Connection | None = None
        self._postgres_context: Any = None
        self.saver: Any = None

    async def open(self):
        if self.saver is not None:
            return self.saver
        backend = self.settings.langgraph_checkpoint_backend.lower().strip()
        if backend == "sqlite":
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            path = self.settings.langgraph_checkpoint_path
            path.parent.mkdir(parents=True, exist_ok=True)
            self._sqlite_connection = await aiosqlite.connect(str(path))
            await self._sqlite_connection.execute("PRAGMA busy_timeout=30000")
            await self._sqlite_connection.execute("PRAGMA journal_mode=WAL")
            await self._sqlite_connection.execute("PRAGMA synchronous=NORMAL")
            self.saver = AsyncSqliteSaver(self._sqlite_connection)
        elif backend == "postgres":
            dsn = self.settings.langgraph_checkpoint_postgres_dsn
            if not dsn:
                raise RuntimeError("LANGGRAPH_CHECKPOINT_POSTGRES_DSN is required for the postgres backend.")
            try:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            except ImportError as exc:
                raise RuntimeError(
                    "The postgres checkpoint backend requires langgraph-checkpoint-postgres and psycopg."
                ) from exc
            self._postgres_context = AsyncPostgresSaver.from_conn_string(dsn)
            self.saver = await self._postgres_context.__aenter__()
        else:
            raise RuntimeError(f"Unsupported LangGraph checkpoint backend: {backend}.")
        await self.saver.setup()
        return self.saver

    async def close(self) -> None:
        if self._postgres_context is not None:
            await self._postgres_context.__aexit__(None, None, None)
        if self._sqlite_connection is not None:
            await self._sqlite_connection.close()
        self._postgres_context = None
        self._sqlite_connection = None
        self.saver = None
