"""add sites.target_config_draft column

Revision ID: d9e0f1a2b3c4
Revises: c4d5e6f7a8b9
Create Date: 2026-04-20 10:00:00.000000

Phase F of the Target Demand Map pivot — Draft Profile Builder.

Adds a separate JSONB column `sites.target_config_draft` that acts as a
non-destructive hypothesis store for auto-generated profile proposals.
The existing `sites.target_config` column remains the source of truth
used by the Phase A expander; downstream consumers are not modified in
Phase F. The admin "commit-draft" endpoint copies the draft into
`target_config` only on explicit user approval.

Parity-safe: no consumer reads this column yet.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sites',
        sa.Column(
            'target_config_draft',
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column('sites', 'target_config_draft')
