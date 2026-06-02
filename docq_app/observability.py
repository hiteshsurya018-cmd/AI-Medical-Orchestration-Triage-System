from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from typing import Any

from flask import Response, g, request


class JsonLogFormatter:
    @staticmethod
    def format(message: str, **fields: Any) -> str:
        payload = {"message": message, **fields}
        return json.dumps(payload, sort_keys=True)


class MetricsRegistry:
    def __init__(self) -> None:
        self.counters: dict[str, float] = defaultdict(float)
        self.gauges: dict[str, float] = defaultdict(float)

    def increment(self, key: str, value: float = 1.0) -> None:
        self.counters[key] += value

    def set_gauge(self, key: str, value: float) -> None:
        self.gauges[key] = value

    def render_prometheus(self) -> str:
        lines: list[str] = []
        for key, value in sorted(self.counters.items()):
            lines.append(f"{key} {value}")
        for key, value in sorted(self.gauges.items()):
            lines.append(f"{key} {value}")
        return "\n".join(lines) + ("\n" if lines else "")


metrics_registry = MetricsRegistry()


def begin_request_trace() -> None:
    g.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
    g.request_started_at = time.perf_counter()
    metrics_registry.increment("docq_http_requests_total")


def finalize_request_trace(response: Response) -> Response:
    elapsed_ms = round((time.perf_counter() - float(getattr(g, "request_started_at", time.perf_counter()))) * 1000.0, 2)
    response.headers["X-Request-Id"] = getattr(g, "request_id", "")
    metrics_registry.increment(f"docq_http_status_{response.status_code}_total")
    metrics_registry.set_gauge("docq_last_request_latency_ms", elapsed_ms)
    return response
