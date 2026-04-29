"""Harmful query diagnosis cache (Studio v2 etap 5+).

Stores LLM-generated diagnosis of WHY we rank for a spam/disputed
query and HOW to fix the page that ranks. Cached so re-fetches don't
re-pay LLM. Single-column JSONB for forward-compat — schema below
will evolve as the diagnoser learns new failure modes.

Expected JSONB shape:
    {
      "matched_url":       "https://...",        # our URL ranking for the query
      "matched_position":  11,                    # the position we found it at
      "cause_ru":          "...",                 # one paragraph: why we rank here
      "fixes": {
        "title_change":         "...",            # specific edit (or null)
        "h1_change":            "...",
        "meta_description_change": "...",
        "content_change_ru":    "...",            # narrative description
        "schema_recommendation": "...",
        "noindex_recommended":  false             # last-resort flag
      },
      "model":             "claude-haiku-...",
      "diagnosed_at":      "2026-04-28T..."
    }

Revision ID: d5e6f7a8b9c0
Revises: c4d8e9f1a2b3
Create Date: 2026-04-28 23:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d8e9f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "search_queries",
        sa.Column(
            "harmful_diagnosis",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "search_queries",
        sa.Column(
            "harmful_diagnosed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("search_queries", "harmful_diagnosed_at")
    op.drop_column("search_queries", "harmful_diagnosis")
