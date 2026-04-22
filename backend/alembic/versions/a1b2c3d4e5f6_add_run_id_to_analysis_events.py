"""Add run_id to analysis_events.

Revision ID: a1b2c3d4e5f6
Revises: f7e8a9b0c1d2
Create Date: 2026-04-22 18:30:00.000000

run_id lets us group events from a single pipeline run together so the
UI can show "this run only" instead of a time-ordered soup of several
concurrent runs. Nullable on purpose — historical events stay at NULL
and standalone button-triggered runs (which aren't wrapped in a
pipeline) can stay at NULL too.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f7e8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_events",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_analysis_events_site_run",
        "analysis_events",
        ["site_id", "run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_events_site_run", table_name="analysis_events")
    op.drop_column("analysis_events", "run_id")
