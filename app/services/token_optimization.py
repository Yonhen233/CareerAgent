from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Literal

from sqlalchemy.orm import Session

from app.agents.skills import get_skill_registry
from app.agents.tools import list_agent_tools
from app.core.config import get_settings
from app.models.entities import AgentArtifact, AgentStep, LLMCallLog
from app.services.context_runtime import TokenEstimator


class TokenOptimizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class NodeTokenBudget:
    node: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    forbidden_fields: tuple[str, ...]
    skill: str
    tools: tuple[str, ...]
    evidence_types: tuple[str, ...]
    max_input_tokens: int
    output_reserve_tokens: int
    tool_schema_tokens: int
    history_tokens: int
    evidence_tokens: int
    repair_tokens: int


class NodeTokenBudgetRegistry:
    """Deterministic context contracts for LLM-facing workflow nodes."""

    _COMMON_FORBIDDEN = ("raw_pdf", "full_conversation", "all_tool_results", "other_tenant_data")
    CONTRACTS: dict[str, NodeTokenBudget] = {
        "planner": NodeTokenBudget("planner", ("user_goal", "task_contract"), ("profile_ref", "job_ref", "constraints", "tool_catalog"), _COMMON_FORBIDDEN + ("full_profile", "full_job", "all_evidence"), "orchestration", ("LangGraph.AgentPlanner", "llm.intent_planner"), (), 6000, 1200, 500, 500, 0, 900),
        "resume_parser": NodeTokenBudget("resume_parser", ("resume_text",), ("page_map",), _COMMON_FORBIDDEN + ("job",), "resume_intake_and_structuring", (), ("resume",), 14000, 3200, 0, 0, 10000, 1200),
        "jd_parser": NodeTokenBudget("jd_parser", ("jd_text",), ("source_url",), _COMMON_FORBIDDEN + ("profile",), "jd_structuring", ("jd_parser.parse_jd",), ("job",), 10000, 2600, 200, 0, 7500, 1000),
        "matcher": NodeTokenBudget("matcher", ("profile_skills", "job_requirements"), ("top_evidence", "negative_facts"), _COMMON_FORBIDDEN + ("application_history",), "fit_assessment", ("matcher.match_job", "vector_index.retrieve_resume_evidence"), ("resume", "job"), 12000, 1800, 350, 0, 7000, 1000),
        "resume_tailor": NodeTokenBudget("resume_tailor", ("verified_facts", "job_requirements", "current_draft"), ("top_evidence", "gaps", "guardrail_issues"), _COMMON_FORBIDDEN, "resume_tailoring", ("resume_tailor.tailor_resume", "guardrail.verify_resume"), ("resume", "job"), 18000, 5200, 450, 0, 9000, 2200),
        "application_packet": NodeTokenBudget("application_packet", ("approved_resume", "job_ref"), ("contact",), _COMMON_FORBIDDEN, "application_packet", ("application.create_quick_apply_packet",), ("resume", "job"), 7000, 1800, 250, 0, 3000, 700),
        "interview_question_generator": NodeTokenBudget("interview_question_generator", ("job_requirements", "project_topics"), ("reference_titles",), _COMMON_FORBIDDEN, "interview_preparation", ("interview_prep.generate_packet",), ("resume", "job", "interview_experience"), 14000, 5000, 250, 0, 8000, 1200),
        "interview_answer_generator": NodeTokenBudget("interview_answer_generator", ("questions", "minimal_evidence"), ("candidate_summary", "job_summary"), _COMMON_FORBIDDEN, "interview_preparation", ("interview_prep.generate_packet",), ("resume", "job", "technical_knowledge"), 22000, 10000, 250, 0, 15000, 3500),
        "claim_verifier": NodeTokenBudget("claim_verifier", ("claims", "minimal_evidence"), ("question",), _COMMON_FORBIDDEN + ("full_profile", "full_job"), "interview_preparation", (), ("resume", "job", "technical_knowledge"), 16000, 2800, 0, 0, 12000, 1800),
        "guardrail": NodeTokenBudget("guardrail", ("claims", "citations", "negative_facts"), ("draft",), _COMMON_FORBIDDEN, "resume_tailoring", ("guardrail.verify_resume",), ("resume", "job"), 10000, 1600, 200, 0, 6000, 1000),
        "completion_gate": NodeTokenBudget("completion_gate", ("task_contract", "step_receipts"), ("artifacts", "gaps"), _COMMON_FORBIDDEN + ("full_profile", "full_job", "all_evidence"), "orchestration", (), (), 5000, 1000, 0, 300, 0, 500),
    }

    def get(self, node: str) -> NodeTokenBudget:
        try:
            return self.CONTRACTS[node]
        except KeyError as exc:
            raise TokenOptimizationError(f"Missing node token contract: {node}.") from exc

    def validate(self, node: str, sections: dict[str, Any]) -> dict[str, Any]:
        contract = self.get(node)
        present = {key for key, value in sections.items() if value not in (None, "", [], {})}
        missing = sorted(set(contract.required_fields) - present)
        forbidden = sorted(set(contract.forbidden_fields) & present)
        return {"passed": not missing and not forbidden, "missing": missing, "forbidden": forbidden}


class ScopedVersionedCache:
    """Tenant-scoped read cache; side-effect receipts remain outside this cache."""

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    @staticmethod
    def key(*, tenant_id: str, user_id: str, data_version: str, tool_version: str, prompt_version: str, contract_version: str, model: str, params: Any) -> str:
        payload = {"tenant_id": tenant_id, "user_id": user_id, "data_version": data_version, "tool_version": tool_version, "prompt_version": prompt_version, "contract_version": contract_version, "model": model, "params": params}
        return DeltaContextBuilder._hash(payload)

    def get(self, key: str) -> Any | None:
        return self._values.get(key)

    def put(self, key: str, value: Any, *, read_only: bool) -> None:
        if not read_only:
            raise TokenOptimizationError("Side-effect tool results cannot use ScopedVersionedCache.")
        self._values[key] = value

    def invalidate(self, key: str) -> None:
        self._values.pop(key, None)


class BatchExecutionError(TokenOptimizationError):
    def __init__(self, message: str, results: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.results = results


@dataclass(frozen=True)
class PromptSection:
    name: str
    tokens: int
    token_method: str
    estimated: bool
    chars: int
    sha256: str


class PromptSectionProfiler:
    SECTION_NAMES = (
        "system_control",
        "task_contract",
        "skill_instructions",
        "tool_schemas",
        "profile",
        "job",
        "evidence",
        "conversation_history",
        "memory",
        "tool_observations",
        "repair_context",
        "output_schema",
        "working",
    )

    def __init__(self, estimator: TokenEstimator | None = None) -> None:
        self.estimator = estimator or TokenEstimator()

    def profile(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        skill_policy_chars: int = 0,
        response_format: dict[str, Any] | None = None,
        explicit_sections: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sections: dict[str, Any] = {}
        if explicit_sections:
            unknown = set(explicit_sections) - set(self.SECTION_NAMES)
            if unknown:
                raise TokenOptimizationError(f"Unknown Prompt sections: {sorted(unknown)}")
            sections.update(explicit_sections)
        else:
            sections.update(self._infer_user_sections(user_prompt))

        skill_chars = max(0, min(skill_policy_chars, len(system_prompt)))
        if skill_chars:
            sections.setdefault("system_control", system_prompt[:-skill_chars])
            sections.setdefault("skill_instructions", system_prompt[-skill_chars:])
        else:
            sections.setdefault("system_control", system_prompt)
        if response_format:
            sections.setdefault("output_schema", response_format)
        sections.setdefault("working", user_prompt if not explicit_sections else "")

        rows = [self._section(name, value) for name, value in sections.items() if self._has_content(value)]
        return {
            "version": "careeragent-prompt-sections-v2",
            "sections": {row.name: asdict(row) for row in rows},
            "total_section_tokens": sum(row.tokens for row in rows),
            "all_tokens_estimated": all(row.estimated for row in rows),
        }

    def _infer_user_sections(self, user_prompt: str) -> dict[str, Any]:
        try:
            parsed = json.loads(user_prompt)
        except (json.JSONDecodeError, TypeError):
            return {"working": user_prompt}
        if not isinstance(parsed, dict):
            return {"working": user_prompt}
        aliases = {
            "profile": "profile",
            "profile_facts": "profile",
            "job": "job",
            "job_requirements": "job",
            "evidence": "evidence",
            "ranked_evidence": "evidence",
            "memory": "memory",
            "memory_context": "memory",
            "history": "conversation_history",
            "conversation": "conversation_history",
            "tool_results": "tool_observations",
            "tool_observations": "tool_observations",
            "repair": "repair_context",
            "verification_errors": "repair_context",
            "task_contract": "task_contract",
            "tool_schemas": "tool_schemas",
        }
        sections: dict[str, Any] = {}
        remainder: dict[str, Any] = {}
        for key, value in parsed.items():
            section = aliases.get(key)
            if section:
                sections[section] = value
            else:
                remainder[key] = value
        if remainder:
            sections["working"] = remainder
        return sections

    def _section(self, name: str, value: Any) -> PromptSection:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        estimate = self.estimator.count(text)
        return PromptSection(
            name=name,
            tokens=estimate.tokens,
            token_method=estimate.method,
            estimated=estimate.estimated,
            chars=len(text),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _has_content(value: Any) -> bool:
        return value not in (None, "", [], {})


@dataclass(frozen=True)
class ToolCatalogSelection:
    task_type: str
    node: str
    full_tool_count: int
    selected_tool_count: int
    compact_catalog: list[dict[str, Any]]
    full_schemas: list[dict[str, Any]]
    full_schema_tokens: int
    injected_schema_tokens: int
    omitted_tools: list[str]
    version: str = "careeragent-dynamic-tool-catalog-v2"


class DynamicToolCatalog:
    NODE_TOOLS: dict[str, set[str]] = {
        "planner": {"LangGraph.AgentPlanner", "llm.intent_planner"},
        "profile": {"profile_repository.load_profile"},
        "job_search": {"job_search.search_jobs", "job_repository.load_job", "jd_parser.parse_jd"},
        "matcher": {"matcher.match_job", "matcher.enforce_fit_gate", "vector_index.retrieve_resume_evidence"},
        "tailor": {"vector_index.retrieve_resume_evidence", "resume_tailor.tailor_resume", "guardrail.verify_resume"},
        "application": {"application.create_quick_apply_packet"},
        "interview": {"interview_prep.generate_packet"},
        "outbound": {"browser_apply", "email_draft", "email_send"},
    }

    def __init__(self, estimator: TokenEstimator | None = None) -> None:
        self.estimator = estimator or TokenEstimator()
        self.skill_registry = get_skill_registry()
        self.all_tools = list_agent_tools()

    def select(
        self,
        *,
        task_type: str,
        node: str,
        max_risk: Literal["low", "medium", "high"] = "medium",
        dependencies_satisfied: set[str] | None = None,
        approved_actions: set[str] | None = None,
        include_full_schema: bool = False,
    ) -> ToolCatalogSelection:
        allowed_by_skill = self.skill_registry.allowed_tools_for_task(task_type)
        node_allowed = self.NODE_TOOLS.get(node, set())
        candidates = allowed_by_skill & node_allowed if node_allowed else allowed_by_skill
        risk_rank = {"low": 0, "medium": 1, "high": 2}
        dependencies_satisfied = dependencies_satisfied or set()
        approved_actions = approved_actions or set()
        selected = []
        for tool in self.all_tools:
            if tool["name"] not in candidates:
                continue
            if risk_rank.get(str(tool["risk_level"]), 2) > risk_rank[max_risk]:
                continue
            approval = str(tool["approval_requirement"])
            if approval != "none" and approval not in approved_actions:
                continue
            selected.append(tool)

        compact = [
            {
                "name": tool["name"],
                "purpose": tool["purpose"],
                "risk_level": tool["risk_level"],
                "allowed_skills": self.skill_registry.skills_for_tool(tool["name"]),
                "input_fields": sorted(tool["input_schema"]),
            }
            for tool in selected
        ]
        full_schema_tokens = self.estimator.count(self.all_tools).tokens
        injected = selected if include_full_schema else compact
        injected_tokens = self.estimator.count(injected).tokens
        selected_names = {tool["name"] for tool in selected}
        return ToolCatalogSelection(
            task_type=task_type,
            node=node,
            full_tool_count=len(self.all_tools),
            selected_tool_count=len(selected),
            compact_catalog=compact,
            full_schemas=selected if include_full_schema else [],
            full_schema_tokens=full_schema_tokens,
            injected_schema_tokens=injected_tokens,
            omitted_tools=sorted({tool["name"] for tool in self.all_tools} - selected_names),
        )


@dataclass(frozen=True)
class BatchItem:
    item_id: str
    payload: Any


@dataclass
class BatchItemResult:
    item_id: str
    status: Literal["completed", "failed"]
    result: Any = None
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    evidence_artifact_ref: dict[str, Any] | None = None
    latency_ms: float = 0.0


class BatchToolExecutor:
    """Execute independent, side-effect-free items with stable per-item receipts."""

    async def run(
        self,
        items: Iterable[BatchItem],
        handler: Callable[[BatchItem], Awaitable[Any]],
        *,
        concurrency: int,
        risk_level: str = "low",
        has_shared_side_effect: bool = False,
        dependencies: dict[str, set[str]] | None = None,
    ) -> list[BatchItemResult]:
        rows = list(items)
        if risk_level == "high" or has_shared_side_effect:
            raise TokenOptimizationError("High-risk or shared-side-effect tools cannot use BatchToolExecutor.")
        dependencies = dependencies or {}
        if any(dependencies.get(item.item_id) for item in rows):
            raise TokenOptimizationError("Dependent tool items must remain sequential.")
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def execute(item: BatchItem) -> BatchItemResult:
            started = time.perf_counter()
            try:
                async with semaphore:
                    value = await handler(item)
                return BatchItemResult(
                    item_id=item.item_id,
                    status="completed",
                    result=value,
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                )
            except Exception as exc:
                return BatchItemResult(
                    item_id=item.item_id,
                    status="failed",
                    error=f"{exc.__class__.__name__}: {exc}",
                    latency_ms=round((time.perf_counter() - started) * 1000, 3),
                )

        return list(await asyncio.gather(*(execute(item) for item in rows)))

    @staticmethod
    def unwrap(results: list[BatchItemResult]) -> list[Any]:
        failures = [asdict(item) for item in results if item.status == "failed"]
        if failures:
            raise BatchExecutionError("One or more batch items failed.", [asdict(item) for item in results])
        return [item.result for item in results]


class ParallelToolObservationAggregator:
    def aggregate(self, results: list[BatchItemResult]) -> dict[str, Any]:
        return {
            "version": "careeragent-parallel-observation-v2",
            "total": len(results),
            "completed": sum(item.status == "completed" for item in results),
            "failed": sum(item.status == "failed" for item in results),
            "items": [
                {
                    "item_id": item.item_id,
                    "status": item.status,
                    "result": self._compact_result(item.result),
                    "error": item.error,
                    "usage": item.usage,
                    "evidence_artifact_ref": item.evidence_artifact_ref,
                }
                for item in results
            ],
        }

    @staticmethod
    def _compact_result(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        allowed = {"status", "count", "metrics", "citations", "artifact_id", "next_step", "result"}
        return {key: item for key, item in value.items() if key in allowed}


class DeltaContextBuilder:
    def build(self, previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        changed: dict[str, Any] = {}
        removed = []
        for key, value in current.items():
            if previous.get(key) != value:
                changed[key] = value
        for key in previous:
            if key not in current:
                removed.append(key)
        return {
            "version": "careeragent-delta-context-v2",
            "changed": changed,
            "removed": sorted(removed),
            "base_sha256": self._hash(previous),
            "current_sha256": self._hash(current),
        }

    @staticmethod
    def _hash(value: Any) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ToolResultArtifactizer:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.estimator = TokenEstimator()

    def store_or_inline(
        self,
        db: Session,
        *,
        run_id: int,
        artifact_type: str,
        result: dict[str, Any],
        inline_token_limit: int = 600,
    ) -> dict[str, Any]:
        estimate = self.estimator.count(result)
        if estimate.tokens <= inline_token_limit or not self.settings.tool_result_artifact_enabled:
            return {"inline": result, "tokens": estimate.tokens, "artifactized": False}
        artifact = AgentArtifact(run_id=run_id, artifact_type=artifact_type, artifact_json=result)
        db.add(artifact)
        db.commit()
        db.refresh(artifact)
        summary = {
            "artifact_id": artifact.id,
            "artifact_type": artifact_type,
            "sha256": DeltaContextBuilder._hash(result),
            "status": "available",
            "count": len(result),
        }
        return {"reference": summary, "tokens": self.estimator.count(summary).tokens, "artifactized": True}


class OutputTokenPolicy:
    CAPS: tuple[tuple[str, int], ...] = (
        ("natural_language.", 1200),
        ("resume_parser.", 3200),
        ("jd_parser.", 2600),
        ("evaluation.llm_judge_suitability", 1800),
        ("resume_tailor.", 5200),
        ("resume_review.", 3200),
        ("application.", 1800),
        ("interview_prep.", 7000),
        ("interview_agentic_rag.verify", 2800),
        ("interview_agentic_rag.repair", 5000),
        ("interview_agentic_rag.", 10000),
    )

    def limit(self, trace_name: str, requested: int | None) -> tuple[int | None, dict[str, Any]]:
        cap = next((value for prefix, value in self.CAPS if trace_name.startswith(prefix)), None)
        if requested is None or cap is None:
            return requested, {"policy": "unclassified", "cap": cap, "requested": requested}
        return min(requested, cap), {
            "policy": "careeragent-node-output-budget-v2",
            "cap": cap,
            "requested": requested,
            "reduced": requested > cap,
        }


class RetryOwnershipRegistry:
    OWNERS = {
        "network_transport": "llm_http_client",
        "http_429_5xx": "llm_http_client",
        "tool_transient": "tool_runtime",
        "worker_crash": "queue_recovery",
        "json_format": "json_repair_handler",
        "evidence_gap": "quality_repair_handler",
        "configuration": "none",
        "permission": "none",
        "budget_exhausted": "none",
        "high_risk_side_effect": "none",
    }

    def owner(self, error_category: str) -> str:
        try:
            return self.OWNERS[error_category]
        except KeyError as exc:
            raise TokenOptimizationError(f"Retry owner is not declared for {error_category}.") from exc


class TokenUsageReportService:
    def summarize(self, db: Session, *, run_id: int) -> dict[str, Any]:
        calls = (
            db.query(LLMCallLog)
            .filter(LLMCallLog.context_json["run_id"].as_integer() == run_id)
            .order_by(LLMCallLog.id.asc())
            .all()
        )
        steps = db.query(AgentStep).filter(AgentStep.run_id == run_id).all()
        by_node: dict[str, dict[str, Any]] = {}
        totals = {
            "business_calls": 0,
            "http_attempts": len(calls),
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "retry_tokens": 0,
            "repair_tokens": 0,
            "duplicate_context_tokens": 0,
            "tool_schema_tokens": 0,
            "tool_result_tokens": 0,
            "usage_missing_calls": 0,
            "tool_calls": sum(bool(step.tool_name) for step in steps),
        }
        seen_business_calls: set[str] = set()
        for call in calls:
            context = call.context_json or {}
            preview = call.prompt_preview_json or {}
            invocation = str(context.get("business_call_id") or f"{call.trace_name}:{call.id}")
            seen_business_calls.add(invocation)
            totals["input_tokens"] += call.prompt_tokens
            totals["output_tokens"] += call.completion_tokens
            totals["total_tokens"] += call.total_tokens
            totals["cached_tokens"] += int(context.get("cached_tokens") or 0)
            totals["reasoning_tokens"] += int(context.get("reasoning_tokens") or 0)
            totals["duplicate_context_tokens"] += int(context.get("duplicate_context_tokens") or 0)
            if context.get("usage_status") == "missing":
                totals["usage_missing_calls"] += 1
            if int(preview.get("attempt") or 1) > 1:
                totals["retry_tokens"] += call.total_tokens
            if context.get("repair_type") not in {None, "none"}:
                totals["repair_tokens"] += call.total_tokens
            sections = (preview.get("prompt_sections") or {}).get("sections") or {}
            totals["tool_schema_tokens"] += int((sections.get("tool_schemas") or {}).get("tokens") or 0)
            totals["tool_result_tokens"] += int(
                (sections.get("tool_observations") or {}).get("tokens") or 0
            )
            node = str(context.get("graph_node") or context.get("stage") or call.trace_name)
            row = by_node.setdefault(
                node,
                {"calls": 0, "attempts": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            )
            row["attempts"] += 1
            row["input_tokens"] += call.prompt_tokens
            row["output_tokens"] += call.completion_tokens
            row["total_tokens"] += call.total_tokens
        totals["business_calls"] = len(seen_business_calls)
        return {
            "version": "careeragent-token-usage-report-v2",
            "run_id": run_id,
            "totals": totals,
            "by_node": by_node,
        }
