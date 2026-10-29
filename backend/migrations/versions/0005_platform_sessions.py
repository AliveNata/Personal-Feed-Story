"""platform sessions

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_sessions",
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("account_name", sa.String(length=100), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("platform"),
    )


def downgrade() -> None:
    op.drop_table("platform_sessions")
