from __future__ import annotations

from flask import Flask


def build_openapi_spec(app: Flask) -> dict[str, object]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "DOCQ Operational API",
            "version": "1.0.0",
            "description": (
                "Enterprise healthcare orchestration APIs for deterministic intake, replay, governance, "
                "operational analytics, observability, and multi-tenant operational workflows."
            ),
        },
        "servers": [{"url": "/"}],
        "tags": [
            {"name": "Access"},
            {"name": "Workflows"},
            {"name": "Governance"},
            {"name": "Analytics"},
            {"name": "Operations"},
            {"name": "Enterprise"},
            {"name": "Observability"},
        ],
        "paths": {
            "/api/intake": {
                "post": {
                    "tags": ["Access"],
                    "summary": "Run deterministic intake orchestration",
                    "requestBody": {"required": True},
                    "responses": {"200": {"description": "Workflow analysis payload"}},
                }
            },
            "/api/public-booking": {
                "post": {
                    "tags": ["Operations"],
                    "summary": "Create public intake booking request",
                    "responses": {"201": {"description": "Appointment accepted"}},
                }
            },
            "/api/workflows/{workflow_id}/replay": {
                "get": {
                    "tags": ["Workflows"],
                    "summary": "Fetch replay-safe workflow reconstruction",
                    "parameters": [{"name": "workflow_id", "in": "path", "required": True}],
                    "responses": {"200": {"description": "Replay model"}},
                }
            },
            "/api/workflows/{workflow_id}/events": {
                "get": {
                    "tags": ["Workflows"],
                    "summary": "Fetch canonical workflow events",
                    "parameters": [{"name": "workflow_id", "in": "path", "required": True}],
                    "responses": {"200": {"description": "Workflow event collection"}},
                }
            },
            "/api/workflows/model-diff": {
                "get": {
                    "tags": ["Governance"],
                    "summary": "Compare active and candidate model outputs",
                    "responses": {"200": {"description": "Model divergence summary"}},
                }
            },
            "/api/analytics/operational": {
                "get": {
                    "tags": ["Analytics"],
                    "summary": "Fetch tenant-scoped operational analytics",
                    "responses": {"200": {"description": "Operational analytics snapshot"}},
                }
            },
            "/api/ml/governance/state": {
                "get": {
                    "tags": ["Governance"],
                    "summary": "Fetch continuous governance runtime state",
                    "responses": {"200": {"description": "Governance state snapshot"}},
                }
            },
            "/api/integrations/health": {
                "get": {
                    "tags": ["Enterprise"],
                    "summary": "Fetch integration adapter health",
                    "responses": {"200": {"description": "Provider readiness status"}},
                }
            },
            "/api/observability/topology": {
                "get": {
                    "tags": ["Observability"],
                    "summary": "Fetch distributed runtime topology",
                    "responses": {"200": {"description": "Runtime node and consumer ownership data"}},
                }
            },
            "/api/demo/bootstrap": {
                "post": {
                    "tags": ["Enterprise"],
                    "summary": "Bootstrap deterministic demo scenarios",
                    "responses": {"200": {"description": "Demo bootstrap summary"}},
                }
            },
        },
    }


def build_docs_context(app: Flask) -> dict[str, object]:
    return {
        "product_name": "DOCQ",
        "api_spec_url": "/api/openapi.json",
        "sections": [
            {
                "title": "Deterministic Runtime",
                "items": [
                    "Canonical versioned events remain replay authority.",
                    "Snapshots, projections, and analytics derive only from append-only lineage.",
                    "Distributed workers coordinate through leases, outbox delivery, and advisory locking.",
                ],
            },
            {
                "title": "Enterprise Operations",
                "items": [
                    "Multi-tenant governance and org-scoped RBAC.",
                    "Operational workflow automation for reminders, SLA, escalation, and recovery playbooks.",
                    "Replay-safe analytics, observability, and tenant-isolated enterprise reporting.",
                ],
            },
            {
                "title": "Developer Surfaces",
                "items": [
                    "Interactive API explorer backed by the live OpenAPI spec.",
                    "Deterministic demo bootstrap for showcase and onboarding.",
                    "Health, readiness, metrics, and topology visibility for operations teams.",
                ],
            },
        ],
        "endpoint_groups": [
            {"label": "Replay", "href": "/api/workflows/stream", "description": "SSE workflow console snapshot stream"},
            {"label": "Governance", "href": "/api/ml/governance/state", "description": "Continuous governance runtime state"},
            {"label": "Analytics", "href": "/api/analytics/operational", "description": "Tenant-scoped operational analytics"},
            {"label": "Observability", "href": "/metrics", "description": "Prometheus metrics export"},
        ],
    }
