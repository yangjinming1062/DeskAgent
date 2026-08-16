"""user activation_code column + drop unused password_hash

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "password_hash")
    op.add_column("users", sa.Column("activation_code", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "activation_code")
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
