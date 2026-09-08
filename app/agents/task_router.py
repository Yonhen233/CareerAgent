from __future__ import annotations

"""任务意图到受限任务图的路由器。

路由器只选择允许的任务类型和职责范围，不直接执行工具；这样可以把“理解
用户想做什么”和“系统允许调用什么”分开审计。
"""

from collections.abc import Awaitable, Callable
from typing import Any

from app.models.agent_plan_schemas import Action


class TaskRouteError(RuntimeError):
    pass


class TaskRouter:
    """Closed-world action router. A plan can select only registered routes."""

    def __init__(self, routes: dict[str, Callable[..., Any]] | None = None) -> None:
        self.routes = dict(routes or {})

    def register(self, action: Action, handler: Callable[..., Any]) -> None:
        if not callable(handler):
            raise TypeError(f"Route handler for {action} must be callable")
        self.routes[action] = handler

    async def dispatch(self, action: str, **kwargs: Any) -> Any:
        handler = self.routes.get(action)
        if handler is None:
            raise TaskRouteError(f"No registered route for action: {action}")
        result = handler(**kwargs)
        if isinstance(result, Awaitable):
            return await result
        return result
