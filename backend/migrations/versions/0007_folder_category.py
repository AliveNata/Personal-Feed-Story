"""folder category

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every folder that exists today is a local account: the default puts them all under Lokal.
    op.add_column(
        "folders",
        sa.Column("category", sa.String(20), nullable=False, server_default="lokal"),
    )
    op.create_check_constraint(
        "ck_folders_category", "folders", "category in ('lokal', 'internasional')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_folders_category", "folders", type_="check")
    op.drop_column("folders", "category")
