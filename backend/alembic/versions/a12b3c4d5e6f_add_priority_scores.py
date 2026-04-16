"""add priority scores to page_review_recommendations

Revision ID: a12b3c4d5e6f
Revises: f5c9a2d1e837
Create Date: 2026-04-17 21:30:00.000000

Module 4 — Prioritization columns stored on each recommendation row.
Computed once per review run, cheap on read. No new table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a12b3c4d5e6f'
down_revision: Union[str, None] = 'f5c9a2d1e837'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('page_review_recommendations',
                  sa.Column('priority_score', sa.Numeric(5, 2), nullable=True))
    op.add_column('page_review_recommendations',
                  sa.Column('impact_score', sa.Numeric(4, 3), nullable=True))
    op.add_column('page_review_recommendations',
                  sa.Column('confidence_score', sa.Numeric(4, 3), nullable=True))
    op.add_column('page_review_recommendations',
                  sa.Column('ease_score', sa.Numeric(4, 3), nullable=True))
    op.add_column('page_review_recommendations',
                  sa.Column('scored_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('page_review_recommendations',
                  sa.Column('scorer_version', sa.String(20), nullable=True))

    # Composite index serving ranking queries (site_id + user_status filter +
    # ORDER BY priority_score DESC).
    op.create_index(
        'ix_rec_site_status_score',
        'page_review_recommendations',
        ['site_id', 'user_status', sa.text('priority_score DESC NULLS LAST')],
    )


def downgrade() -> None:
    op.drop_index('ix_rec_site_status_score', table_name='page_review_recommendations')
    op.drop_column('page_review_recommendations', 'scorer_version')
    op.drop_column('page_review_recommendations', 'scored_at')
    op.drop_column('page_review_recommendations', 'ease_score')
    op.drop_column('page_review_recommendations', 'confidence_score')
    op.drop_column('page_review_recommendations', 'impact_score')
    op.drop_column('page_review_recommendations', 'priority_score')
