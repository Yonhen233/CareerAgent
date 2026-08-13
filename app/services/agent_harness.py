from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.agents.skills import get_skill_registry
from app.agents.subagents import list_agent_roles
from app.agents.tools import list_agent_tools
from app.core.config import Settings, get_settings


HARNESS_VERSION = "careeragent-harness-v3"


@dataclass(frozen=True)
class HarnessReadinessCheck:
    name: str
    passed: bool
    required_in_production: bool
    detail: str


class AgentHarnessService:
    """Describe and enforce the non-model runtime surrounding CareerAgent graphs."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def manifest(self) -> dict[str, Any]:
        checks = self.readiness_checks()
        production_failures = [
            item for item in checks if item.required_in_production and not item.passed
        ]
        return {
            "version": HARNESS_VERSION,
            "architecture": "domain_bounded_agent_harness",
            "environment": self.settings.app_env,
            "components": {
                "brain": "LLM planner/generator/judge behind explicit budgets and schemas",
                "control_plane": "LangGraph state machine, task contract, completion and quality gates",
                "hands": "bound tools with capability, approval, timeout, retry and audit policy",
                "context": "progressive disclosure and budgeted evidence packets",
                "memory": "checkpoint state, business artifacts and scoped user memory",
                "durability": self.settings.langgraph_checkpoint_backend,
                "observability": "steps, events, artifacts, LLM call logs and online quality review",
                "evaluation": "offline suites, release gates, SLO probes and bad-case regression",
            },
            "inventory": {
                "tool_count": len(list_agent_tools()),
                "skill_count": len(get_skill_registry().list()),
                "role_count": len(list_agent_roles()),
            },
            "readiness": {
                "production_ready": not production_failures,
                "checks": [asdict(item) for item in checks],
            },
        }

    def readiness_checks(self) -> list[HarnessReadinessCheck]:
        settings = self.settings
        return [
            HarnessReadinessCheck(
                "shared_business_database",
                not settings.database_url.lower().startswith("sqlite"),
                True,
                "生产多实例必须共享业务数据库；SQLite 仅用于本地开发和单机评测。",
            ),
            HarnessReadinessCheck(
                "shared_langgraph_checkpointer",
                settings.langgraph_checkpoint_backend.lower() == "postgres"
                and bool(settings.langgraph_checkpoint_postgres_dsn),
                True,
                "生产跨 worker 恢复要求 PostgreSQL checkpointer；SQLite 仅保证单机持久化。",
            ),
            HarnessReadinessCheck(
                "external_queue",
                settings.redis_enabled,
                True,
                "生产运行要求 Redis 队列、分布式锁、heartbeat、恢复扫描和 DLQ。",
            ),
            HarnessReadinessCheck(
                "tenant_rbac",
                settings.rbac_enabled and not settings.rbac_trusted_header_auth,
                True,
                "生产环境必须启用 session/OIDC 身份与 tenant RBAC，不能信任调用方伪造 Header。",
            ),
            HarnessReadinessCheck(
                "session_secret",
                bool(settings.session_secret_key) and settings.session_secret_key != "dev-change-me",
                True,
                "生产会话密钥不能使用开发默认值。",
            ),
            HarnessReadinessCheck(
                "strict_tool_contracts",
                settings.agent_strict_tool_contracts,
                True,
                "未登记工具必须 fail closed。",
            ),
            HarnessReadinessCheck(
                "prompt_injection_gate",
                settings.prompt_injection_classifier_enabled,
                True,
                "外部 JD/PDF/网页内容进入模型上下文前必须经过注入检测。",
            ),
        ]

    def assert_production_ready(self) -> None:
        if self.settings.app_env.lower() not in {"production", "prod"}:
            return
        failed = [
            item for item in self.readiness_checks() if item.required_in_production and not item.passed
        ]
        if failed:
            details = "; ".join(f"{item.name}: {item.detail}" for item in failed)
            raise RuntimeError(f"Agent Harness production readiness gate failed: {details}")
