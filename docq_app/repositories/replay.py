from __future__ import annotations

from .base import BaseRepository


class ReplayRepository(BaseRepository):
    def fetch_workflow_events(self, workflow_id: str, limit: int = 40):
        return self.fetchall(
            """
            SELECT id, tenant_key, workflow_id, trace_id, correlation_id, causation_id, root_event_id,
                   parent_event_id, causation_depth, replay_branch_id,
                   stage, agent, action, decision, confidence, reasons, payload_json, event_fingerprint, created_at
            FROM workflow_events
            WHERE workflow_id = :workflow_id
            ORDER BY id ASC
            LIMIT :limit
            """,
            {"workflow_id": workflow_id, "limit": limit},
        )

    def fetch_recent_workflow_events(self, limit: int = 25):
        return self.fetchall(
            """
            SELECT id, tenant_key, workflow_id, trace_id, correlation_id, causation_id, root_event_id,
                   parent_event_id, causation_depth, replay_branch_id,
                   stage, agent, action, decision, confidence, reasons, payload_json, event_fingerprint, created_at
            FROM workflow_events
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    def fetch_latest_workflow_snapshots(
        self,
        *,
        evaluation_prefix: str,
        governance_prefix: str,
        security_prefix: str,
        limit: int = 100,
    ):
        return self.fetchall(
            """
            SELECT we.id, we.tenant_key, we.workflow_id, we.trace_id, we.correlation_id, we.causation_id, we.root_event_id,
                   we.parent_event_id, we.causation_depth, we.replay_branch_id,
                   we.stage, we.agent, we.action, we.decision, we.confidence, we.reasons, we.payload_json, we.created_at
            FROM workflow_events we
            INNER JOIN (
                SELECT workflow_id, MAX(id) AS max_id
                FROM workflow_events
                WHERE workflow_id NOT LIKE :evaluation_prefix
                  AND workflow_id NOT LIKE :governance_prefix
                  AND workflow_id NOT LIKE :security_prefix
                GROUP BY workflow_id
            ) latest ON latest.max_id = we.id
            ORDER BY we.created_at DESC
            LIMIT :limit
            """,
            {
                "evaluation_prefix": f"{evaluation_prefix}%",
                "governance_prefix": f"{governance_prefix}%",
                "security_prefix": f"{security_prefix}%",
                "limit": limit,
            },
        )

    def fetch_workflow_lifecycle_stats(
        self,
        *,
        evaluation_prefix: str,
        governance_prefix: str,
        security_prefix: str,
        limit: int = 200,
    ):
        return self.fetchall(
            """
            SELECT workflow_id, MIN(created_at) AS started_at, MAX(created_at) AS latest_at, COUNT(*) AS event_count
            FROM workflow_events
            WHERE workflow_id NOT LIKE :evaluation_prefix
              AND workflow_id NOT LIKE :governance_prefix
              AND workflow_id NOT LIKE :security_prefix
            GROUP BY workflow_id
            ORDER BY latest_at DESC
            LIMIT :limit
            """,
            {
                "evaluation_prefix": f"{evaluation_prefix}%",
                "governance_prefix": f"{governance_prefix}%",
                "security_prefix": f"{security_prefix}%",
                "limit": limit,
            },
        )

    def fetch_workflow_lineage_summary(
        self,
        *,
        evaluation_prefix: str,
        governance_prefix: str,
        security_prefix: str,
        limit: int = 40,
    ):
        return self.fetchall(
            """
            SELECT we.workflow_id,
                   MIN(we.id) AS root_event_id,
                   MAX(we.id) AS latest_event_id,
                   COUNT(*) AS event_count,
                   COALESCE(MAX(we.correlation_id), we.workflow_id) AS correlation_id,
                   (
                       SELECT COUNT(*)
                       FROM tool_execution_logs tel
                       WHERE tel.workflow_id = we.workflow_id
                   ) AS tool_invocation_count,
                   (
                       SELECT tel.tool_name
                       FROM tool_execution_logs tel
                       WHERE tel.workflow_id = we.workflow_id
                       ORDER BY tel.created_at DESC
                       LIMIT 1
                   ) AS last_tool_name
            FROM workflow_events we
            WHERE we.workflow_id NOT LIKE :evaluation_prefix
              AND we.workflow_id NOT LIKE :governance_prefix
              AND we.workflow_id NOT LIKE :security_prefix
            GROUP BY we.workflow_id
            ORDER BY latest_event_id DESC
            LIMIT :limit
            """,
            {
                "evaluation_prefix": f"{evaluation_prefix}%",
                "governance_prefix": f"{governance_prefix}%",
                "security_prefix": f"{security_prefix}%",
                "limit": limit,
            },
        )
