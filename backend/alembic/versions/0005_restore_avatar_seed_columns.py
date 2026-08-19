"""avatar_assets: restore fullbody seed-image columns

Re-enables the fullbody seed pipeline with dual-style exploration and
automatic multiview generation for image-to-3D model generation.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("avatar_assets", sa.Column("seed_front_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False))
    op.add_column("avatar_assets", sa.Column("seed_right_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False))
    op.add_column("avatar_assets", sa.Column("seed_back_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False))


def downgrade() -> None:
    op.drop_column("avatar_assets", "seed_back_url")
    op.drop_column("avatar_assets", "seed_right_url")
    op.drop_column("avatar_assets", "seed_front_url")
