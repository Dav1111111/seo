"""Per-query SERP snapshots — point 2 of owner roadmap.

Adds the ``query_serp_snapshots`` table that stores top-N SERP results
for the most valuable site queries (the SERP-intel selector picks
direct_product / funnel_warm / funnel_top etc. — see
``app.core_audit.serp_intel.selector.pick_queries_to_probe``).
The weekly Celery beat ``serp_intel_probe_all`` fans out to per-site
``serp_intel_probe_for_site`` tasks that populate this table; downstream
the brain rule ``_rule_serp_competitor_pressure`` aggregates across rows
to surface dominant competitor domains as advice cards, and the
advisor exposes per-query «we are not in top-5 but X is» gaps.

Shape:

  * ``results`` (JSONB)               — full SERP rows: position,
                                        domain, url, title, headline
  * ``our_position``                  — 1..N if our site is in top-N,
                                        else NULL
  * ``our_url``                       — the exact URL of our page that
                                        ranked (helps the «which page
                                        wins» column)
  * ``top_competitor_domains`` JSONB  — first 3 non-our domains in
                                        rank order
  * ``error_tag``                     — set when fetch failed;
                                        ``results=[]`` in that case so
                                        callers can distinguish «empty
                                        SERP» from «we never asked»

History matters here: we never UPSERT-replace older rows. A new probe
just inserts a fresh row, and ``ix_qss_site_query_taken`` keeps the
``ORDER BY taken_at DESC LIMIT 1`` lookup cheap.

Empty-table migration safety (CLAUDE.md rule 6): both indexes use
plain ``op.create_index`` — this is a NEW table, so there's nothing
to lock.

Revision ID: e1f2a3b4c5d6
Revises: d4e5f6a7b8c9
Create Date: 2026-05-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "query_serp_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "query_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("search_queries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "taken_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "region",
            sa.String(32),
            nullable=False,
            server_default="225",
        ),
        sa.Column(
            "results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("our_position", sa.Integer, nullable=True),
        sa.Column("our_url", sa.String(2048), nullable=True),
        sa.Column(
            "top_competitor_domains",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("error_tag", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_qss_site_query_taken",
        "query_serp_snapshots",
        ["site_id", "query_id", "taken_at"],
    )
    op.create_index(
        "ix_qss_site_taken",
        "query_serp_snapshots",
        ["site_id", "taken_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_qss_site_taken", table_name="query_serp_snapshots")
    op.drop_index("ix_qss_site_query_taken", table_name="query_serp_snapshots")
    op.drop_table("query_serp_snapshots")
