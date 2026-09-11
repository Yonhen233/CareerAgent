from pathlib import Path

from app.services.agent_efficiency_evaluator import AgentEfficiencyEvaluator


def test_fault_injection_contract_passes_all_runtime_guards():
    report = AgentEfficiencyEvaluator(base_path=Path(__file__).resolve().parents[1]).run_fault_injection()

    assert report["pass_rate"] == 1.0
    assert report["release_gates"]["illegal_tool_blocking_rate"] == 1.0
    assert report["release_gates"]["duplicate_side_effects"] == 0
    assert report["release_gates"]["replan_completed_node_rewrite"] == 0


def test_cache_ab_preserves_scores_and_uses_single_flight():
    report = AgentEfficiencyEvaluator().run_cache_ab(iterations=5, concurrent_calls=4)

    assert report["equivalence"] is True
    assert report["single_flight_passed"] is True
    assert report["scope_cross_hit_count"] == 0
    assert report["model_call_reduction_rate"] > 0.5
