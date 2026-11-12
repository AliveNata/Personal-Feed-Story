"""media item rotation

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media_items",
        sa.Column("rotation", sa.SmallInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("media_items", "rotation")
