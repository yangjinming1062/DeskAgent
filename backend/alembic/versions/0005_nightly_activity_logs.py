"""nightly activity logs

夜间自主活动流水线运行记录：每次流水线执行（含跳过）写入一条日志，
管理员页面可查看每日活动详情。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nightly_activity_logs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'running'"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "target_date", name="uq_nightly_activity_logs_user_date"),
    )
    op.create_index(
        op.f("ix_nightly_activity_logs_user_id"),
        "nightly_activity_logs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_nightly_activity_logs_target_date"),
        "nightly_activity_logs",
        ["target_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_nightly_activity_logs_target_date"),
        table_name="nightly_activity_logs",
    )
    op.drop_index(
        op.f("ix_nightly_activity_logs_user_id"),
        table_name="nightly_activity_logs",
    )
    op.drop_table("nightly_activity_logs")
