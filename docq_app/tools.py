from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any, Callable

from .contracts import ToolExecutionTelemetry
from .pydantic_compat import model_dump
from .appointments import allocate_live_slot, fetch_available_dates, recommend_doctor_for_patient, recommend_doctor_matches


class ToolInvocationError(RuntimeError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, func: Callable[..., Any]) -> None:
        self._tools[name] = func

    def invoke(self, name: str, **kwargs):
        return self.invoke_with_telemetry(name, **kwargs)["result"]

    def invoke_with_telemetry(self, name: str, *, workflow_id: str = "", trace_id: str = "", agent: str = "tool-agent", **kwargs):
        tool = self._tools.get(name)
        if tool is None:
            raise ToolInvocationError(f"Tool '{name}' is not registered.")
        started = time.perf_counter()
        invocation_id = f"tool-{uuid.uuid4().hex[:12]}"
        parent_event_id = kwargs.pop("parent_event_id", None)
        replay_branch_id = str(kwargs.pop("replay_branch_id", "main"))
        try:
            result = tool(**kwargs)
            telemetry = ToolExecutionTelemetry(
                invocation_id=invocation_id,
                workflow_id=workflow_id,
                trace_id=trace_id or workflow_id,
                tool_name=name,
                agent=agent,
                parent_event_id=parent_event_id,
                replay_branch_id=replay_branch_id,
                latency_ms=max(int((time.perf_counter() - started) * 1000), 0),
                success=True,
                created_at=dt.datetime.now().isoformat(timespec="seconds"),
            )
            return {"result": result, "telemetry": model_dump(telemetry)}
        except Exception as exc:
            telemetry = ToolExecutionTelemetry(
                invocation_id=invocation_id,
                workflow_id=workflow_id,
                trace_id=trace_id or workflow_id,
                tool_name=name,
                agent=agent,
                parent_event_id=parent_event_id,
                replay_branch_id=replay_branch_id,
                latency_ms=max(int((time.perf_counter() - started) * 1000), 0),
                success=False,
                error=str(exc),
                created_at=dt.datetime.now().isoformat(timespec="seconds"),
            )
            exc.tool_telemetry = model_dump(telemetry)
            raise


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("recommend_doctor", recommend_doctor_for_patient)
    registry.register("recommend_doctor_matches", recommend_doctor_matches)
    registry.register("fetch_available_dates", fetch_available_dates)
    registry.register(
        "find_next_live_slot",
        lambda doctor_name, requested_date=None: allocate_live_slot(
            doctor_name,
            requested_date or dt.date.today().isoformat(),
        ),
    )
    return registry
