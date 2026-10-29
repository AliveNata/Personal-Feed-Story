"""page cache and account snapshots

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cached_pages",
        sa.Column("key", sa.String(length=300), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_cached_pages_account", "cached_pages", ["platform", "username"])
    op.create_index(op.f("ix_cached_pages_expires_at"), "cached_pages", ["expires_at"])
    op.create_table(
        "account_snapshots",
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("post_count", sa.Integer(), nullable=True),
        sa.Column("latest_story_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("platform", "username"),
    )


def downgrade() -> None:
    op.drop_table("account_snapshots")
    op.drop_index(op.f("ix_cached_pages_expires_at"), table_name="cached_pages")
    op.drop_index("ix_cached_pages_account", table_name="cached_pages")
    op.drop_table("cached_pages")
