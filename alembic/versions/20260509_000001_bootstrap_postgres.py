"""Bootstrap PostgreSQL production tables and indexes.

Revision ID: 20260509_000001
Revises:
Create Date: 2026-05-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260509_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_events_workflow_id ON workflow_events (workflow_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_events_trace_id ON workflow_events (trace_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_events_root_event_id ON workflow_events (root_event_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_events_causation_id ON workflow_events (causation_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_events_replay_branch_id ON workflow_events (replay_branch_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_workflow_events_created_at ON workflow_events (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_risk_predictions_model_registry_id ON risk_predictions (model_registry_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_model_evaluation_results_evaluation_run_id ON model_evaluation_results (evaluation_run_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_model_evaluation_results_evaluation_run_id")
    op.execute("DROP INDEX IF EXISTS ix_risk_predictions_model_registry_id")
    op.execute("DROP INDEX IF EXISTS ix_workflow_events_created_at")
    op.execute("DROP INDEX IF EXISTS ix_workflow_events_replay_branch_id")
    op.execute("DROP INDEX IF EXISTS ix_workflow_events_causation_id")
    op.execute("DROP INDEX IF EXISTS ix_workflow_events_root_event_id")
    op.execute("DROP INDEX IF EXISTS ix_workflow_events_trace_id")
    op.execute("DROP INDEX IF EXISTS ix_workflow_events_workflow_id")
