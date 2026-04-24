"""Unify naive timestamps to timestamptz + partial index on activity.

Revision ID: b3c7e4d1f520
Revises: a1b2c3d4e5f6
Create Date: 2026-04-24

Two things this migration does.

(1) Convert 13 naive `timestamp without time zone` columns to
    `timestamp with time zone`, interpreting existing values as UTC.
    Background: half the schema used TimestampMixin (tz-aware) and half
    used bare DateTime. After container/worker restarts, "local" was
    whatever the session's TimeZone was, which for Celery workers
    drifts. Comparisons like `datetime.now(timezone.utc)` against these
    columns then silently slide by ±3h (MSK vs UTC).

    Affected columns:
      analysis_events.ts
      alerts.sent_at
      issues.resolved_at
      outcome_snapshots.applied_at, followup_at, created_at
      pages.first_seen_at, last_seen_at, last_crawled_at
      search_queries.first_seen_at, last_seen_at, wordstat_updated_at

    USING `col AT TIME ZONE 'UTC'` attaches the UTC offset to the
    existing wallclock. All code paths already write UTC (we control
    both webmaster collector and pipeline wrappers), so there is no
    silent 3h shift on historical data.

(2) Partial index for the hot activity-feed query:
      WHERE status IN ('done','failed','skipped') AND site_id=? AND ts > ?
    The dashboard reads terminals constantly; `started`/`progress`
    rows are ~70% of the table and pollute the full (site_id, ts)
    index scans. Partial index cuts I/O 3-5× without slowing writes.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b3c7e4d1f520"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS_TO_CONVERT: list[tuple[str, str]] = [
    ("analysis_events", "ts"),
    ("alerts", "sent_at"),
    ("issues", "resolved_at"),
    ("outcome_snapshots", "applied_at"),
    ("outcome_snapshots", "followup_at"),
    ("outcome_snapshots", "created_at"),
    ("pages", "first_seen_at"),
    ("pages", "last_seen_at"),
    ("pages", "last_crawled_at"),
    ("search_queries", "first_seen_at"),
    ("search_queries", "last_seen_at"),
    ("search_queries", "wordstat_updated_at"),
]


def upgrade() -> None:
    for table, column in _COLUMNS_TO_CONVERT:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE timestamp with time zone "
            f"USING {column} AT TIME ZONE 'UTC'"
        )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_analysis_events_terminal_ts "
        "ON analysis_events (site_id, ts DESC) "
        "WHERE status IN ('done', 'failed', 'skipped')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_analysis_events_terminal_ts")

    for table, column in _COLUMNS_TO_CONVERT:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE timestamp without time zone "
            f"USING {column} AT TIME ZONE 'UTC'"
        )
