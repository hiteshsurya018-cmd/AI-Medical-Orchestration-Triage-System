from __future__ import annotations

from ..workflow import CaseWorkflowState


class MemoryAgent:
    name = "memory-agent"

    def hydrate(
        self,
        state: CaseWorkflowState,
        *,
        profile,
        stored_age: int | None,
        stored_history: str,
    ) -> CaseWorkflowState:
        state.known_context = {
            "profile_found": bool(profile),
            "history_loaded": bool(stored_history),
            "used_age": stored_age,
        }
        if stored_age is not None:
            state.intake_data["patient_age"] = stored_age
        if stored_history:
            state.intake_data["medical_history"] = stored_history
        state.record(self.name, "loaded patient memory and longitudinal context")
        return state
