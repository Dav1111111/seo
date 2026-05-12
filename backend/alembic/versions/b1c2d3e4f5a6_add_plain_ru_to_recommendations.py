"""add plain_ru to page_review_recommendations

Revision ID: b1c2d3e4f5a6
Revises: a9f0c3b1d2e4
Create Date: 2026-05-13

Owner-facing plain-language explanation for a recommendation, sitting
next to before_text/after_text/reasoning_ru. The technical fields stay
for developers; plain_ru gives the site owner 2-3 sentences without
jargon (Schema, JSON-LD, canonical, hreflang are paraphrased away).

Nullable: existing rows are backfilled out-of-band by a separate
script, so this migration only adds the column and exits.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a9f0c3b1d2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "page_review_recommendations",
        sa.Column("plain_ru", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("page_review_recommendations", "plain_ru")
