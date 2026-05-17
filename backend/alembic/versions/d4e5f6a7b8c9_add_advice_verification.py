"""Per-card verification fields for advice cards.

Adds three columns to ``advice_card_states`` so the system can store the
result of an automatic technical re-check that fires right after the
owner presses «Применил»:

  * ``verified_at`` — when the auto-check ran
  * ``verification_status`` — one of
      ``pending`` / ``verified`` / ``not_yet_visible`` /
      ``user_attested`` / ``failed``
  * ``verification_evidence`` — JSONB blob with the diff the verifier
    used to decide the status (before/after counts, URLs, message)

A partial index on ``verification_status='not_yet_visible'`` lets the
daily beat task cheaply re-scan only the rows that need another try.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "advice_card_states",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "advice_card_states",
        sa.Column("verification_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "advice_card_states",
        sa.Column(
            "verification_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    # Partial index for the beat task that re-verifies "not_yet_visible"
    # rows. We never read by status outside this case, so a full index
    # would just bloat the table.
    op.create_index(
        "ix_advice_cs_unverified",
        "advice_card_states",
        ["verification_status", "verified_at"],
        postgresql_where=sa.text("verification_status = 'not_yet_visible'"),
    )


def downgrade() -> None:
    op.drop_index("ix_advice_cs_unverified", table_name="advice_card_states")
    op.drop_column("advice_card_states", "verification_evidence")
    op.drop_column("advice_card_states", "verification_status")
    op.drop_column("advice_card_states", "verified_at")
