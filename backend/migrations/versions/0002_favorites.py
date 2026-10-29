"""favorites

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "favorites",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("post_count", sa.Integer(), nullable=True),
        sa.Column("seen_post_count", sa.Integer(), nullable=True),
        sa.Column("latest_story_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seen_story_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_favorites_platform_username",
        "favorites",
        ["platform", sa.literal_column("lower(username)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_favorites_platform_username", table_name="favorites")
    op.drop_table("favorites")
