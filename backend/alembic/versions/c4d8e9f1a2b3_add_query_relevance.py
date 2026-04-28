"""Query relevance classification (Studio v2 etap 4).

Adds four columns to `search_queries`:

  relevance            — own / adjacent / disputed / spam / unclassified
  relevance_set_by     — rules / llm / user (or NULL when never classified)
  relevance_set_at     — timestamptz of last write
  relevance_reason_ru  — short human-readable explanation

Default `relevance='unclassified'` so existing rows behave as «not yet
classified» rather than spuriously «own». Index on (site_id, relevance)
backs the «show only spam / show only ours» filters in /studio/queries.

Why VARCHAR + CHECK instead of a Postgres ENUM type: enum values
shift in v2 (we may add `branded` or merge `disputed` into `adjacent`
based on real data). VARCHAR + CHECK lets us update the allowed set
with a quick `op.execute(ALTER ... DROP CONSTRAINT; ADD CONSTRAINT ...)`
without the dance Postgres requires for ENUM type evolution.

Revision ID: c4d8e9f1a2b3
Revises: b3c7e4d1f520
Create Date: 2026-04-27 18:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c4d8e9f1a2b3"
down_revision: Union[str, None] = "b3c7e4d1f520"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "search_queries",
        sa.Column(
            "relevance",
            sa.String(length=20),
            nullable=False,
            server_default="unclassified",
        ),
    )
    op.add_column(
        "search_queries",
        sa.Column("relevance_set_by", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "search_queries",
        sa.Column(
            "relevance_set_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "search_queries",
        sa.Column("relevance_reason_ru", sa.Text(), nullable=True),
    )

    # Bound the allowed values without a full enum type — see header.
    op.create_check_constraint(
        "ck_search_queries_relevance",
        "search_queries",
        "relevance IN ('own','adjacent','disputed','spam','unclassified')",
    )
    op.create_check_constraint(
        "ck_search_queries_relevance_set_by",
        "search_queries",
        "relevance_set_by IS NULL OR relevance_set_by IN ('rules','llm','user')",
    )

    # Filter index — frontend defaults to «exclude spam», so the most
    # common query is `WHERE site_id=? AND relevance != 'spam'`. A
    # composite (site_id, relevance) index serves that AND the
    # «show only own» filter both.
    op.create_index(
        "ix_search_queries_site_relevance",
        "search_queries",
        ["site_id", "relevance"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_search_queries_site_relevance",
        table_name="search_queries",
    )
    op.drop_constraint(
        "ck_search_queries_relevance_set_by",
        "search_queries",
        type_="check",
    )
    op.drop_constraint(
        "ck_search_queries_relevance",
        "search_queries",
        type_="check",
    )
    op.drop_column("search_queries", "relevance_reason_ru")
    op.drop_column("search_queries", "relevance_set_at")
    op.drop_column("search_queries", "relevance_set_by")
    op.drop_column("search_queries", "relevance")
