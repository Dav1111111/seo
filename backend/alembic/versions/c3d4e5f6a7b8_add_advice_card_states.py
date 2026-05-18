"""Add workflow state for Studio advice cards.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "advice_card_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "site_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sites.id"),
            nullable=False,
        ),
        sa.Column("card_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("source_module", sa.String(80), nullable=True),
        sa.Column("page_url", sa.String(2048), nullable=True),
        sa.Column("note_ru", sa.String(1000), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_advice_card_states_site_id", "advice_card_states", ["site_id"])
    op.create_index(
        "ix_advice_card_states_site_status",
        "advice_card_states",
        ["site_id", "status"],
    )
    op.create_unique_constraint(
        "uq_advice_card_state_site_card",
        "advice_card_states",
        ["site_id", "card_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_advice_card_state_site_card",
        "advice_card_states",
        type_="unique",
    )
    op.drop_index("ix_advice_card_states_site_status", table_name="advice_card_states")
    op.drop_index("ix_advice_card_states_site_id", table_name="advice_card_states")
    op.drop_table("advice_card_states")
