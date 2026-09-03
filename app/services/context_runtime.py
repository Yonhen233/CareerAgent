from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import AgentArtifact, AgentMemory, AgentRun, Job, JobChunk, Profile, ResumeChunk


ContextCategory = Literal["control", "working", "evidence", "memory", "artifact"]


class ContextRuntimeError(RuntimeError):
    pass


class ContextBudgetExceededError(ContextRuntimeError):
    pass


class ContextIntegrityError(ContextRuntimeError):
    pass


class ContextScopeError(ContextRuntimeError):
    pass


@dataclass(frozen=True)
class ContextScope:
    tenant_id: str
    user_id: str
    profile_id: int | None = None

    def cache_scope(self) -> str:
        return f"{self.tenant_id}:{self.user_id}:{self.profile_id or '-'}"


@dataclass(frozen=True)
class ContextContract:
    name: str
    version: str
    task_types: tuple[str, ...]
    trace_prefixes: tuple[str, ...]
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    allowed_working_fields: tuple[str, ...]
    forbidden_fields: tuple[str, ...]
    allowed_evidence_types: tuple[str, ...]
    max_input_tokens: int
    output_reserve_tokens: int
    tool_schema_budget_tokens: int
    allow_session_memory: bool
    allow_long_term_memory: bool
    allow_raw_expansion: bool
    expansion_strategy: str
    critical_fact_types: tuple[str, ...]
    failure_policy: Literal["fail", "local_expand", "context_reset"]
    budget_weights: dict[str, float]


def _contract(
    name: str,
    trace_prefixes: tuple[str, ...],
    *,
    required: tuple[str, ...],
    evidence: tuple[str, ...] = (),
    max_input: int = 24000,
    output_reserve: int = 4096,
    tools: int = 768,
    memory: bool = False,
    long_memory: bool = False,
    raw: bool = False,
    failure: Literal["fail", "local_expand", "context_reset"] = "local_expand",
    weights: dict[str, float] | None = None,
    allowed_working: tuple[str, ...] = (),
    critical: tuple[str, ...] = (
        "identity",
        "goal",
        "constraint",
        "metric",
        "negative",
        "citation",
        "approval",
        "tool_receipt",
    ),
) -> ContextContract:
    return ContextContract(
        name=name,
        version="2.0.0",
        task_types=(name,),
        trace_prefixes=trace_prefixes,
        required_fields=required,
        optional_fields=("recent_errors", "tool_receipts", "tool_outputs", "artifact_refs"),
        allowed_working_fields=tuple(
            dict.fromkeys(
                (
                    *required,
                    *allowed_working,
                    "recent_errors",
                    "tool_receipts",
                    "tool_outputs",
                    "artifact_refs",
                    "user_prompt",
                )
            )
        ),
        forbidden_fields=("api_key", "authorization", "password", "session_secret", "raw_tool_log"),
        allowed_evidence_types=evidence,
        max_input_tokens=max_input,
        output_reserve_tokens=output_reserve,
        tool_schema_budget_tokens=tools,
        allow_session_memory=memory,
        allow_long_term_memory=long_memory,
        allow_raw_expansion=raw,
        expansion_strategy="citation_local_window" if raw else "none",
        critical_fact_types=critical,
        failure_policy=failure,
        budget_weights=weights
        or {"working": 0.35, "evidence": 0.45, "memory": 0.10, "artifact": 0.10},
    )


CONTEXT_CONTRACTS: tuple[ContextContract, ...] = (
    _contract(
        "natural_language_planner",
        ("natural_language.",),
        required=("goal", "constraints"),
        allowed_working=(
            "current_request",
            "current_node",
            "pending_steps",
            "profile_id",
            "job_id",
            "recent_messages",
            "conversation_summary",
            "steps",
        ),
        memory=True,
        long_memory=True,
        critical=("goal", "constraint", "negative", "approval", "tool_receipt"),
        failure="context_reset",
        weights={"working": 0.55, "evidence": 0.10, "memory": 0.30, "artifact": 0.05},
    ),
    _contract(
        "profile_resume_parser",
        ("resume_parser.",),
        required=("raw_resume",),
        allowed_working=("page_map", "sections"),
        evidence=("resume_page", "resume_chunk"),
        max_input=48000,
        raw=True,
        weights={"working": 0.10, "evidence": 0.80, "memory": 0.05, "artifact": 0.05},
    ),
    _contract(
        "jd_parser",
        ("jd_parser.",),
        required=("raw_jd",),
        allowed_working=("source_url", "page_map", "sections"),
        evidence=("jd_section", "job_chunk"),
        max_input=32000,
        raw=True,
        weights={"working": 0.10, "evidence": 0.82, "memory": 0.03, "artifact": 0.05},
    ),
    _contract(
        "job_matcher",
        ("evaluation.llm_judge_suitability", "matcher."),
        required=("profile", "job"),
        allowed_working=("query",),
        evidence=("project", "work_experience", "job_requirement"),
        weights={"working": 0.20, "evidence": 0.70, "memory": 0.05, "artifact": 0.05},
    ),
    _contract(
        "resume_tailor",
        ("resume_tailor.", "resume_review."),
        required=("profile", "job", "evidence"),
        allowed_working=("query", "current_draft", "gaps", "guardrail_issues"),
        evidence=("project", "work_experience", "education", "metric", "negative"),
        raw=True,
        weights={"working": 0.18, "evidence": 0.72, "memory": 0.05, "artifact": 0.05},
    ),
    _contract(
        "application_packet",
        ("application.",),
        required=("profile", "job", "verified_resume"),
        allowed_working=("profile_id", "job_id", "resume_version_id", "approval_status"),
        evidence=("verified_resume", "job_requirement"),
        weights={"working": 0.35, "evidence": 0.50, "memory": 0.05, "artifact": 0.10},
    ),
    _contract(
        "interview_question_generator",
        ("interview_prep.",),
        required=("job", "profile"),
        allowed_working=("questions", "query"),
        evidence=("job_requirement", "project", "interview_experience"),
        max_input=36000,
        weights={"working": 0.15, "evidence": 0.75, "memory": 0.05, "artifact": 0.05},
    ),
    _contract(
        "interview_answer_generator",
        ("interview_agentic_rag.",),
        required=("question", "evidence"),
        allowed_working=("questions", "shared_context", "items"),
        evidence=("project", "job_requirement", "interview_experience", "technical_reference"),
        max_input=32000,
        memory=True,
        raw=True,
        weights={"working": 0.15, "evidence": 0.70, "memory": 0.10, "artifact": 0.05},
    ),
    _contract(
        "claim_verifier",
        ("evaluation.interview_claim_verifier.", "claim_verifier."),
        required=("claims", "evidence"),
        allowed_working=("question", "items", "shared_context"),
        evidence=("project", "work_experience", "job_requirement", "citation"),
        max_input=18000,
        output_reserve=2048,
        weights={"working": 0.25, "evidence": 0.70, "memory": 0.0, "artifact": 0.05},
    ),
    _contract(
        "guardrail",
        ("guardrail.",),
        required=("candidate_output", "source_facts"),
        allowed_working=("citations", "negative_facts", "issues"),
        evidence=("project", "work_experience", "metric", "negative", "citation"),
        max_input=18000,
        output_reserve=2048,
        weights={"working": 0.30, "evidence": 0.65, "memory": 0.0, "artifact": 0.05},
    ),
    _contract(
        "completion_gate",
        ("completion_gate.",),
        required=("goal", "steps", "artifact_refs"),
        allowed_working=(
            "profile_id",
            "job_id",
            "resume_version_id",
            "approval_status",
            "artifact_status",
            "business_terminal_state",
        ),
        max_input=8000,
        output_reserve=1024,
        tools=256,
        failure="fail",
        weights={"working": 0.75, "evidence": 0.0, "memory": 0.0, "artifact": 0.25},
    ),
)


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    method: str
    estimated: bool
    model: str


class TokenEstimator:
    """Prefer a local model tokenizer and make fallback estimates explicit."""

    _calibration: dict[str, float] = {}

    def __init__(self, model: str | None = None) -> None:
        self.settings = get_settings()
        self.model = model or self.settings.context_tokenizer_model or self.settings.llm_model
        self._tokenizer: Any | None = None
        self._tokenizer_checked = False

    def count(self, value: Any) -> TokenEstimate:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        tokenizer = self._load_tokenizer()
        if tokenizer is not None:
            try:
                return TokenEstimate(len(tokenizer.encode(text, add_special_tokens=False)), "model_tokenizer", False, self.model)
            except TypeError:
                return TokenEstimate(len(tokenizer.encode(text)), "model_tokenizer", False, self.model)
        try:
            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")
            tokens = len(encoding.encode(text))
            method = "tiktoken_cl100k_proxy"
        except (ImportError, KeyError):
            tokens = self._heuristic_count(text)
            method = "cjk_heuristic"
        ratio = self._calibration.get(self.model, 1.0)
        return TokenEstimate(max(1, round(tokens * ratio)) if text else 0, method, True, self.model)

    def calibrate(self, *, estimated_tokens: int, actual_prompt_tokens: int) -> None:
        if estimated_tokens <= 0 or actual_prompt_tokens <= 0:
            return
        observed = max(0.5, min(actual_prompt_tokens / estimated_tokens, 2.0))
        previous = self._calibration.get(self.model)
        self._calibration[self.model] = observed if previous is None else previous * 0.7 + observed * 0.3

    def _load_tokenizer(self) -> Any | None:
        if self._tokenizer_checked:
            return self._tokenizer
        self._tokenizer_checked = True
        configured = self.settings.context_tokenizer_model
        if not configured:
            return None
        try:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                configured,
                local_files_only=True,
                trust_remote_code=False,
            )
        except (OSError, ValueError):
            self._tokenizer = None
        return self._tokenizer

    @staticmethod
    def _heuristic_count(text: str) -> int:
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        ascii_words = len(re.findall(r"[A-Za-z0-9_]+|[^\w\s]", text))
        remaining = max(len(text) - cjk, 0)
        return cjk + max(ascii_words, (remaining + 3) // 4)


@dataclass(frozen=True)
class CriticalFact:
    fact_id: str
    fact_type: str
    value: Any
    source_id: str
    hard: bool = True

    @property
    def digest(self) -> str:
        raw = json.dumps(self.value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(f"{self.fact_type}:{raw}".encode("utf-8")).hexdigest()


class CriticalFactLedger:
    NEGATIVE_PATTERN = re.compile(
        r"没有|未实现|未上线|仅课程|课程学习|计划学习|尚未|不具备|abandoned|coursework|planned|not implemented",
        re.IGNORECASE,
    )
    KEY_TYPES = {
        "name": "identity",
        "email": "identity",
        "phone": "identity",
        "target_roles": "goal",
        "target_role": "goal",
        "goal": "goal",
        "city": "constraint",
        "location": "constraint",
        "duration": "constraint",
        "required_skills": "constraint",
        "preferred_skills": "constraint",
        "approval_status": "approval",
        "approval_id": "approval",
        "tool_receipt": "tool_receipt",
        "citation_id": "citation",
        "chunk_uid": "citation",
        "page_no": "citation",
        "page_start": "citation",
        "page_end": "citation",
    }

    def extract(self, *sources: tuple[str, Any]) -> list[CriticalFact]:
        found: dict[str, CriticalFact] = {}
        for source_id, value in sources:
            self._walk(value, source_id=source_id, path="", found=found)
        return list(found.values())

    def recall(self, before: list[CriticalFact], after: Any) -> tuple[float, list[CriticalFact]]:
        serialized = json.dumps(after, ensure_ascii=False, sort_keys=True, default=str)
        missing = []
        for fact in before:
            value = json.dumps(fact.value, ensure_ascii=False, sort_keys=True, default=str)
            if value not in serialized and str(fact.value) not in serialized:
                missing.append(fact)
        retained = len(before) - len(missing)
        return (retained / len(before) if before else 1.0), missing

    def _walk(self, value: Any, *, source_id: str, path: str, found: dict[str, CriticalFact]) -> None:
        if isinstance(value, dict):
            explicit = value.get("critical_facts")
            if isinstance(explicit, list):
                for index, item in enumerate(explicit):
                    if not isinstance(item, dict) or "value" not in item:
                        continue
                    self._add(
                        found,
                        fact_type=str(item.get("type") or "constraint"),
                        value=item["value"],
                        source_id=str(item.get("source_id") or source_id),
                        path=f"{path}.critical_facts[{index}]",
                        hard=bool(item.get("hard", True)),
                    )
            for key, item in value.items():
                child_path = f"{path}.{key}" if path else key
                fact_type = self.KEY_TYPES.get(key.lower())
                if fact_type and self._is_compact(item):
                    self._add(found, fact_type=fact_type, value=item, source_id=source_id, path=child_path)
                if key.lower() in {"metric", "metrics", "impact", "quantified_results"} and self._is_compact(item):
                    self._add(found, fact_type="metric", value=item, source_id=source_id, path=child_path)
                self._walk(item, source_id=source_id, path=child_path, found=found)
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                self._walk(item, source_id=source_id, path=f"{path}[{index}]", found=found)
            return
        if isinstance(value, str):
            if self.NEGATIVE_PATTERN.search(value):
                self._add(found, fact_type="negative", value=value, source_id=source_id, path=path)

    @staticmethod
    def _is_compact(value: Any) -> bool:
        return len(json.dumps(value, ensure_ascii=False, default=str)) <= 1200

    @staticmethod
    def _add(
        found: dict[str, CriticalFact],
        *,
        fact_type: str,
        value: Any,
        source_id: str,
        path: str,
        hard: bool = True,
    ) -> None:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(f"{fact_type}:{raw}".encode("utf-8")).hexdigest()
        found.setdefault(
            digest,
            CriticalFact(
                fact_id=f"cf_{digest[:16]}",
                fact_type=fact_type,
                value=value,
                source_id=f"{source_id}:{path}",
                hard=hard,
            ),
        )


@dataclass
class ContextRequest:
    run_id: int | None
    node: str
    task_type: str
    scope: ContextScope
    control: dict[str, Any]
    working: dict[str, Any]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    memory: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    tool_schema: dict[str, Any] = field(default_factory=dict)
    query: str = ""
    prompt_version: str = "unregistered"
    skill_versions: dict[str, str] = field(default_factory=dict)
    tool_policy_version: str = "careeragent-tool-contract-v4"
    data_version: str = "unknown"
    source_mode: Literal["structured", "text_prompt"] = "structured"
    jit_loader: Any | None = None


@dataclass
class ContextBuildResult:
    packet: dict[str, Any]
    trace: dict[str, Any]
    handoff_artifact: dict[str, Any] | None = None


class ContextProjectionCache:
    def __init__(self, max_entries: int | None = None) -> None:
        self.settings = get_settings()
        self.max_entries = max_entries or self.settings.context_cache_max_entries
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def make_key(self, request: ContextRequest, contract: ContextContract) -> str:
        material = {
            "scope": request.scope.cache_scope(),
            "data_version": request.data_version,
            "contract": f"{contract.name}@{contract.version}",
            "prompt": request.prompt_version,
            "working": request.working,
            "evidence": request.evidence,
            "memory": request.memory,
            "artifacts": request.artifacts,
            "query": request.query,
        }
        raw = json.dumps(material, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        value = self._entries.get(key)
        if value is None:
            return None
        self._entries.move_to_end(key)
        return json.loads(json.dumps(value, ensure_ascii=False))

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._entries[key] = json.loads(json.dumps(value, ensure_ascii=False, default=str))
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def invalidate_scope(self, scope: ContextScope) -> int:
        prefix = scope.cache_scope()
        keys = [key for key, value in self._entries.items() if value.get("_cache_scope") == prefix]
        for key in keys:
            self._entries.pop(key, None)
        return len(keys)


class ContextRuntimeV2:
    VERSION = "careeragent-context-runtime-v2.0.0"

    def __init__(self, *, estimator: TokenEstimator | None = None, cache: ContextProjectionCache | None = None) -> None:
        self.settings = get_settings()
        self.estimator = estimator or TokenEstimator()
        self.ledger = CriticalFactLedger()
        self.cache = cache or ContextProjectionCache()

    def contract_for(self, node_or_trace: str) -> ContextContract:
        exact = next((item for item in CONTEXT_CONTRACTS if item.name == node_or_trace), None)
        if exact is not None:
            return exact
        matches = [
            item
            for item in CONTEXT_CONTRACTS
            if any(node_or_trace.startswith(prefix) for prefix in item.trace_prefixes)
        ]
        if matches:
            return max(matches, key=lambda item: max(len(prefix) for prefix in item.trace_prefixes))
        raise KeyError(f"No Context Contract registered for node or trace: {node_or_trace}")

    def build(self, request: ContextRequest) -> ContextBuildResult:
        started = time.perf_counter()
        self._validate_scope(request.scope)
        contract = self.contract_for(request.node)
        self._validate_required(request, contract)
        self._validate_forbidden(request, contract)
        estimates_before = self._category_tokens(request)
        control_tokens = estimates_before["control"]
        tool_tokens = estimates_before["tool_schema"]
        model_limit = min(contract.max_input_tokens, self.settings.context_model_window_tokens)
        output_reserve = max(contract.output_reserve_tokens, self.settings.context_output_reserve_tokens)
        available = (
            model_limit
            - output_reserve
            - self.settings.context_safety_margin_tokens
            - control_tokens
            - min(tool_tokens, contract.tool_schema_budget_tokens)
        )
        if available <= 0:
            raise ContextBudgetExceededError("Control Context and reserved output exhaust the node token budget.")

        cache_key = self.cache.make_key(request, contract)
        cached = self.cache.get(cache_key) if self.settings.context_cache_enabled else None
        cache_hit = cached is not None
        levels = [0, 1]
        deduplicated = 0
        if cached is not None:
            packet = cached
            packet.pop("_cache_scope", None)
        else:
            working = self._project_working(request.working, contract)
            evidence, evidence_deduped = self._project_evidence(
                request.evidence,
                contract=contract,
                query=request.query,
                token_budget=max(0, round(available * contract.budget_weights.get("evidence", 0))),
            )
            memory, memory_deduped = self._project_memory(
                request.memory,
                contract=contract,
                token_budget=max(0, round(available * contract.budget_weights.get("memory", 0))),
            )
            artifacts = self._project_artifacts(request.artifacts)
            deduplicated = evidence_deduped + memory_deduped
            if request.evidence:
                levels.append(2)
            packet = {
                "context_runtime": self.VERSION,
                "contract": {"name": contract.name, "version": contract.version},
                "scope": {
                    "tenant_id": request.scope.tenant_id,
                    "user_id": request.scope.user_id,
                    "profile_id": request.scope.profile_id,
                },
                "control_context": request.control,
                "working_context": working,
                "evidence_context": evidence,
                "memory_context": memory,
                "artifact_context": artifacts,
            }
            if self.settings.context_cache_enabled:
                cache_value = {**packet, "_cache_scope": request.scope.cache_scope()}
                self.cache.put(cache_key, cache_value)
                packet.pop("_cache_scope", None)

        facts = [
            fact
            for fact in self.ledger.extract(
            ("working", packet.get("working_context", {})),
            ("evidence", packet.get("evidence_context", [])),
            ("memory", packet.get("memory_context", [])),
            ("artifacts", packet.get("artifact_context", [])),
            )
            if fact.fact_type in contract.critical_fact_types
        ]

        total = self.estimator.count(packet).tokens
        ratio = total / max(model_limit - output_reserve, 1)
        handoff = None
        if ratio >= self.settings.context_token_high_limit_ratio and self.settings.context_compaction_enabled:
            packet["memory_context"] = self._compact_history(packet.get("memory_context", []))
            levels.append(3)
            total = self.estimator.count(packet).tokens
            ratio = total / max(model_limit - output_reserve, 1)
        if ratio >= self.settings.context_token_hard_limit_ratio:
            if contract.failure_policy != "context_reset":
                raise ContextBudgetExceededError(
                    f"Context for {contract.name} exceeds hard limit after deterministic compaction."
                )
            handoff = self._handoff(packet, facts, request)
            packet = {
                "context_runtime": self.VERSION,
                "contract": {"name": contract.name, "version": contract.version},
                "scope": packet["scope"],
                "control_context": packet["control_context"],
                "working_context": self._reset_working(packet["working_context"]),
                "handoff": handoff,
                "evidence_context": packet.get("evidence_context", [])[:3],
                "memory_context": packet.get("memory_context", [])[-2:],
                "artifact_context": packet.get("artifact_context", []),
                "critical_fact_ledger": packet.get("critical_fact_ledger", []),
            }
            levels.append(4)
            total = self.estimator.count(packet).tokens
            if total / max(model_limit - output_reserve, 1) >= self.settings.context_token_hard_limit_ratio:
                raise ContextBudgetExceededError("Context reset still exceeds the hard input limit.")

        recall, missing = self.ledger.recall(facts, packet)
        local_expansions = 0
        if missing and request.jit_loader is not None:
            local_expansions = self._jit_restore_missing(
                packet,
                missing=missing,
                loader=request.jit_loader,
            )
            recall, missing = self.ledger.recall(facts, packet)
        if request.jit_loader is not None:
            local_expansions += self._jit_restore_missing_citation_bodies(
                packet,
                loader=request.jit_loader,
            )
        if missing:
            packet["critical_fact_ledger"] = [
                {
                    "fact_id": fact.fact_id,
                    "type": fact.fact_type,
                    "value": fact.value,
                    "source_id": fact.source_id,
                }
                for fact in missing
            ]
        self._validate_citation_bodies(packet)
        if any(fact.hard for fact in missing):
            raise ContextIntegrityError(
                "Context compression lost hard critical facts after JIT: "
                + ", ".join(fact.fact_id for fact in missing)
            )

        final_tokens = self._packet_category_tokens(packet)
        trace = {
            "context_runtime_version": self.VERSION,
            "run_id": request.run_id,
            "node": request.node,
            "task_type": request.task_type,
            "contract_name": contract.name,
            "contract_version": contract.version,
            "prompt_version": request.prompt_version,
            "skill_versions": request.skill_versions,
            "tool_policy_version": request.tool_policy_version,
            "token_estimation_method": self.estimator.count(packet).method,
            "tokens_are_estimated": self.estimator.count(packet).estimated,
            "raw_input_tokens": sum(estimates_before.values()),
            "final_input_tokens": total,
            "output_reserve_tokens": output_reserve,
            "category_tokens": final_tokens,
            "compression_levels": sorted(set(levels)),
            "deduplicated_items": deduplicated,
            "removed_fields": sorted(set(request.working) - set(packet.get("working_context", {}))),
            "retained_evidence_count": len(packet.get("evidence_context", [])),
            "critical_fact_total": len(facts),
            "critical_fact_retained": len(facts) - len(missing),
            "critical_fact_recall": round(recall, 6),
            "critical_fact_missing_ids": [fact.fact_id for fact in missing],
            "jit_expansion_count": local_expansions,
            "cache_hit": cache_hit,
            "compaction_count": int(3 in levels),
            "context_reset": handoff is not None,
            "quality_gate_passed": not missing,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "packet_sha256": self._hash(packet),
            "control_sha256": self._hash(request.control),
            "scope_hash": self._hash(asdict(request.scope))[:16],
            "actual_provider_usage": None,
            "actual_usage_available": False,
        }
        return ContextBuildResult(packet=packet, trace=trace, handoff_artifact=handoff)

    def observe_text_prompt(
        self,
        *,
        trace_name: str,
        system_prompt: str,
        user_prompt: str,
        run_id: int | None,
        task_type: str,
        scope: ContextScope,
        prompt_version: str,
        skill_versions: dict[str, str],
    ) -> ContextBuildResult | None:
        try:
            contract = self.contract_for(trace_name)
        except KeyError:
            return None
        request = ContextRequest(
            run_id=run_id,
            node=contract.name,
            task_type=task_type or contract.name,
            scope=scope,
            control={"system_prompt": system_prompt},
            working={"user_prompt": user_prompt},
            prompt_version=prompt_version,
            skill_versions=skill_versions,
            data_version=self._hash(user_prompt),
            source_mode="text_prompt",
        )
        return self.build(request)

    def _category_tokens(self, request: ContextRequest) -> dict[str, int]:
        return {
            "control": self.estimator.count(request.control).tokens,
            "working": self.estimator.count(request.working).tokens,
            "evidence": self.estimator.count(request.evidence).tokens,
            "memory": self.estimator.count(request.memory).tokens,
            "artifact": self.estimator.count(request.artifacts).tokens,
            "tool_schema": self.estimator.count(request.tool_schema).tokens,
        }

    def _packet_category_tokens(self, packet: dict[str, Any]) -> dict[str, int]:
        return {
            category: self.estimator.count(packet.get(f"{category}_context", [])).tokens
            for category in ("control", "working", "evidence", "memory", "artifact")
        }

    def _project_working(self, working: dict[str, Any], contract: ContextContract) -> dict[str, Any]:
        if self.settings.context_management_v3_enabled:
            allowed = set(contract.allowed_working_fields)
            projected = {key: value for key, value in working.items() if key in allowed}
        else:
            projected = dict(working)
            for key in (
                "full_conversation",
                "raw_resume_text",
                "raw_jd_text",
                "all_rag_results",
                "memory_blob",
            ):
                projected.pop(key, None)
        if isinstance(projected.get("profile"), dict):
            projected["profile"] = self._project_profile(projected["profile"])
        if isinstance(projected.get("job"), dict):
            projected["job"] = self._project_job(projected["job"])
        if contract.name not in {"profile_resume_parser", "jd_parser"}:
            projected.pop("raw_resume_text", None)
            projected.pop("raw_jd_text", None)
        if "steps" in projected and isinstance(projected["steps"], list):
            projected["steps"] = [
                item for item in projected["steps"] if not isinstance(item, dict) or item.get("status") != "completed"
            ]
        if "tool_outputs" in projected and isinstance(projected["tool_outputs"], list):
            projected["tool_receipts"] = [self._artifact_ref(item) for item in projected.pop("tool_outputs")]
        return projected

    def _jit_restore_missing(
        self,
        packet: dict[str, Any],
        *,
        missing: list[CriticalFact],
        loader: Any,
    ) -> int:
        restored = 0
        evidence_context = packet.setdefault("evidence_context", [])
        working_context = packet.setdefault("working_context", {})
        for fact in missing:
            receipt = None
            if fact.fact_type == "citation" and isinstance(fact.value, str):
                receipt = loader.load_evidence_fragment(fact.value)
                evidence_context.append(
                    {
                        "citation_id": fact.value,
                        "evidence_type": receipt["source_type"],
                        "text": receipt["value"],
                        "jit_loaded": True,
                        "source_id": receipt["source_id"],
                        "value_sha256": receipt["value_sha256"],
                    }
                )
            elif fact.source_id.startswith("working:"):
                source_path = fact.source_id.split(":", 1)[1]
                parts = source_path.split(".")
                if len(parts) >= 2 and parts[0] == "profile" and loader.scope.profile_id:
                    receipt = loader.load_profile_fragment(loader.scope.profile_id, field=parts[1])
                    working_context.setdefault("profile", {})[parts[1]] = receipt["value"]
                elif len(parts) >= 2 and parts[0] == "job":
                    job_id = working_context.get("job_id")
                    if job_id:
                        receipt = loader.load_job_fragment(int(job_id), field=parts[1])
                        working_context.setdefault("job", {})[parts[1]] = receipt["value"]
            if receipt is not None:
                restored += 1
        return restored

    @staticmethod
    def _validate_citation_bodies(packet: dict[str, Any]) -> None:
        missing_bodies = [
            str(item.get("citation_id"))
            for item in packet.get("evidence_context", [])
            if item.get("citation_id") and not str(item.get("text") or "").strip()
        ]
        if missing_bodies:
            raise ContextIntegrityError(
                "Citation is present without minimal evidence text: " + ", ".join(missing_bodies)
            )

    @staticmethod
    def _jit_restore_missing_citation_bodies(packet: dict[str, Any], *, loader: Any) -> int:
        restored = 0
        for item in packet.get("evidence_context", []):
            citation_id = item.get("citation_id")
            if not citation_id or str(item.get("text") or "").strip():
                continue
            receipt = loader.load_evidence_fragment(str(citation_id))
            item.update(
                {
                    "text": receipt["value"],
                    "jit_loaded": True,
                    "source_id": receipt["source_id"],
                    "value_sha256": receipt["value_sha256"],
                }
            )
            restored += 1
        return restored

    @staticmethod
    def _project_profile(profile: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            "name",
            "headline",
            "target_roles",
            "skills",
            "projects",
            "work_experience",
            "education",
            "certifications",
            "awards",
            "languages",
            "constraints",
            "critical_facts",
        )
        return {key: profile[key] for key in allowed if key in profile}

    @staticmethod
    def _project_job(job: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            "title",
            "company",
            "location",
            "job_type",
            "responsibilities",
            "qualifications",
            "required_skills",
            "preferred_skills",
            "negative_requirements",
            "education_requirement",
            "internship_duration",
            "keywords",
            "critical_facts",
        )
        return {key: job[key] for key in allowed if key in job}

    def _project_evidence(
        self,
        evidence: list[dict[str, Any]],
        *,
        contract: ContextContract,
        query: str,
        token_budget: int,
    ) -> tuple[list[dict[str, Any]], int]:
        allowed = set(contract.allowed_evidence_types)
        unique: dict[str, dict[str, Any]] = {}
        deduped = 0
        for item in evidence:
            evidence_type = str(item.get("evidence_type") or item.get("chunk_type") or "unknown")
            if allowed and evidence_type not in allowed and evidence_type != "unknown":
                continue
            key = str(item.get("citation_id") or item.get("chunk_uid") or self._hash(item.get("text", "")))
            if key in unique:
                deduped += 1
                if self._evidence_priority(item) > self._evidence_priority(unique[key]):
                    unique[key] = item
            else:
                unique[key] = item
        ranked = sorted(unique.values(), key=self._evidence_priority, reverse=True)
        selected: list[dict[str, Any]] = []
        used = 0
        for item in ranked:
            projected = self._evidence_window(item, query=query, max_tokens=min(900, max(token_budget - used, 0)))
            item_tokens = self.estimator.count(projected).tokens
            is_negative = self._is_negative_evidence(item)
            if used + item_tokens > token_budget and not is_negative:
                continue
            selected.append(projected)
            used += item_tokens
            if used >= token_budget and not any(self._is_negative_evidence(candidate) for candidate in ranked[len(selected) :]):
                break
        return selected, deduped

    def _project_memory(
        self,
        memory: list[dict[str, Any]],
        *,
        contract: ContextContract,
        token_budget: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if not contract.allow_session_memory:
            return [], len(memory)
        seen: set[str] = set()
        retained: list[dict[str, Any]] = []
        deduped = 0
        used = 0
        for item in reversed(memory):
            if str(item.get("injection_risk") or "").lower() in {"high", "critical"}:
                continue
            if item.get("memory_scope") == "long_term" and not contract.allow_long_term_memory:
                continue
            key = str(item.get("memory_key") or self._hash(item))
            if key in seen:
                deduped += 1
                continue
            tokens = self.estimator.count(item).tokens
            if used + tokens > token_budget:
                continue
            seen.add(key)
            retained.append(item)
            used += tokens
        retained.reverse()
        return retained, deduped

    def _project_artifacts(self, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self._artifact_ref(item) for item in artifacts]

    def _artifact_ref(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item.get(key)
            for key in ("artifact_id", "artifact_type", "uri", "sha256", "status", "summary", "receipt_id")
            if item.get(key) is not None
        }

    def _evidence_window(self, item: dict[str, Any], *, query: str, max_tokens: int) -> dict[str, Any]:
        text = str(item.get("text") or "")
        max_chars = max(160, max_tokens * 3)
        if len(text) > max_chars:
            terms = [term.lower() for term in re.findall(r"[\w\u3400-\u9fff]+", query) if len(term) > 1]
            positions = [text.lower().find(term) for term in terms if text.lower().find(term) >= 0]
            negative = self.ledger.NEGATIVE_PATTERN.search(text)
            if negative:
                positions.append(negative.start())
            center = min(positions) if positions else len(text) // 2
            start = max(0, center - max_chars // 3)
            text = text[start : start + max_chars]
        return {
            "citation_id": item.get("citation_id") or item.get("chunk_uid"),
            "evidence_type": item.get("evidence_type") or item.get("chunk_type"),
            "polarity": item.get("polarity") or ("negative" if self._is_negative_evidence(item) else "positive"),
            "source": item.get("source"),
            "page_no": item.get("page_no") or (item.get("metadata") or {}).get("page_no"),
            "page_start": item.get("page_start") or (item.get("metadata") or {}).get("page_start"),
            "page_end": item.get("page_end") or (item.get("metadata") or {}).get("page_end"),
            "score": item.get("score"),
            "trust": item.get("trust", 1.0),
            "untrusted": bool(item.get("untrusted", True)),
            "injection_risk": item.get("injection_risk", "unknown"),
            "text": text,
        }

    def _evidence_priority(self, item: dict[str, Any]) -> float:
        score = float(item.get("score") or 0.0)
        trust = float(item.get("trust") if item.get("trust") is not None else 1.0)
        negative_boost = 0.35 if self._is_negative_evidence(item) else 0.0
        critical_boost = 0.20 if item.get("critical") else 0.0
        recency = float(item.get("recency_score") or 0.0) * 0.05
        return score * 0.65 + trust * 0.15 + negative_boost + critical_boost + recency

    def _is_negative_evidence(self, item: dict[str, Any]) -> bool:
        return str(item.get("polarity") or "").lower() in {"negative", "missing", "planned"} or bool(
            self.ledger.NEGATIVE_PATTERN.search(str(item.get("text") or ""))
        )

    def _compact_history(self, memory: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(memory) <= 3:
            return memory
        older, recent = memory[:-3], memory[-3:]
        compact = {
            "memory_type": "structured_compaction",
            "current_goal": self._latest(older, "current_goal"),
            "user_constraints": self._collect(older, "user_constraints"),
            "confirmed_facts": self._collect(older, "confirmed_facts"),
            "completed_actions": self._collect(older, "completed_actions"),
            "decisions": self._collect(older, "decisions"),
            "unresolved": self._collect(older, "unresolved"),
            "errors": self._collect(older, "errors"),
            "artifact_refs": self._collect(older, "artifact_refs"),
            "forbidden": self._collect(older, "forbidden"),
            "next_steps": self._collect(older, "next_steps"),
            "source_ids": [item.get("source_id") for item in older if item.get("source_id")],
            "injection_risks": sorted(
                {
                    str(item.get("injection_risk"))
                    for item in older
                    if item.get("injection_risk") is not None
                }
            ),
            "authoritative": False,
        }
        return [compact, *recent]

    def _handoff(
        self,
        packet: dict[str, Any],
        facts: list[CriticalFact],
        request: ContextRequest,
    ) -> dict[str, Any]:
        return {
            "artifact_type": "context_handoff",
            "version": "2.0.0",
            "source_run_id": request.run_id,
            "goal": packet.get("working_context", {}).get("goal"),
            "uncompleted_steps": packet.get("working_context", {}).get("steps", []),
            "recent_errors": packet.get("working_context", {}).get("recent_errors", []),
            "artifact_refs": packet.get("artifact_context", []),
            "evidence_refs": [item.get("citation_id") for item in packet.get("evidence_context", [])],
            "critical_fact_ids": [fact.fact_id for fact in facts],
            "created_at": datetime.now(UTC).isoformat(),
            "source_packet_sha256": self._hash(packet),
        }

    @staticmethod
    def _reset_working(working: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            "goal",
            "constraints",
            "steps",
            "recent_errors",
            "tool_receipts",
            "selected_job_id",
            "profile_id",
        )
        return {key: working[key] for key in allowed if key in working}

    def _validate_forbidden(self, request: ContextRequest, contract: ContextContract) -> None:
        forbidden = set(contract.forbidden_fields)
        control_text = json.dumps(request.control, ensure_ascii=False, default=str).lower()
        for source in (request.evidence, request.memory, request.artifacts):
            if isinstance(source, list):
                for item in source:
                    if isinstance(item, dict) and item.get("promote_to_control"):
                        raise ContextIntegrityError("Untrusted context cannot be promoted into Control Context.")
        if any(key in request.working for key in forbidden):
            raise ContextIntegrityError("Working Context contains a forbidden secret field.")
        if "ignore previous instructions" in control_text and request.control.get("source") == "external":
            raise ContextIntegrityError("External text cannot be used as Control Context.")

    @staticmethod
    def _validate_required(request: ContextRequest, contract: ContextContract) -> None:
        if request.source_mode == "text_prompt":
            return
        available = set(request.working) | {"evidence", "artifact_refs"}
        missing = sorted(set(contract.required_fields) - available)
        if missing:
            raise ContextIntegrityError(
                f"Context Contract {contract.name} is missing required fields: {', '.join(missing)}"
            )

    @staticmethod
    def _validate_scope(scope: ContextScope) -> None:
        if not scope.tenant_id or not scope.user_id:
            raise ContextScopeError("ContextRequest requires tenant_id and user_id.")

    @staticmethod
    def _collect(items: list[dict[str, Any]], key: str) -> list[Any]:
        values = []
        for item in items:
            value = item.get(key)
            if value is None:
                continue
            values.extend(value if isinstance(value, list) else [value])
        return values

    @staticmethod
    def _latest(items: list[dict[str, Any]], key: str) -> Any:
        for item in reversed(items):
            if item.get(key) is not None:
                return item[key]
        return None

    @staticmethod
    def _hash(value: Any) -> str:
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ContextJITLoader:
    """Scope-checked fragment loading. Full documents are never returned by default."""

    OPERATIONS = {
        "load_profile_fragment",
        "load_job_fragment",
        "load_evidence_fragment",
        "load_artifact_excerpt",
        "load_session_decisions",
        "load_prior_run_outcome",
    }

    def __init__(
        self,
        db: Session,
        *,
        scope: ContextScope,
        max_calls: int | None = None,
        allowed_operations: set[str] | None = None,
    ) -> None:
        self.db = db
        self.scope = scope
        self.settings = get_settings()
        self.max_calls = self.settings.context_jit_max_calls if max_calls is None else max_calls
        self.allowed_operations = allowed_operations or set(self.OPERATIONS)
        unknown = self.allowed_operations - self.OPERATIONS
        if unknown:
            raise ContextScopeError(f"Unknown JIT Context operations: {sorted(unknown)}")
        self.calls = 0
        self.receipts: list[dict[str, Any]] = []
        self.estimator = TokenEstimator()

    def load_profile_fragment(self, profile_id: int, *, field: str) -> dict[str, Any]:
        self._authorize("load_profile_fragment")
        profile = self.db.get(Profile, profile_id)
        if profile is None or profile.tenant_id != self.scope.tenant_id or profile.id != self.scope.profile_id:
            raise ContextScopeError("Profile fragment is not visible in the current Context scope.")
        allowed = {"name", "headline", "target_roles", "skills", "projects", "work_experience", "education"}
        if field not in allowed:
            raise ContextScopeError(f"Profile field is not JIT-readable: {field}")
        value = (profile.structured_profile_json or {}).get(field)
        if field in {"name", "headline"} and value is None:
            value = getattr(profile, field)
        return self._receipt("profile", profile_id, field, value, untrusted=False)

    def load_job_fragment(self, job_id: int, *, field: str) -> dict[str, Any]:
        self._authorize("load_job_fragment")
        job = self.db.get(Job, job_id)
        if job is None or job.tenant_id not in {None, self.scope.tenant_id}:
            raise ContextScopeError("Job fragment is not visible in the current Context scope.")
        allowed = {"title", "company", "location", "required_skills", "preferred_skills", "responsibilities", "qualifications"}
        if field not in allowed:
            raise ContextScopeError(f"Job field is not JIT-readable: {field}")
        value = getattr(job, field, None)
        if value is None:
            value = (job.structured_jd_json or {}).get(field)
        return self._receipt("job", job_id, field, value, untrusted=True)

    def load_evidence_fragment(self, citation_id: str) -> dict[str, Any]:
        self._authorize("load_evidence_fragment")
        profile_chunk = (
            self.db.query(ResumeChunk)
            .filter(ResumeChunk.profile_id == self.scope.profile_id, ResumeChunk.chunk_uid == citation_id)
            .one_or_none()
        )
        if profile_chunk is not None:
            return self._receipt("resume_chunk", profile_chunk.id, citation_id, profile_chunk.text, untrusted=True)
        job_chunk = self.db.query(JobChunk).filter(JobChunk.chunk_uid == citation_id).one_or_none()
        if job_chunk is None or job_chunk.job.tenant_id not in {None, self.scope.tenant_id}:
            raise ContextScopeError("Evidence fragment is not visible in the current Context scope.")
        return self._receipt("job_chunk", job_chunk.id, citation_id, job_chunk.text, untrusted=True)

    def load_artifact_excerpt(self, artifact_id: int, *, field: str) -> dict[str, Any]:
        self._authorize("load_artifact_excerpt")
        artifact = self.db.get(AgentArtifact, artifact_id)
        run = self.db.get(AgentRun, artifact.run_id) if artifact is not None else None
        if artifact is None or run is None or run.tenant_id != self.scope.tenant_id or run.user_id != self.scope.user_id:
            raise ContextScopeError("Artifact is not visible in the current Context scope.")
        value = artifact.artifact_json.get(field)
        return self._receipt("artifact", artifact_id, field, value, untrusted=True)

    def load_session_decisions(self, *, memory_type: str = "decision") -> dict[str, Any]:
        self._authorize("load_session_decisions")
        rows = (
            self.db.query(AgentMemory)
            .filter(
                AgentMemory.tenant_id == self.scope.tenant_id,
                AgentMemory.user_id == self.scope.user_id,
                AgentMemory.profile_id == self.scope.profile_id,
                AgentMemory.memory_type == memory_type,
                AgentMemory.status == "active",
                (AgentMemory.expires_at.is_(None) | (AgentMemory.expires_at > datetime.now(UTC))),
            )
            .order_by(AgentMemory.updated_at.desc())
            .limit(10)
            .all()
        )
        return self._receipt("memory", 0, memory_type, [row.value_json for row in rows], untrusted=True)

    def load_prior_run_outcome(self, run_id: int) -> dict[str, Any]:
        self._authorize("load_prior_run_outcome")
        run = self.db.get(AgentRun, run_id)
        if run is None or run.tenant_id != self.scope.tenant_id or run.user_id != self.scope.user_id:
            raise ContextScopeError("Prior Run is not visible in the current Context scope.")
        value = {"status": run.status, "output": run.output_json, "error": run.error_message}
        return self._receipt("agent_run", run_id, "outcome", value, untrusted=False)

    def _receipt(self, source_type: str, source_id: int, selector: str, value: Any, *, untrusted: bool) -> dict[str, Any]:
        if self.calls >= self.max_calls:
            raise ContextBudgetExceededError("Context JIT call budget exceeded.")
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        estimate = self.estimator.count(serialized)
        if estimate.tokens > self.settings.context_jit_max_tokens_per_call:
            max_chars = self.settings.context_jit_max_tokens_per_call * 3
            serialized = serialized[:max_chars]
            value = {"excerpt": serialized, "truncated": True}
            estimate = self.estimator.count(value)
        self.calls += 1
        receipt = {
            "call_no": self.calls,
            "source_type": source_type,
            "source_id": source_id,
            "selector": selector,
            "tokens": estimate.tokens,
            "tokens_estimated": estimate.estimated,
            "untrusted": untrusted,
            "value": value,
            "value_sha256": ContextRuntimeV2._hash(value),
        }
        self.receipts.append({key: value for key, value in receipt.items() if key != "value"})
        return receipt

    def _authorize(self, operation: str) -> None:
        if operation not in self.allowed_operations:
            raise ContextScopeError(f"Current Skill/Tool Policy does not allow JIT operation: {operation}")


def context_contract_manifest() -> list[dict[str, Any]]:
    return [asdict(contract) for contract in CONTEXT_CONTRACTS]
