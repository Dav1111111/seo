"""extend task and page for SEO agent

Revision ID: a7b3f2e1d5c6
Revises: bc745533ed8a
Create Date: 2026-04-17 08:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a7b3f2e1d5c6'
down_revision: Union[str, None] = 'bc745533ed8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Task: add target, generated content, timing fields
    op.add_column('tasks', sa.Column('started_at', sa.Date(), nullable=True))
    op.add_column('tasks', sa.Column('completed_at', sa.Date(), nullable=True))
    op.add_column('tasks', sa.Column('target_query', sa.String(1000), nullable=True))
    op.add_column('tasks', sa.Column('target_cluster', sa.String(255), nullable=True))
    op.add_column('tasks', sa.Column('target_page_url', sa.String(2048), nullable=True))
    op.add_column('tasks', sa.Column('generated_content', postgresql.JSONB(), nullable=True))

    # Page: add crawled content fields
    op.add_column('pages', sa.Column('meta_description', sa.String(1000), nullable=True))
    op.add_column('pages', sa.Column('h1', sa.String(500), nullable=True))
    op.add_column('pages', sa.Column('content_text', sa.Text(), nullable=True))
    op.add_column('pages', sa.Column('word_count', sa.Integer(), nullable=True))
    op.add_column('pages', sa.Column('internal_links', postgresql.JSONB(), nullable=True))
    op.add_column('pages', sa.Column('images_count', sa.Integer(), nullable=True))
    op.add_column('pages', sa.Column('has_schema', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('pages', sa.Column('last_crawled_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('pages', 'last_crawled_at')
    op.drop_column('pages', 'has_schema')
    op.drop_column('pages', 'images_count')
    op.drop_column('pages', 'internal_links')
    op.drop_column('pages', 'word_count')
    op.drop_column('pages', 'content_text')
    op.drop_column('pages', 'h1')
    op.drop_column('pages', 'meta_description')

    op.drop_column('tasks', 'generated_content')
    op.drop_column('tasks', 'target_page_url')
    op.drop_column('tasks', 'target_cluster')
    op.drop_column('tasks', 'target_query')
    op.drop_column('tasks', 'completed_at')
    op.drop_column('tasks', 'started_at')
