"""add site vertical + business_model columns

Revision ID: f3b2a0e4c819
Revises: d9f2b4c8e501
Create Date: 2026-04-17 19:00:00.000000

Adds two columns to `sites` for the core/profile refactor:
  - vertical (default 'tourism') — which industry profile to load
  - business_model (default 'tour_operator') — overlay within the vertical

Default values match current production (two tourism sites), so backfill
is implicit and the Decisioner output stays byte-identical.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f3b2a0e4c819'
down_revision: Union[str, None] = 'd9f2b4c8e501'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sites',
        sa.Column('vertical', sa.String(32), nullable=False, server_default='tourism'),
    )
    op.add_column(
        'sites',
        sa.Column('business_model', sa.String(48), nullable=False, server_default='tour_operator'),
    )
    op.create_index('ix_sites_vertical', 'sites', ['vertical'])


def downgrade() -> None:
    op.drop_index('ix_sites_vertical', table_name='sites')
    op.drop_column('sites', 'business_model')
    op.drop_column('sites', 'vertical')
