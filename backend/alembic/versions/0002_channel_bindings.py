"""IM 通道桥：channel_bindings / channel_peers 两表（外接 IM 渠道绑定与对端白名单）。

conversation_id 唯一外键是「每用户每渠道一条专属 im 会话」的 DB 级锚点：binding 的
(user_id, channel) 唯一性传递为渠道间不混流，UNIQUE 又阻止两条绑定共享同一会话。
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
    op.create_table(
        "channel_bindings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'disabled'"), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("config_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("credentials", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("account_ref", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("account_name", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("channel_bindings_pkey")),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], name=op.f("channel_bindings_conversation_id_fkey"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("channel_bindings_user_id_fkey"), ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "channel", name="uq_channel_bindings_user_channel"),
        sa.UniqueConstraint("conversation_id", name=op.f("uq_channel_bindings_conversation_id")),
    )
    op.create_index(op.f("ix_channel_bindings_user_id"), "channel_bindings", ["user_id"], unique=False)
    op.create_index(op.f("ix_channel_bindings_status"), "channel_bindings", ["status"], unique=False)
    op.create_table(
        "channel_peers",
        sa.Column("binding_id", sa.Integer(), nullable=False),
        sa.Column("peer_id", sa.String(length=128), nullable=False),
        sa.Column("peer_name", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("channel_peers_pkey")),
        sa.ForeignKeyConstraint(["binding_id"], ["channel_bindings.id"], name=op.f("channel_peers_binding_id_fkey"), ondelete="CASCADE"),
        sa.UniqueConstraint("binding_id", "peer_id", name="uq_channel_peers_binding_peer"),
    )
    op.create_index(op.f("ix_channel_peers_binding_id"), "channel_peers", ["binding_id"], unique=False)
    op.create_index(op.f("ix_channel_peers_status"), "channel_peers", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_channel_peers_status"), table_name="channel_peers")
    op.drop_index(op.f("ix_channel_peers_binding_id"), table_name="channel_peers")
    op.drop_table("channel_peers")
    op.drop_index(op.f("ix_channel_bindings_status"), table_name="channel_bindings")
    op.drop_index(op.f("ix_channel_bindings_user_id"), table_name="channel_bindings")
    op.drop_table("channel_bindings")
