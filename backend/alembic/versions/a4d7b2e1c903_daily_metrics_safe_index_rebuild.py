"""daily_metrics — rebuild null-dimension unique index with CONCURRENTLY.

Defensive follow-up to f9a1b2c3d4e5. The original migration created the
partial unique index inside a transaction, which would take ACCESS EXCLUSIVE
lock on daily_metrics. On a busy table (Webmaster + Metrica beat collectors
hit it constantly) that risks production stalls. This revision drops the
non-concurrent index (if present) and recreates it via CREATE UNIQUE INDEX
CONCURRENTLY — the safe pattern per CLAUDE.md hard rule 6.

Revision ID: a4d7b2e1c903
Revises: f9a1b2c3d4e5
Create Date: 2026-05-15
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a4d7b2e1c903"
down_revision: Union[str, None] = "f9a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Alembic runs each migration in a single transaction by default.
# CONCURRENTLY is forbidden inside transactions — we exit it explicitly
# with COMMIT before issuing the DROP/CREATE.


def upgrade() -> None:
    # Commit the auto-begun transaction; CONCURRENTLY only works outside.
    op.execute("COMMIT")
    # Drop the non-concurrent variant if it exists, recreate concurrently.
    # IF EXISTS handles both fresh installs (where nothing was created yet)
    # and prod (where f9a1b2c3d4e5 already created it without CONCURRENTLY).
    op.execute(
        "DROP INDEX IF EXISTS uq_daily_metrics_site_date_type_null_dim"
    )
    op.execute(
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
        "uq_daily_metrics_site_date_type_null_dim "
        "ON daily_metrics (site_id, date, metric_type) "
        "WHERE dimension_id IS NULL"
    )


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS "
        "uq_daily_metrics_site_date_type_null_dim"
    )
