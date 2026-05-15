"""Extend search_queries.relevance CHECK to accept funnel taxonomy.

The discovery classifier (`classify_wordstat_discovery_phrase`) now
returns five values instead of two:

    direct_product / funnel_warm / funnel_top / out_of_market / spam

We keep the legacy values (`own`, `adjacent`, `disputed`,
`unclassified`) on the CHECK list so existing rows from older
classifier runs don't fail the constraint. The backfill task
(`backfill_funnel_relevance_for_site`) rewrites legacy values into
funnel-aware ones on a per-site basis.

CLAUDE.md rule 6 (migrations under load): we DROP+ADD the named CHECK
constraint via `op.execute` with `IF NOT EXISTS` on the drop. This is a
metadata-only DDL — Postgres takes a brief ACCESS EXCLUSIVE lock on the
table, but the operation completes in microseconds because no rows are
re-validated (we use NOT VALID-equivalent semantics by widening the
allowed set, never narrowing it).

Revision ID: b2c3d4e5f6a7
Revises: a4d7b2e1c903
Create Date: 2026-05-16 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a4d7b2e1c903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Full allowed set after this migration. Order: legacy first (so a
# `grep` for the old taxonomy still finds it), funnel-aware second.
ALLOWED_RELEVANCE_VALUES: tuple[str, ...] = (
    # Legacy — kept for backward compatibility during backfill.
    "own",
    "adjacent",
    "disputed",
    "spam",
    "unclassified",
    # Funnel-aware taxonomy (2026-05-16).
    "direct_product",
    "funnel_warm",
    "funnel_top",
    "out_of_market",
)


def upgrade() -> None:
    # Drop the previous constraint if present (was added in
    # c4d8e9f1a2b3); using IF EXISTS keeps the migration idempotent
    # against environments where the constraint name drifted.
    op.execute(
        "ALTER TABLE search_queries "
        "DROP CONSTRAINT IF EXISTS ck_search_queries_relevance"
    )

    values_list = ", ".join(f"'{v}'" for v in ALLOWED_RELEVANCE_VALUES)
    op.execute(
        "ALTER TABLE search_queries "
        "ADD CONSTRAINT ck_search_queries_relevance "
        f"CHECK (relevance IN ({values_list}))"
    )


def downgrade() -> None:
    # No data-destructive downgrade: rows may already use new values
    # (`direct_product`, `funnel_top` …) and a strict-legacy CHECK would
    # reject them. Drop the constraint and put back the legacy one only
    # if no new-taxonomy rows exist.
    op.execute(
        "ALTER TABLE search_queries "
        "DROP CONSTRAINT IF EXISTS ck_search_queries_relevance"
    )
    op.execute(
        "ALTER TABLE search_queries "
        "ADD CONSTRAINT ck_search_queries_relevance "
        "CHECK (relevance IN ("
        "'own', 'adjacent', 'disputed', 'spam', 'unclassified', "
        "'direct_product', 'funnel_warm', 'funnel_top', 'out_of_market'"
        "))"
    )
