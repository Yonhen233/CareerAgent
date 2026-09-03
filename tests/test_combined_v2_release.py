from __future__ import annotations

import asyncio

from app.core.config import Settings
from scripts.run_combined_v2_ab import evaluate


def test_v2_features_are_production_defaults(monkeypatch) -> None:
    for key in (
        "CONTEXT_RUNTIME_V2_ENABLED",
        "CONTEXT_RUNTIME_V2_SHADOW_MODE",
        "TOKEN_OPTIMIZATION_V2_ENABLED",
        "TOKEN_OPTIMIZATION_SHADOW_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.context_runtime_v2_enabled is True
    assert settings.context_runtime_v2_shadow_mode is False
    assert settings.token_optimization_v2_enabled is True
    assert settings.token_optimization_shadow_mode is False


def test_combined_v2_offline_release_gate_passes() -> None:
    report = asyncio.run(
        evaluate(
            real_llm=False,
            context_limit=3,
            token_limit=3,
            question_limit=3,
        )
    )
    assert report["release_gate"]["passed"] is True
    assert report["metrics"]["input_token_reduction"] >= 0.4
    assert report["metrics"]["business_call_reduction"] >= 0.4
    assert report["metrics"]["prompt_injection_escape_count"] == 0
    assert report["metrics"]["cross_tenant_leakage_count"] == 0
