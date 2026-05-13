"""persist source_finding_id on recommendations

Revision ID: c2d4e6f8a0b1
Revises: b1c2d3e4f5a6
Create Date: 2026-05-14

Keep the detector/finding identity that produced each page-review
recommendation. This gives Studio and the assistant a durable evidence
anchor instead of relying only on generated prose.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d4e6f8a0b1"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "page_review_recommendations",
        sa.Column("source_finding_id", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("page_review_recommendations", "source_finding_id")
