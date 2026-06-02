from __future__ import annotations

import datetime as dt

from ..tools import ToolRegistry, build_default_registry
from ..workflow import CaseWorkflowState, WorkflowStage


class SchedulingAgent:
    name = "scheduling-agent"

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or build_default_registry()

    def _invoke_tool(self, state: CaseWorkflowState, name: str, **kwargs):
        try:
            payload = self.registry.invoke_with_telemetry(
                name,
                workflow_id=state.conversation_id,
                trace_id=state.trace_id or state.conversation_id,
                agent=self.name,
                parent_event_id=state.last_event_id,
                replay_branch_id=state.replay_branch_id,
                **kwargs,
            )
            state.tool_telemetry.append(payload["telemetry"])
            return payload["result"]
        except Exception as exc:
            telemetry = getattr(exc, "tool_telemetry", None)
            if telemetry:
                state.tool_telemetry.append(telemetry)
            raise

    def process(self, state: CaseWorkflowState) -> CaseWorkflowState:
        state.set_stage(WorkflowStage.SCHEDULING)
        specialty = str(state.analysis["specialty"])
        continuity = self._invoke_tool(
            state,
            "recommend_doctor",
            specialty=specialty,
            phone=state.patient_phone,
            patient_email=state.patient_email,
        )
        doctor_matches = self._invoke_tool(
            state,
            "recommend_doctor_matches",
            specialty=specialty,
            phone=state.patient_phone,
            patient_email=state.patient_email,
        )
        state.recommended_doctor = continuity
        state.doctor_matches = doctor_matches
        state.available_dates = self._invoke_tool(
            state,
            "fetch_available_dates",
            doctor_name=str(continuity["doctor_name"]),
        )
        slot = self._invoke_tool(
            state,
            "find_next_live_slot",
            doctor_name=str(continuity["doctor_name"]),
            requested_date=dt.date.today().isoformat(),
        )
        state.analysis["doctor_name"] = str(continuity["doctor_name"])
        state.analysis["branch"] = str(continuity["branch"])
        state.analysis["continuity_reason"] = str(continuity["continuity_reason"])
        state.analysis["doctor_matches"] = doctor_matches
        state.analysis["next_slot"] = f"{slot['slot_date']} {slot['slot_time']}" if slot else "No live slot available"
        state.analysis["available_dates"] = state.available_dates
        state.analysis["booking_mode"] = (
            "urgent"
            if state.analysis["urgency"] in {"High", "Emergency"}
            else ("review" if state.analysis["requires_review"] else "self-serve")
        )
        state.analysis["policy_decision"] = state.policy_decision
        state.analysis["reasoning_trace"] = state.reasoning_trace
        state.analysis["tool_trace"] = [
            "recommend_doctor",
            "recommend_doctor_matches",
            "fetch_available_dates",
            "find_next_live_slot",
        ]
        state.analysis["tool_telemetry"] = state.tool_telemetry
        state.next_action = "prepare_response"
        state.assigned_agent = "communication-agent"
        state.record(self.name, "generated continuity-aware doctor routing and slot options")
        return state
