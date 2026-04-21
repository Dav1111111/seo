"""Этап 1 — conversational onboarding schema.

Revision ID: e1a2b3c4d5f6
Revises: d9e0f1a2b3c4
Create Date: 2026-04-21 14:00:00.000000

Adds state for the 7-step conversational onboarding wizard.

sites:
  - onboarding_step        — state machine position
  - understanding          — JSONB narrative + niche + positioning + usp
  - competitor_domains     — JSONB array[str]
  - kpi_targets            — JSONB {baseline, target_3m, target_6m, target_12m}

target_clusters:
  - user_confirmed              — null=not reviewed, true=confirmed, false=rejected
  - growth_intent               — 'grow' | 'ignore' | 'not_mine'
  - query_intent                — 'info' | 'comm' | 'trans' | 'nav'
  - seasonality_peak_months     — JSONB array[int 1-12]
  - page_intent_fit             — 'green' | 'yellow' | 'red'
  - page_intent_fit_reason_ru   — free text

target_config (JSONB, no schema change) — primary_product, product_weights
are stored inside the existing JSONB blob and read by the scorer.

Parity-safe: all new columns nullable with sensible defaults. Downstream
consumers that don't know about these columns remain unaffected.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1a2b3c4d5f6"
down_revision: Union[str, None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ONBOARDING_STEPS = (
    "pending_analyze",     # just created, no data yet
    "confirm_business",    # step 1 — understanding shown, awaits confirm
    "confirm_products",    # step 2 — main/secondary product split
    "confirm_competitors", # step 3 — SERP-derived competitor list
    "confirm_queries",     # step 4 — demand map selection
    "confirm_positions",   # step 5 — current fit green/yellow/red
    "confirm_plan",        # step 6 — initial recommendations
    "confirm_kpi",         # step 7 — targets for 3/6/12m
    "active",              # onboarding done, normal ops
)

GROWTH_INTENTS = ("grow", "ignore", "not_mine")
QUERY_INTENTS = ("info", "comm", "trans", "nav")
PAGE_FITS = ("green", "yellow", "red")


def upgrade() -> None:
    # sites ------------------------------------------------------------
    op.add_column(
        "sites",
        sa.Column(
            "onboarding_step",
            sa.String(32),
            nullable=False,
            server_default="pending_analyze",
        ),
    )
    op.add_column(
        "sites",
        sa.Column(
            "understanding",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "sites",
        sa.Column(
            "competitor_domains",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "sites",
        sa.Column(
            "kpi_targets",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_sites_onboarding_step",
        "sites",
        "onboarding_step IN "
        + str(ONBOARDING_STEPS).replace("[", "(").replace("]", ")"),
    )

    # target_clusters --------------------------------------------------
    op.add_column(
        "target_clusters",
        sa.Column("user_confirmed", sa.Boolean, nullable=True),
    )
    op.add_column(
        "target_clusters",
        sa.Column("growth_intent", sa.String(16), nullable=True),
    )
    op.add_column(
        "target_clusters",
        sa.Column("query_intent", sa.String(8), nullable=True),
    )
    op.add_column(
        "target_clusters",
        sa.Column(
            "seasonality_peak_months",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "target_clusters",
        sa.Column("page_intent_fit", sa.String(8), nullable=True),
    )
    op.add_column(
        "target_clusters",
        sa.Column("page_intent_fit_reason_ru", sa.Text, nullable=True),
    )
    op.create_check_constraint(
        "ck_target_clusters_growth_intent",
        "target_clusters",
        "growth_intent IS NULL OR growth_intent IN "
        + str(GROWTH_INTENTS).replace("[", "(").replace("]", ")"),
    )
    op.create_check_constraint(
        "ck_target_clusters_query_intent",
        "target_clusters",
        "query_intent IS NULL OR query_intent IN "
        + str(QUERY_INTENTS).replace("[", "(").replace("]", ")"),
    )
    op.create_check_constraint(
        "ck_target_clusters_page_intent_fit",
        "target_clusters",
        "page_intent_fit IS NULL OR page_intent_fit IN "
        + str(PAGE_FITS).replace("[", "(").replace("]", ")"),
    )

    # Partial index for the "confirmed & growing" working set — what
    # the scorer & review pipeline iterate over in the hot path.
    op.create_index(
        "ix_target_clusters_site_growing",
        "target_clusters",
        ["site_id"],
        postgresql_where=sa.text("growth_intent = 'grow'"),
    )


def downgrade() -> None:
    op.drop_index("ix_target_clusters_site_growing", table_name="target_clusters")
    op.drop_constraint("ck_target_clusters_page_intent_fit", "target_clusters")
    op.drop_constraint("ck_target_clusters_query_intent", "target_clusters")
    op.drop_constraint("ck_target_clusters_growth_intent", "target_clusters")
    op.drop_column("target_clusters", "page_intent_fit_reason_ru")
    op.drop_column("target_clusters", "page_intent_fit")
    op.drop_column("target_clusters", "seasonality_peak_months")
    op.drop_column("target_clusters", "query_intent")
    op.drop_column("target_clusters", "growth_intent")
    op.drop_column("target_clusters", "user_confirmed")

    op.drop_constraint("ck_sites_onboarding_step", "sites")
    op.drop_column("sites", "kpi_targets")
    op.drop_column("sites", "competitor_domains")
    op.drop_column("sites", "understanding")
    op.drop_column("sites", "onboarding_step")
