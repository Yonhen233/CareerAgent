from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.agents.tools import tool_policies_for_names
from app.agents.subagents import roles_for_task
from app.agents.prompt_registry import prompt_registry_manifest
from app.agents.skills import skill_contracts_for_task
from app.core.config import Settings, get_settings
from app.services.agent_reliability import TASK_CONTRACT_VERSION
from app.services.context_runtime import ContextRuntimeV2, context_contract_manifest
from app.services.token_optimization import NodeTokenBudgetRegistry


class ExecutionProvenanceService:
    VERSION = "careeragent-execution-provenance-v5"

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def build(self, *, task_type: str, plan: dict[str, Any]) -> dict[str, Any]:
        tool_names = [str(step.get("tool")) for step in plan.get("steps") or [] if step.get("tool")]
        contracts = tool_policies_for_names(tool_names)
        contract_payload = json.dumps(contracts, ensure_ascii=False, sort_keys=True, default=str)
        return {
            "version": self.VERSION,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "application": {
                "name": self.settings.app_name,
                "version": self.settings.app_version,
                "environment": self.settings.app_env,
            },
            "task_type": task_type,
            "orchestrator": "langgraph",
            "harness_version": "careeragent-harness-v3",
            "runtime_contract_version": "careeragent-tool-runtime-v3",
            "tool_contract_sha256": hashlib.sha256(contract_payload.encode("utf-8")).hexdigest(),
            "tool_names": tool_names,
            "tool_inventory": {
                "direct": [item["name"] for item in contracts if item.get("invocation_scope") == "direct"],
                "embedded": [item["name"] for item in contracts if item.get("invocation_scope") == "embedded"],
            },
            "skill_versions": {
                item["name"]: item["version"] for item in skill_contracts_for_task(task_type)
            },
            "prompt_registry": prompt_registry_manifest(),
            "context_runtime": {
                "version": ContextRuntimeV2.VERSION,
                "active": self.settings.context_runtime_v2_enabled,
                "shadow_mode": self.settings.context_runtime_v2_shadow_mode,
                "contracts": context_contract_manifest(),
                "token_limits": {
                    "model_window": self.settings.context_model_window_tokens,
                    "soft_ratio": self.settings.context_token_soft_limit_ratio,
                    "high_ratio": self.settings.context_token_high_limit_ratio,
                    "hard_ratio": self.settings.context_token_hard_limit_ratio,
                    "output_reserve": self.settings.context_output_reserve_tokens,
                },
            },
            "token_optimization": {
                "version": "careeragent-token-optimization-v2",
                "active": self.settings.token_optimization_v2_enabled,
                "shadow_mode": self.settings.token_optimization_shadow_mode,
                "dynamic_tool_catalog": self.settings.dynamic_tool_catalog_enabled,
                "batch_tool_calls": self.settings.batch_tool_calls_enabled,
                "parallel_tool_calls": self.settings.parallel_tool_calls_enabled,
                "tool_result_artifact": self.settings.tool_result_artifact_enabled,
                "delta_context": self.settings.delta_context_enabled,
                "node_contracts": sorted(NodeTokenBudgetRegistry.CONTRACTS),
                "run_limits": {
                    "business_calls": self.settings.llm_max_calls_per_run,
                    "http_attempts": self.settings.llm_max_attempts_per_run,
                    "repair_calls": self.settings.llm_max_repair_calls,
                    "input_tokens": self.settings.llm_max_input_tokens_per_run,
                    "output_tokens": self.settings.llm_max_output_tokens_per_run,
                    "total_tokens": self.settings.llm_max_total_tokens_per_run,
                },
            },
            "roles": [item["name"] for item in roles_for_task(task_type)],
            "checkpoint_policy": {
                "backend": self.settings.langgraph_checkpoint_backend,
                "shared_backend_required_in_production": True,
            },
            "model_policy": {
                "version": "careeragent-model-routing-v2",
                "routing_enabled": self.settings.llm_routing_enabled,
                "default_model": self.settings.llm_model,
                "flash_model": self.settings.llm_flash_model,
                "pro_model": self.settings.llm_pro_model,
                "thinking_mode": self.settings.llm_thinking_mode,
            },
            "retrieval_policy": {
                "version": "careeragent-hybrid-rag-v2",
                "vector_backend": self.settings.vector_backend,
                "embedding_model": self.settings.embedding_model_name,
                "reranker_model": self.settings.reranker_model_name,
                "multi_query": self.settings.rag_multi_query_enabled,
                "rrf_k": self.settings.rag_multi_query_rrf_k,
            },
            "safety_policy": {
                "completion_gate": TASK_CONTRACT_VERSION,
                "prompt_injection_detector": self.settings.prompt_injection_classifier_enabled,
                "diagnostic_pii_redaction": self.settings.diagnostic_redact_pii,
            },
        }
