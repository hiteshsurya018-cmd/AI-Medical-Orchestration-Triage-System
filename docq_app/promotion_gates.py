from __future__ import annotations

from .contracts import EvaluationDriftSnapshot, PromotionGateResult


def evaluate_promotion_gate(
    *,
    profile_key: str,
    gate_rules: dict[str, float | bool],
    replay_integrity_passed: bool,
    drift_snapshot: EvaluationDriftSnapshot,
) -> PromotionGateResult:
    violated_rules: list[str] = []
    evidence: list[str] = []
    require_replay_integrity = bool(gate_rules.get("require_replay_integrity", True))
    if require_replay_integrity and not replay_integrity_passed:
        violated_rules.append("replay_integrity")
        evidence.append("Replay integrity did not pass for the evaluation scope.")
    if float(drift_snapshot.false_negative_delta) > float(gate_rules.get("max_false_negative_delta", 0.0)):
        violated_rules.append("false_negative_delta")
        evidence.append(
            f"False-negative delta {drift_snapshot.false_negative_delta:.2f} exceeded gate {float(gate_rules.get('max_false_negative_delta', 0.0)):.2f}."
        )
    if float(drift_snapshot.review_rate_delta) > float(gate_rules.get("max_review_rate_delta", 10.0)):
        violated_rules.append("review_rate_delta")
        evidence.append(
            f"Review-rate delta {drift_snapshot.review_rate_delta:.2f} exceeded gate {float(gate_rules.get('max_review_rate_delta', 10.0)):.2f}."
        )
    if float(drift_snapshot.escalation_delta) > float(gate_rules.get("max_escalation_delta", 10.0)):
        violated_rules.append("escalation_delta")
        evidence.append(
            f"Escalation delta {drift_snapshot.escalation_delta:.2f} exceeded gate {float(gate_rules.get('max_escalation_delta', 10.0)):.2f}."
        )
    min_calibration_improvement = float(gate_rules.get("min_calibration_improvement", -5.0))
    observed_improvement = 0.0 - float(drift_snapshot.calibration_error_delta)
    if observed_improvement < min_calibration_improvement:
        violated_rules.append("calibration_improvement")
        evidence.append(
            f"Calibration improvement {observed_improvement:.2f} did not meet gate {min_calibration_improvement:.2f}."
        )
    passed = not violated_rules
    recommendation = "Promote candidate safely." if passed else "Hold promotion and review violated gate rules."
    return PromotionGateResult(
        profile_key=profile_key,
        passed=passed,
        violated_rules=violated_rules,
        supporting_evidence=evidence,
        confidence_metrics={
            "false_negative_delta": drift_snapshot.false_negative_delta,
            "review_rate_delta": drift_snapshot.review_rate_delta,
            "escalation_delta": drift_snapshot.escalation_delta,
            "calibration_error_delta": drift_snapshot.calibration_error_delta,
        },
        recommendation_summary=recommendation,
    )
