"""avatar_assets: drop the fullbody seed-image columns

The fullbody seed pipeline (front/right/back seed images feeding image-to-3D)
is retired in favour of text-to-3D reading the avatar + persona. The columns
go away with the pipeline; historical seed files under companion-avatars/ stay
on disk untouched (no code references them anymore).

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("avatar_assets", "seed_front_url")
    op.drop_column("avatar_assets", "seed_right_url")
    op.drop_column("avatar_assets", "seed_back_url")


def downgrade() -> None:
    op.add_column("avatar_assets", sa.Column("seed_front_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False))
    op.add_column("avatar_assets", sa.Column("seed_right_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False))
    op.add_column("avatar_assets", sa.Column("seed_back_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False))
