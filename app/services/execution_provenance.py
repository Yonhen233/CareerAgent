from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.agents.tools import tool_policies_for_names
from app.core.config import Settings, get_settings
from app.services.agent_reliability import TASK_CONTRACT_VERSION


class ExecutionProvenanceService:
    VERSION = "careeragent-execution-provenance-v2"

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
            "runtime_contract_version": "careeragent-tool-runtime-v2",
            "tool_contract_sha256": hashlib.sha256(contract_payload.encode("utf-8")).hexdigest(),
            "tool_names": tool_names,
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
