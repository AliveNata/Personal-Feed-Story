"""library events

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("folder_id", sa.BigInteger(), nullable=True),
        sa.Column("folder_name", sa.String(length=100), nullable=False),
        sa.Column("target_folder_id", sa.BigInteger(), nullable=True),
        sa.Column("target_folder_name", sa.String(length=100), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("created_at", "folder_id", "target_folder_id"):
        op.create_index(op.f(f"ix_library_events_{column}"), "library_events", [column])


def downgrade() -> None:
    for column in ("created_at", "folder_id", "target_folder_id"):
        op.drop_index(op.f(f"ix_library_events_{column}"), table_name="library_events")
    op.drop_table("library_events")
