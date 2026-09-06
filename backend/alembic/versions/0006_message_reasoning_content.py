"""add reasoning_content to messages

工作台支持模型推理过程展示，持久化助手消息的思考与推理过程文本。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("reasoning_content", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "reasoning_content")
