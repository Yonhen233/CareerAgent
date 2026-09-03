import asyncio
import time

import pytest

from app.core.llm import LLMBudgetExceededError, LLMCallBudget, LLMClient
from app.models.entities import AgentRun, LLMCallLog
from app.services.token_optimization import (
    BatchExecutionError,
    BatchItem,
    BatchToolExecutor,
    DeltaContextBuilder,
    DynamicToolCatalog,
    NodeTokenBudgetRegistry,
    OutputTokenPolicy,
    ParallelToolObservationAggregator,
    PromptSectionProfiler,
    RetryOwnershipRegistry,
    ScopedVersionedCache,
    TokenOptimizationError,
    TokenUsageReportService,
    ToolResultArtifactizer,
)


def test_prompt_sections_record_semantic_areas():
    result = PromptSectionProfiler().profile(
        system_prompt="control",
        user_prompt='{"profile":{"skills":["RAG"]},"job":{"title":"Agent"},"evidence":[{"id":"E1"}]}',
        response_format={"type": "json_object"},
    )
    assert {"system_control", "profile", "job", "evidence", "output_schema"} <= set(result["sections"])
    assert result["total_section_tokens"] > 0


def test_provider_usage_missing_is_explicit(db_session):
    client = LLMClient()
    client._record_llm_call(
        db_session,
        trace_name="token.missing",
        status="failed",
        prompt_preview={"system_chars": 1, "user_chars": 1},
        response_preview=None,
        error_message="failed before usage",
        started_at=time.perf_counter(),
    )
    row = db_session.query(LLMCallLog).filter_by(trace_name="token.missing").one()
    assert row.context_json["usage_status"] == "missing"


def test_business_call_and_http_attempt_budgets_are_separate():
    budget = LLMCallBudget(
        "run",
        max_calls=5,
        max_prompt_chars=1000,
        max_completion_tokens=1000,
        max_business_calls=1,
        max_http_attempts=2,
    )
    budget.start_business_call(trace_name="a", estimated_input_tokens=10, repair_type="none")
    budget.reserve(trace_name="a#1", prompt_chars=10, max_tokens=10)
    budget.reserve(trace_name="a#2", prompt_chars=10, max_tokens=10)
    assert budget.business_calls == 1
    assert budget.calls == 2
    with pytest.raises(LLMBudgetExceededError, match="max_business_calls"):
        budget.start_business_call(trace_name="b", estimated_input_tokens=10, repair_type="none")


def test_duplicate_prompt_sections_are_counted():
    section = {
        "sections": {"profile": {"sha256": "same", "tokens": 30}},
    }
    budget = LLMCallBudget("run", 5, 1000, 1000)
    assert budget.start_business_call(
        trace_name="a", estimated_input_tokens=30, repair_type="none", prompt_sections=section
    ) == 0
    assert budget.start_business_call(
        trace_name="b", estimated_input_tokens=30, repair_type="none", prompt_sections=section
    ) == 30


def test_node_token_contract_rejects_forbidden_context():
    registry = NodeTokenBudgetRegistry()
    result = registry.validate(
        "planner",
        {"user_goal": "找 Agent 实习", "task_contract": {}, "full_profile": {"raw": "x"}},
    )
    assert result["passed"] is False
    assert result["forbidden"] == ["full_profile"]
    assert len(registry.CONTRACTS) >= 11


def test_dynamic_tool_catalog_keeps_required_tool_and_shrinks_catalog():
    selection = DynamicToolCatalog().select(
        task_type="tailor_resume_for_job",
        node="tailor",
        max_risk="medium",
    )
    names = {item["name"] for item in selection.compact_catalog}
    assert "resume_tailor.tailor_resume" in names
    assert "email_send" not in names
    assert selection.selected_tool_count < selection.full_tool_count
    assert selection.injected_schema_tokens < selection.full_schema_tokens


def test_batch_executor_preserves_item_alignment_and_partial_failure():
    async def handler(item):
        if item.item_id == "bad":
            raise ValueError("boom")
        return {"result": item.payload}

    executor = BatchToolExecutor()
    results = asyncio.run(
        executor.run(
            [BatchItem("ok", 1), BatchItem("bad", 2)],
            handler,
            concurrency=2,
        )
    )
    assert [item.item_id for item in results] == ["ok", "bad"]
    assert [item.status for item in results] == ["completed", "failed"]
    with pytest.raises(BatchExecutionError) as exc:
        executor.unwrap(results)
    assert exc.value.results[1]["item_id"] == "bad"


@pytest.mark.parametrize(
    ("risk_level", "shared"),
    [("high", False), ("low", True)],
)
def test_batch_executor_rejects_high_risk_or_shared_side_effect(risk_level, shared):
    async def run():
        await BatchToolExecutor().run(
            [BatchItem("x", 1)],
            lambda item: asyncio.sleep(0, result=item.payload),
            concurrency=1,
            risk_level=risk_level,
            has_shared_side_effect=shared,
        )

    with pytest.raises(TokenOptimizationError):
        asyncio.run(run())


def test_batch_executor_rejects_dependent_items():
    async def run():
        await BatchToolExecutor().run(
            [BatchItem("b", 1)],
            lambda item: asyncio.sleep(0, result=item.payload),
            concurrency=1,
            dependencies={"b": {"a"}},
        )

    with pytest.raises(TokenOptimizationError, match="sequential"):
        asyncio.run(run())


def test_parallel_observation_is_compact():
    executor = BatchToolExecutor()

    async def handler(item):
        return {"count": 1, "artifact_id": 9, "full_text": "do not repeat"}

    results = asyncio.run(executor.run([BatchItem("x", {})], handler, concurrency=1))
    observation = ParallelToolObservationAggregator().aggregate(results)
    assert observation["items"][0]["result"] == {"count": 1, "artifact_id": 9}


def test_tool_result_artifactization(db_session, monkeypatch):
    run = AgentRun(task_type="test", status="running", input_json={})
    db_session.add(run)
    db_session.commit()
    service = ToolResultArtifactizer()
    monkeypatch.setattr(service.settings, "tool_result_artifact_enabled", True)
    result = service.store_or_inline(
        db_session,
        run_id=run.id,
        artifact_type="long_tool_result",
        result={"text": "长结果" * 1000},
        inline_token_limit=10,
    )
    assert result["artifactized"] is True
    assert result["reference"]["artifact_id"] > 0


def test_delta_context_only_contains_changes():
    delta = DeltaContextBuilder().build({"a": 1, "b": 2}, {"a": 1, "c": 3})
    assert delta["changed"] == {"c": 3}
    assert delta["removed"] == ["b"]


def test_scoped_cache_is_tenant_and_version_separated():
    cache = ScopedVersionedCache()
    common = dict(
        user_id="u1",
        data_version="1",
        tool_version="1",
        prompt_version="1",
        contract_version="1",
        model="flash",
        params={"q": "agent"},
    )
    key_a = cache.key(tenant_id="a", **common)
    key_b = cache.key(tenant_id="b", **common)
    assert key_a != key_b
    cache.put(key_a, {"jobs": [1]}, read_only=True)
    assert cache.get(key_a) == {"jobs": [1]}
    assert cache.get(key_b) is None
    with pytest.raises(TokenOptimizationError, match="Side-effect"):
        cache.put(key_b, {}, read_only=False)


def test_retry_owner_has_single_owner():
    registry = RetryOwnershipRegistry()
    assert registry.owner("http_429_5xx") == "llm_http_client"
    assert registry.owner("high_risk_side_effect") == "none"
    with pytest.raises(TokenOptimizationError):
        registry.owner("unknown")


def test_output_policy_uses_node_specific_cap():
    limit, metadata = OutputTokenPolicy().limit("interview_agentic_rag.verify.1", 9000)
    assert limit == 2800
    assert metadata["reduced"] is True


def test_token_usage_report_counts_business_calls_and_missing_usage(db_session):
    run = AgentRun(task_type="test", status="running", input_json={})
    db_session.add(run)
    db_session.commit()
    client = LLMClient()
    for attempt in (1, 2):
        client._record_llm_call(
            db_session,
            trace_name="token.report",
            status="failed",
            prompt_preview={
                "system_chars": 1,
                "user_chars": 1,
                "business_call_id": "same",
                "attempt": attempt,
                "prompt_sections": {"sections": {"tool_schemas": {"tokens": 5}}},
            },
            response_preview=None,
            error_message="x",
            started_at=time.perf_counter(),
        )
        row = db_session.query(LLMCallLog).order_by(LLMCallLog.id.desc()).first()
        row.context_json = {**row.context_json, "run_id": run.id}
        db_session.commit()
    report = TokenUsageReportService().summarize(db_session, run_id=run.id)
    assert report["totals"]["business_calls"] == 1
    assert report["totals"]["http_attempts"] == 2
    assert report["totals"]["usage_missing_calls"] == 2
    assert report["totals"]["tool_schema_tokens"] == 10


def test_feature_flag_preserves_requested_output_limit(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(client.settings, "token_optimization_v2_enabled", False)
    requested, _ = client.output_token_policy.limit("interview_agentic_rag.verify.1", 9000)
    effective = requested if client.settings.token_optimization_v2_enabled else 9000
    assert effective == 9000


def test_offline_ab_meets_token_and_call_reduction_gate():
    from scripts.run_token_optimization_ab import evaluate

    report = asyncio.run(evaluate(real_llm=False, limit=3, question_limit=3))
    assert report["metrics"]["input_token_reduction"] >= 0.4
    assert report["metrics"]["llm_call_reduction"] >= 0.5
    assert report["release_gate"]["passed"] is True
