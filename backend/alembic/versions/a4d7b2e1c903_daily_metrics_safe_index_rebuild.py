"""daily_metrics — guarantee partial unique index exists, concurrently.

Defensive follow-up to f9a1b2c3d4e5. The original migration created the
partial unique index inside Alembic's auto-transaction, which holds an
ACCESS EXCLUSIVE lock on daily_metrics until the build finishes. On a
busy table (Webmaster + Metrica beat collectors hit it constantly) that
risks production stalls. This revision ensures the index exists using
CREATE UNIQUE INDEX CONCURRENTLY — the safe pattern per CLAUDE.md hard
rule 6.

On prod (where f9a1b2c3d4e5 already produced the index): no-op via
IF NOT EXISTS. On fresh installs: this is the first concurrent build.

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


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY must run outside any transaction.
    # `autocommit_block` swaps the connection out of Alembic's
    # auto-transaction for the duration of the block.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            "uq_daily_metrics_site_date_type_null_dim "
            "ON daily_metrics (site_id, date, metric_type) "
            "WHERE dimension_id IS NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "uq_daily_metrics_site_date_type_null_dim"
        )
