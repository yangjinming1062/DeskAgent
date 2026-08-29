"""drop personas.system_prompt_extras

历史列：onboarding 时把渲染好的（中文）人设块烤进数据库的缓存列，运行期由
build_system_prompt_extras(persona, language=...) 改为按 session language 从
definition_json 实时渲染后该列成为纯死数据。drop column 配套 models.py / serializers.py
同步删除引用。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic 用的版本标识符。
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("personas", "system_prompt_extras")


def downgrade() -> None:
    op.add_column(
        "personas",
        sa.Column("system_prompt_extras", sa.Text(), nullable=False, server_default=""),
    )
