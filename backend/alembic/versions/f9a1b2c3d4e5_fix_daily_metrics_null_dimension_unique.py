"""dedupe null-dimension daily metrics.

Revision ID: f9a1b2c3d4e5
Revises: c2d4e6f8a0b1
Create Date: 2026-05-15

PostgreSQL treats NULL values as distinct inside a regular UNIQUE
constraint. Our site-wide metric rows use dimension_id=NULL, so repeated
collector runs created duplicates instead of updating the same day/type row.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f9a1b2c3d4e5"
down_revision: Union[str, None] = "c2d4e6f8a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Bound any DDL waits so we never park behind a long-running write.
    op.execute("SET lock_timeout = '5s'")
    # Keep the newest copy for each site/date/type and remove historical
    # duplicates before creating the partial unique index.
    op.execute("""
        DELETE FROM daily_metrics dm
        USING (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY site_id, date, metric_type
                    ORDER BY updated_at DESC NULLS LAST, id DESC
                ) AS rn
            FROM daily_metrics
            WHERE dimension_id IS NULL
        ) ranked
        WHERE dm.id = ranked.id
          AND ranked.rn > 1
    """)
    # Idempotent create — prod already has this index from the original
    # (unsafe) apply; fresh installs and replays now no-op cleanly.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_daily_metrics_site_date_type_null_dim "
        "ON daily_metrics (site_id, date, metric_type) "
        "WHERE dimension_id IS NULL"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS uq_daily_metrics_site_date_type_null_dim"
    )
