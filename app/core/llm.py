import asyncio
import hashlib
import json
import math
import re
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from typing import Any

import httpx

from app.agents.prompt_registry import PromptRegistry
from app.core.config import get_settings
from app.core.redaction import SecurityRedactor
from app.services.context_runtime import ContextRuntimeV2, ContextScope
from app.services.token_optimization import OutputTokenPolicy, PromptSectionProfiler

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class LLMConfigurationError(RuntimeError):
    """Raised when an online LLM call is requested without configuration."""


class LLMResponseError(RuntimeError):
    """Raised when the LLM endpoint returns an unusable response."""


class LLMBudgetExceededError(RuntimeError):
    """Raised before an LLM call would exceed the active workflow budget."""


class _RetryableLLMResponseError(LLMResponseError):
    """Raised for transient LLM HTTP responses that are worth retrying."""


_LLM_TRACE_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("llm_trace_context", default={})
_LLM_CALL_BUDGET: ContextVar["LLMCallBudget | None"] = ContextVar("llm_call_budget", default=None)


@dataclass
class LLMCallBudget:
    name: str
    max_calls: int
    max_prompt_chars: int
    max_completion_tokens: int
    calls: int = 0
    prompt_chars: int = 0
    reserved_completion_tokens: int = 0
    actual_prompt_tokens: int = 0
    actual_completion_tokens: int = 0
    actual_total_tokens: int = 0
    business_calls: int = 0
    repair_calls: int = 0
    estimated_input_tokens: int = 0
    max_business_calls: int | None = None
    max_http_attempts: int | None = None
    max_repair_calls: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_total_tokens: int | None = None
    duplicate_context_tokens: int = 0
    section_hashes: set[str] = field(default_factory=set)
    traces: list[str] = field(default_factory=list)
    parent: "LLMCallBudget | None" = field(default=None, repr=False)

    def start_business_call(
        self,
        *,
        trace_name: str,
        estimated_input_tokens: int,
        repair_type: str,
        prompt_sections: dict[str, Any] | None = None,
    ) -> int:
        section_rows = (prompt_sections or {}).get("sections") or {}
        duplicate_for_active = 0
        for budget in self._budget_chain():
            next_business_calls = budget.business_calls + 1
            next_repair_calls = budget.repair_calls + int(repair_type != "none")
            next_input_tokens = budget.estimated_input_tokens + max(0, estimated_input_tokens)
            if budget.max_business_calls is not None and next_business_calls > budget.max_business_calls:
                raise LLMBudgetExceededError(
                    f"LLM budget {budget.name} exceeds max_business_calls={budget.max_business_calls} "
                    f"before {trace_name}."
                )
            for section in section_rows.values():
                section_hash = str(section.get("sha256") or "")
                section_tokens = int(section.get("tokens") or 0)
                if section_hash and section_hash in budget.section_hashes:
                    budget.duplicate_context_tokens += section_tokens
                    if budget is self:
                        duplicate_for_active += section_tokens
            if budget.max_repair_calls is not None and next_repair_calls > budget.max_repair_calls:
                raise LLMBudgetExceededError(
                    f"LLM budget {budget.name} exceeds max_repair_calls={budget.max_repair_calls} "
                    f"before {trace_name}."
                )
            if budget.max_input_tokens is not None and next_input_tokens > budget.max_input_tokens:
                raise LLMBudgetExceededError(
                    f"LLM budget {budget.name} exceeds max_input_tokens={budget.max_input_tokens} "
                    f"before {trace_name}."
                )
        for budget in self._budget_chain():
            budget.business_calls += 1
            budget.repair_calls += int(repair_type != "none")
            budget.estimated_input_tokens += max(0, estimated_input_tokens)
            budget.section_hashes.update(
                str(section.get("sha256"))
                for section in section_rows.values()
                if section.get("sha256")
            )
        return duplicate_for_active

    def reserve(self, *, trace_name: str, prompt_chars: int, max_tokens: int | None) -> None:
        completion_tokens = max(0, int(max_tokens or 0))
        chain = self._budget_chain()
        for budget in chain:
            budget._assert_capacity(
                trace_name=trace_name,
                prompt_chars=prompt_chars,
                completion_tokens=completion_tokens,
            )
        for budget in chain:
            budget.calls += 1
            budget.prompt_chars += prompt_chars
            budget.reserved_completion_tokens += completion_tokens
            budget.traces.append(trace_name)

    def _assert_capacity(
        self,
        *,
        trace_name: str,
        prompt_chars: int,
        completion_tokens: int,
    ) -> None:
        if self.max_http_attempts is not None and self.calls + 1 > self.max_http_attempts:
            raise LLMBudgetExceededError(
                f"LLM budget {self.name} exceeds max_http_attempts={self.max_http_attempts} before {trace_name}."
            )
        if self.calls + 1 > self.max_calls:
            raise LLMBudgetExceededError(
                f"LLM budget {self.name} exceeds max_calls={self.max_calls} before {trace_name}."
            )
        if self.prompt_chars + prompt_chars > self.max_prompt_chars:
            raise LLMBudgetExceededError(
                f"LLM budget {self.name} exceeds max_prompt_chars={self.max_prompt_chars} before {trace_name}."
            )
        if self.reserved_completion_tokens + completion_tokens > self.max_completion_tokens:
            raise LLMBudgetExceededError(
                "LLM budget "
                f"{self.name} exceeds max_completion_tokens={self.max_completion_tokens} before {trace_name}."
            )

    def _budget_chain(self) -> list["LLMCallBudget"]:
        chain: list[LLMCallBudget] = []
        current: LLMCallBudget | None = self
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            chain.append(current)
            current = current.parent
        return chain

    def record_usage(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None:
        for budget in self._budget_chain():
            next_output = budget.actual_completion_tokens + max(0, completion_tokens)
            next_total = budget.actual_total_tokens + max(0, total_tokens)
            if budget.max_output_tokens is not None and next_output > budget.max_output_tokens:
                raise LLMBudgetExceededError(
                    f"LLM budget {budget.name} exceeds max_output_tokens={budget.max_output_tokens}."
                )
            if budget.max_total_tokens is not None and next_total > budget.max_total_tokens:
                raise LLMBudgetExceededError(
                    f"LLM budget {budget.name} exceeds max_total_tokens={budget.max_total_tokens}."
                )
            budget.actual_prompt_tokens += max(0, prompt_tokens)
            budget.actual_completion_tokens += max(0, completion_tokens)
            budget.actual_total_tokens += max(0, total_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "limits": {
                "max_calls": self.max_calls,
                "max_prompt_chars": self.max_prompt_chars,
                "max_completion_tokens": self.max_completion_tokens,
                "max_business_calls": self.max_business_calls,
                "max_http_attempts": self.max_http_attempts,
                "max_repair_calls": self.max_repair_calls,
                "max_input_tokens": self.max_input_tokens,
                "max_output_tokens": self.max_output_tokens,
                "max_total_tokens": self.max_total_tokens,
            },
            "reserved": {
                "calls": self.calls,
                "business_calls": self.business_calls,
                "repair_calls": self.repair_calls,
                "prompt_chars": self.prompt_chars,
                "completion_tokens": self.reserved_completion_tokens,
                "estimated_input_tokens": self.estimated_input_tokens,
                "duplicate_context_tokens": self.duplicate_context_tokens,
            },
            "actual": {
                "prompt_tokens": self.actual_prompt_tokens,
                "completion_tokens": self.actual_completion_tokens,
                "total_tokens": self.actual_total_tokens,
            },
            "traces": list(self.traces),
            "parent_budget": self.parent.name if self.parent is not None else None,
        }


@dataclass(frozen=True)
class LLMRoute:
    name: str
    model: str
    max_tokens_multiplier: float = 1.0


@contextmanager
def llm_trace_context(**metadata: Any):
    parent = dict(_LLM_TRACE_CONTEXT.get() or {})
    clean = {key: value for key, value in metadata.items() if value is not None}
    token = _LLM_TRACE_CONTEXT.set({**parent, **clean})
    try:
        yield
    finally:
        _LLM_TRACE_CONTEXT.reset(token)


@contextmanager
def llm_call_budget(budget: LLMCallBudget):
    parent = _LLM_CALL_BUDGET.get()
    if parent is not budget and budget.parent is None:
        budget.parent = parent
    token = _LLM_CALL_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _LLM_CALL_BUDGET.reset(token)


def format_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return f"{exc.__class__.__name__}: {repr(exc)}"


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise LLMResponseError("LLM response did not contain a JSON object.")

    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise LLMResponseError("LLM JSON response must be an object.")
    return parsed


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.redactor = SecurityRedactor()
        self.prompt_registry = PromptRegistry()
        self.context_runtime_v2 = ContextRuntimeV2()
        self.prompt_section_profiler = PromptSectionProfiler()
        self.output_token_policy = OutputTokenPolicy()

    @property
    def available(self) -> bool:
        return bool(self.settings.effective_llm_api_key and self.settings.effective_llm_base_url)

    def _chat_url(self) -> str:
        base = self.settings.effective_llm_base_url.rstrip("/")
        if not base:
            raise LLMConfigurationError("LLM_BASE_URL is not configured.")
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def resolve_route(self, trace_name: str) -> LLMRoute:
        if not self.settings.llm_routing_enabled:
            return LLMRoute(name="configured_default", model=self.settings.llm_model)
        for prefix in self.settings.llm_pro_trace_prefix_list:
            if trace_name.startswith(prefix):
                return LLMRoute(name="pro_quality", model=self.settings.llm_pro_model)
        for prefix in self.settings.llm_flash_trace_prefix_list:
            if trace_name.startswith(prefix):
                return LLMRoute(
                    name="flash_economy",
                    model=self.settings.llm_flash_model,
                    max_tokens_multiplier=max(1.0, self.settings.llm_flash_max_tokens_multiplier),
                )
        return LLMRoute(name="configured_default", model=self.settings.llm_model)

    @staticmethod
    def effective_max_tokens(max_tokens: int | None, route: LLMRoute) -> int | None:
        if max_tokens is None:
            return None
        return max(1, math.ceil(max_tokens * route.max_tokens_multiplier))

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        db: "Session | None" = None,
        trace_name: str = "llm.generate_text",
        prompt_sections: dict[str, Any] | None = None,
    ) -> str:
        started = time.perf_counter()
        business_call_id = uuid.uuid4().hex
        repair_type = self._repair_type(trace_name)
        route = self.resolve_route(trace_name)
        effective_max_tokens = self.effective_max_tokens(max_tokens, route)
        policy_max_tokens, output_policy = self.output_token_policy.limit(trace_name, effective_max_tokens)
        if self.settings.token_optimization_v2_enabled:
            effective_max_tokens = policy_max_tokens
        prepared_prompt = self.prompt_registry.prepare(
            trace_name=trace_name,
            system_prompt=system_prompt,
        )
        effective_system_prompt = prepared_prompt.system_prompt
        runtime_context = dict(_LLM_TRACE_CONTEXT.get() or {})
        context_v2_result = None
        if self.settings.context_runtime_v2_enabled or self.settings.context_runtime_v2_shadow_mode:
            try:
                context_v2_result = self.context_runtime_v2.observe_text_prompt(
                    trace_name=trace_name,
                    system_prompt=effective_system_prompt,
                    user_prompt=user_prompt,
                    run_id=self._optional_int(runtime_context.get("run_id")),
                    task_type=str(runtime_context.get("task_type") or ""),
                    scope=ContextScope(
                        tenant_id=str(
                            runtime_context.get("tenant_id") or self.settings.rbac_default_tenant_id
                        ),
                        user_id=str(runtime_context.get("user_id") or "runtime"),
                        profile_id=self._optional_int(runtime_context.get("profile_id")),
                    ),
                    prompt_version=str(prepared_prompt.provenance.get("prompt_version") or "unregistered"),
                    skill_versions=dict(prepared_prompt.provenance.get("skill_versions") or {}),
                )
                if self.settings.context_runtime_v2_enabled and context_v2_result is not None:
                    effective_system_prompt = str(
                        context_v2_result.packet.get("control_context", {}).get(
                            "system_prompt", effective_system_prompt
                        )
                    )
                    user_prompt = str(
                        context_v2_result.packet.get("working_context", {}).get(
                            "user_prompt", user_prompt
                        )
                    )
            except Exception as exc:
                if self.settings.context_runtime_v2_enabled:
                    raise
                context_v2_result = {"shadow_error": format_exception(exc)}
        prompt_preview = self._prompt_preview(
            effective_system_prompt,
            user_prompt,
            temperature,
            effective_max_tokens,
            response_format,
        )
        prompt_preview.update(
            {
                "requested_max_tokens": max_tokens,
                "model_route": route.name,
                "routed_model": route.model,
                "max_tokens_multiplier": route.max_tokens_multiplier,
                "business_call_id": business_call_id,
                "repair_type": repair_type,
                "output_token_policy": output_policy,
                **prepared_prompt.provenance,
            }
        )
        prompt_preview["prompt_sections"] = self.prompt_section_profiler.profile(
            system_prompt=effective_system_prompt,
            user_prompt=user_prompt,
            skill_policy_chars=int(prepared_prompt.provenance.get("skill_policy_chars") or 0),
            response_format=response_format,
            explicit_sections=prompt_sections,
        )
        if context_v2_result is not None:
            prompt_preview["context_runtime_v2"] = (
                context_v2_result.trace if hasattr(context_v2_result, "trace") else context_v2_result
            )
        if not self.available:
            error = "LLM_API_KEY and LLM_BASE_URL are required for online generation."
            self._record_llm_call(
                db,
                trace_name=trace_name,
                status="configuration_error",
                prompt_preview=prompt_preview,
                response_preview=None,
                error_message=error,
                started_at=started,
                model=route.model,
                route_name=route.name,
            )
            raise LLMConfigurationError(error)

        active_budget = _LLM_CALL_BUDGET.get()
        prompt_chars = int(prompt_preview.get("system_chars", 0)) + int(
            prompt_preview.get("user_chars", 0)
        )
        estimated_input_tokens = int(
            (prompt_preview.get("prompt_sections") or {}).get("total_section_tokens") or 0
        )
        if active_budget is not None:
            duplicate_context_tokens = active_budget.start_business_call(
                trace_name=trace_name,
                estimated_input_tokens=estimated_input_tokens,
                repair_type=repair_type,
                prompt_sections=prompt_preview.get("prompt_sections"),
            )
            prompt_preview["duplicate_context_tokens"] = duplicate_context_tokens
        headers = {
            "Authorization": f"Bearer {self.settings.effective_llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": route.model,
            "messages": [
                {"role": "system", "content": effective_system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if effective_max_tokens is not None:
            payload["max_tokens"] = effective_max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        payload.update(self._provider_options(model=route.model))

        max_attempts = max(1, int(self.settings.llm_retry_attempts or 0) + 1)
        for attempt in range(1, max_attempts + 1):
            attempt_preview = {**prompt_preview, "attempt": attempt, "max_attempts": max_attempts}
            if active_budget is not None:
                try:
                    active_budget.reserve(
                        trace_name=f"{trace_name}#attempt{attempt}",
                        prompt_chars=prompt_chars,
                        max_tokens=effective_max_tokens,
                    )
                except LLMBudgetExceededError as exc:
                    self._record_llm_call(
                        db,
                        trace_name=trace_name,
                        status="budget_exceeded",
                        prompt_preview=attempt_preview,
                        response_preview=None,
                        error_message=format_exception(exc),
                        started_at=started,
                        model=route.model,
                        route_name=route.name,
                    )
                    raise
            try:
                async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                    response = await client.post(self._chat_url(), headers=headers, json=payload)
                if response.status_code >= 400:
                    error = f"LLM request failed with HTTP {response.status_code}: {response.text[:500]}"
                    if response.status_code in {408, 409, 429} or response.status_code >= 500:
                        raise _RetryableLLMResponseError(error)
                    raise LLMResponseError(error)

                body = response.json()
                try:
                    choice = body["choices"][0]
                    message = choice["message"]
                    content = message["content"]
                except (KeyError, IndexError, TypeError) as exc:
                    raise LLMResponseError("LLM response is missing choices[0].message.content.") from exc

                if not isinstance(content, str) or not content.strip():
                    reasoning_content = message.get("reasoning_content") if isinstance(message, dict) else None
                    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
                    reasoning_chars = len(reasoning_content) if isinstance(reasoning_content, str) else 0
                    raise LLMResponseError(
                        "LLM returned empty content "
                        f"(finish_reason={finish_reason}, reasoning_chars={reasoning_chars}, "
                        f"thinking_mode={self.settings.llm_thinking_mode})."
                    )
                content = content.strip()
                raw_usage = body.get("usage") if isinstance(body, dict) else None
                usage = raw_usage if isinstance(raw_usage, dict) else {}
                prompt_tokens = self._usage_int(usage, "prompt_tokens", "input_tokens")
                completion_tokens = self._usage_int(usage, "completion_tokens", "output_tokens")
                total_tokens = self._usage_int(usage, "total_tokens")
                provider_usage = self._provider_usage_details(usage)
                if total_tokens <= 0:
                    total_tokens = prompt_tokens + completion_tokens
                if active_budget is not None:
                    active_budget.record_usage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                    )
                if context_v2_result is not None and hasattr(context_v2_result, "trace"):
                    estimated = int(context_v2_result.trace.get("final_input_tokens") or 0)
                    self.context_runtime_v2.estimator.calibrate(
                        estimated_tokens=estimated,
                        actual_prompt_tokens=prompt_tokens,
                    )
                self._record_llm_call(
                    db,
                    trace_name=trace_name,
                    status="completed",
                    prompt_preview=attempt_preview,
                    response_preview=content[:1200],
                    response_chars=len(content),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    provider_usage=provider_usage if isinstance(raw_usage, dict) else None,
                    error_message=None,
                    started_at=started,
                    model=route.model,
                    route_name=route.name,
                )
                return content
            except Exception as exc:
                retryable = isinstance(exc, (httpx.TransportError, _RetryableLLMResponseError))
                will_retry = retryable and attempt < max_attempts
                error_message = format_exception(exc)
                self._record_llm_call(
                    db,
                    trace_name=trace_name,
                    status="retryable_failed" if will_retry else "failed",
                    prompt_preview=attempt_preview,
                    response_preview=None,
                    error_message=error_message,
                    started_at=started,
                    model=route.model,
                    route_name=route.name,
                )
                if will_retry:
                    await asyncio.sleep(max(self.settings.llm_retry_backoff_seconds, 0) * attempt)
                    continue
                raise

        raise LLMResponseError("LLM request exhausted without producing a response.")

    @staticmethod
    def _usage_int(usage: dict[str, Any], *keys: str) -> int:
        for key in keys:
            try:
                value = int(usage.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value >= 0:
                return value
        return 0

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _provider_usage_details(cls, usage: dict[str, Any]) -> dict[str, int]:
        aliases = {
            "prompt_cache_hit_tokens": ("prompt_cache_hit_tokens", "cache_read_input_tokens"),
            "prompt_cache_miss_tokens": ("prompt_cache_miss_tokens", "cache_creation_input_tokens"),
            "reasoning_tokens": ("reasoning_tokens",),
        }
        details: dict[str, int] = {}
        for target, keys in aliases.items():
            value = cls._usage_int(usage, *keys)
            if value > 0:
                details[target] = value
        completion_details = usage.get("completion_tokens_details")
        if isinstance(completion_details, dict):
            reasoning_tokens = cls._usage_int(completion_details, "reasoning_tokens")
            if reasoning_tokens > 0:
                details["reasoning_tokens"] = reasoning_tokens
        return details

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        db: "Session | None" = None,
        trace_name: str = "llm.generate_json",
        prompt_sections: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = await self.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            db=db,
            trace_name=trace_name,
            prompt_sections=prompt_sections,
        )
        return extract_json_object(text)

    def _prompt_preview(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int | None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        bundle = json.dumps(
            {"system": system_prompt, "user": user_prompt, "response_format": response_format},
            ensure_ascii=False,
            sort_keys=True,
        )
        return {
            "system_preview": self.redactor.redact(
                system_prompt[:1000], redact_pii=self.settings.diagnostic_redact_pii
            ),
            "user_preview": self.redactor.redact(
                user_prompt[:1600], redact_pii=self.settings.diagnostic_redact_pii
            ),
            "system_chars": len(system_prompt),
            "user_chars": len(user_prompt),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format,
            "prompt_bundle_sha256": hashlib.sha256(bundle.encode("utf-8")).hexdigest(),
            "prompt_contract_version": "careeragent-prompt-observability-v2",
            "routing_policy_version": "careeragent-model-routing-v2",
        }

    def _provider_options(self, *, model: str | None = None) -> dict[str, Any]:
        mode = (self.settings.llm_thinking_mode or "auto").strip().lower()
        if mode in {"omit", "none", "off"}:
            return {}

        base_url = self.settings.effective_llm_base_url.lower()
        selected_model = (model or self.settings.llm_model).lower()
        is_deepseek_v4 = "api.deepseek.com" in base_url and selected_model.startswith("deepseek-v4")
        if mode == "auto" and not is_deepseek_v4:
            return {}

        if mode == "enabled":
            return {
                "thinking": {"type": "enabled"},
                "reasoning_effort": self.settings.llm_reasoning_effort,
            }
        if mode in {"disabled", "auto"}:
            return {"thinking": {"type": "disabled"}}

        return {}

    def _record_llm_call(
        self,
        db: "Session | None",
        *,
        trace_name: str,
        status: str,
        prompt_preview: dict[str, Any],
        response_preview: str | None,
        error_message: str | None,
        started_at: float,
        response_chars: int | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        provider_usage: dict[str, int] | None = None,
        model: str | None = None,
        route_name: str | None = None,
    ) -> None:
        if db is None:
            return
        try:
            from app.models.entities import ContextCompressionTrace, LLMCallLog

            context = dict(_LLM_TRACE_CONTEXT.get() or {})
            if route_name:
                context["model_route"] = route_name
            if model:
                context["routed_model"] = model
            if provider_usage:
                context["provider_usage"] = dict(provider_usage)
            context.update(
                {
                    "business_call_id": prompt_preview.get("business_call_id"),
                    "run_id": self._optional_int(
                        context.get("run_id")
                        or context.get("agent_run_id")
                        or context.get("workflow_run_id")
                    ),
                    "repair_type": prompt_preview.get("repair_type", "none"),
                    "usage_status": (
                        "provider_reported"
                        if provider_usage is not None
                        else "missing"
                    ),
                    "cached_tokens": int((provider_usage or {}).get("cached_tokens") or 0),
                    "reasoning_tokens": int((provider_usage or {}).get("reasoning_tokens") or 0),
                    "context_compression_version": (
                        (prompt_preview.get("context_runtime_v2") or {}).get("context_runtime_version")
                        if isinstance(prompt_preview.get("context_runtime_v2"), dict)
                        else None
                    ),
                    "output_token_limit": prompt_preview.get("max_tokens"),
                    "duplicate_context_tokens": int(
                        prompt_preview.get("duplicate_context_tokens") or 0
                    ),
                    "graph_node": context.get("graph_node") or context.get("stage") or trace_name,
                    "node": context.get("graph_node") or context.get("stage") or trace_name,
                    "batch_id": context.get("batch_id")
                    or prompt_preview.get("business_call_id"),
                }
            )
            db.add(
                LLMCallLog(
                    trace_name=trace_name,
                    model=model or self.settings.llm_model,
                    base_url=self.settings.effective_llm_base_url,
                    status=status,
                    prompt_preview_json=prompt_preview,
                    response_preview=self.redactor.redact(
                        response_preview,
                        redact_pii=self.settings.diagnostic_redact_pii,
                    ),
                    error_message=self.redactor.redact(error_message),
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                    prompt_chars=int(prompt_preview.get("system_chars", 0))
                    + int(prompt_preview.get("user_chars", 0)),
                    response_chars=response_chars if response_chars is not None else len(response_preview or ""),
                    prompt_tokens=max(0, int(prompt_tokens or 0)),
                    completion_tokens=max(0, int(completion_tokens or 0)),
                    total_tokens=max(0, int(total_tokens or 0)),
                    context_json=context,
                )
            )
            context_trace = prompt_preview.get("context_runtime_v2")
            if isinstance(context_trace, dict) and context_trace.get("contract_name"):
                db.add(
                    ContextCompressionTrace(
                        run_id=self._optional_int(context_trace.get("run_id")),
                        node=str(context_trace.get("node") or trace_name),
                        task_type=str(context_trace.get("task_type") or "unknown"),
                        runtime_version=str(context_trace.get("context_runtime_version") or "v2"),
                        contract_name=str(context_trace.get("contract_name") or "unknown"),
                        contract_version=str(context_trace.get("contract_version") or "unknown"),
                        mode=(
                            "active"
                            if self.settings.context_runtime_v2_enabled
                            else "shadow"
                        ),
                        raw_input_tokens=int(context_trace.get("raw_input_tokens") or 0),
                        final_input_tokens=int(context_trace.get("final_input_tokens") or 0),
                        actual_prompt_tokens=max(0, int(prompt_tokens or 0)),
                        actual_completion_tokens=max(0, int(completion_tokens or 0)),
                        actual_total_tokens=max(0, int(total_tokens or 0)),
                        critical_fact_recall=float(context_trace.get("critical_fact_recall") or 0.0),
                        quality_gate_passed=bool(context_trace.get("quality_gate_passed")),
                        latency_ms=float(context_trace.get("latency_ms") or 0.0),
                        trace_json=context_trace,
                    )
                )
            db.commit()
        except Exception:
            db.rollback()

    @staticmethod
    def _repair_type(trace_name: str) -> str:
        lowered = trace_name.lower()
        if "json_repair" in lowered:
            return "json_repair"
        if "contract_repair" in lowered:
            return "contract_repair"
        if "repair" in lowered:
            return "quality_repair"
        return "none"
