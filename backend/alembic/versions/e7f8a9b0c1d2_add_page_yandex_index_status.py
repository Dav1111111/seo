"""Per-URL Yandex index status (Studio v2 etap 1+2 deepening).

Adds three columns to `pages`:

  in_yandex_index           BOOLEAN | NULL — TRUE if Yandex Webmaster
                            says the URL is indexed; FALSE if it's
                            in the excluded list; NULL if we never
                            checked or status couldn't be resolved.

  yandex_excluded_reason    VARCHAR(40) | NULL — verbatim from
                            Webmaster («NOT_FOUND», «BAD_HTTP_STATUS»,
                            «META_NO_INDEX», «ROBOTS_TXT_HOST», etc.)
                            so the UI can show a precise reason
                            without guessing.

  yandex_index_checked_at   TIMESTAMPTZ | NULL — when we last asked
                            Webmaster about this URL. Lets the UI
                            warn «status is N days old».

Why three columns and not one JSONB: every row gets touched by the
sync task; querying «show all excluded with reason X» becomes a
trivial WHERE filter, no JSONB-index dance. The reason column is
short-string so the column itself is cheap.

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
Create Date: 2026-04-29 13:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pages",
        sa.Column("in_yandex_index", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "pages",
        sa.Column(
            "yandex_excluded_reason",
            sa.String(length=40),
            nullable=True,
        ),
    )
    op.add_column(
        "pages",
        sa.Column(
            "yandex_index_checked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("pages", "yandex_index_checked_at")
    op.drop_column("pages", "yandex_excluded_reason")
    op.drop_column("pages", "in_yandex_index")
