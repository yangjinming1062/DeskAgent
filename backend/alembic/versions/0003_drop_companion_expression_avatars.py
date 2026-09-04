"""drop companion_expression_avatars

聊天窗口废弃表情头像展示与生成后，表 companion_expression_avatars 成为无用数据。
配套 models.py / serializers.py / cascade.py 等一并移除引用。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic 用的版本标识符。
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("companion_expression_avatars")


def downgrade() -> None:
    op.create_table(
        "companion_expression_avatars",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=True),
        sa.Column("prompt", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("asset_url", sa.String(length=2048), nullable=False),
        sa.Column("content_hash", sa.String(length=64), server_default=sa.text("''"), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", "avatar_id", name="uq_companion_expression_avatars_key"),
    )
