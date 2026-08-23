"""删除 avatar_assets 的左右视角种子列：3D 生模改为双视图（front + ），左右视角不再使用。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic 用的版本标识符。
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("avatar_assets", "seed_right_url")
    op.drop_column("avatar_assets", "seed_left_url")


def downgrade() -> None:
    op.add_column(
        "avatar_assets",
        sa.Column("seed_right_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "avatar_assets",
        sa.Column("seed_left_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
    )
