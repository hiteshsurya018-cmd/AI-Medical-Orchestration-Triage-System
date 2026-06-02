from __future__ import annotations

from typing import Any

from ..clinical_runtime import evaluate_vitals
from ..workflow import CaseWorkflowState


class VitalsAgent:
    name = "vitals-agent"

    def process(self, state: CaseWorkflowState, vitals_payload: dict[str, Any] | None = None) -> CaseWorkflowState:
        evaluation = evaluate_vitals(vitals_payload)
        state.intake_data["vitals"] = evaluation["vitals"]
        state.known_context["vitals_loaded"] = any(value not in (None, "") for value in evaluation["vitals"].values())
        state.known_context["vitals_evaluation"] = evaluation
        if evaluation["level"] == "critical":
            state.add_reason("critical vital sign threshold crossed")
        elif evaluation["level"] == "urgent":
            state.add_reason("urgent vital sign threshold crossed")
        state.record(self.name, f"vitals evaluated as {evaluation['level']}")
        return state
