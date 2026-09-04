"""room backdrops + moments + diary entries

生活空间产品化：把房间图（背景必须含角色）、时刻（生活空间时间线）、日记
（每日第一人称）从原来的 memories 单一通道拆出，对应 PROTOCOL.md §1.x 增补。

未部署阶段的增量迁移；与基线 migration env 同步通过 models.metadata 校验。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companion_room_backdrops",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("origin", sa.String(length=16), server_default=sa.text("'onboarding'"), nullable=False),
        sa.Column("intent", sa.String(length=16), server_default=sa.text("'decorate'"), nullable=False),
        sa.Column("brief", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("prompt", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("media_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("public_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("seed_portrait_media_id", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("seed_outfit_media_id", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("outfit_fingerprint", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("contains_character", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("error_utterance", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_room_backdrops_user_id"), "companion_room_backdrops", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_room_backdrops_status"), "companion_room_backdrops", ["status"], unique=False)
    op.create_index(op.f("ix_companion_room_backdrops_outfit_fingerprint"), "companion_room_backdrops", ["outfit_fingerprint"], unique=False)

    op.add_column(
        "personas",
        sa.Column("active_backdrop_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "personas",
        sa.Column("backdrop_policy", sa.String(length=16), server_default=sa.text("'llm_may_replace'"), nullable=False),
    )
    op.create_foreign_key(
        "personas_active_backdrop_id_fkey",
        "personas",
        "companion_room_backdrops",
        ["active_backdrop_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "companion_moments",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default=sa.text("'greeting'"), nullable=False),
        sa.Column("title", sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("emotion", sa.String(length=32), nullable=True),
        sa.Column("media_url", sa.String(length=2048), nullable=True),
        sa.Column("source", sa.String(length=16), server_default=sa.text("'system'"), nullable=False),
        sa.Column("memory_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("visibility", sa.String(length=16), server_default=sa.text("'shown'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_moments_user_id"), "companion_moments", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_moments_occurred_at"), "companion_moments", ["occurred_at"], unique=False)
    op.create_index(op.f("ix_companion_moments_kind"), "companion_moments", ["kind"], unique=False)

    op.create_table(
        "companion_diary_entries",
        sa.Column("id", UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=128), server_default=sa.text("''"), nullable=False),
        sa.Column("body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("mood", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=16), server_default=sa.text("'nightly'"), nullable=False),
        sa.Column("memory_ids", ARRAY(sa.String()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("moment_ids", ARRAY(sa.String()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "entry_date", name="uq_companion_diary_user_date"),
    )
    op.create_index(op.f("ix_companion_diary_entries_user_id"), "companion_diary_entries", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_diary_entries_entry_date"), "companion_diary_entries", ["entry_date"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_companion_diary_entries_entry_date"), table_name="companion_diary_entries")
    op.drop_index(op.f("ix_companion_diary_entries_user_id"), table_name="companion_diary_entries")
    op.drop_table("companion_diary_entries")

    op.drop_index(op.f("ix_companion_moments_kind"), table_name="companion_moments")
    op.drop_index(op.f("ix_companion_moments_occurred_at"), table_name="companion_moments")
    op.drop_index(op.f("ix_companion_moments_user_id"), table_name="companion_moments")
    op.drop_table("companion_moments")

    op.drop_constraint("personas_active_backdrop_id_fkey", "personas", type_="foreignkey")
    op.drop_column("personas", "backdrop_policy")
    op.drop_column("personas", "active_backdrop_id")

    op.drop_index(op.f("ix_companion_room_backdrops_outfit_fingerprint"), table_name="companion_room_backdrops")
    op.drop_index(op.f("ix_companion_room_backdrops_status"), table_name="companion_room_backdrops")
    op.drop_index(op.f("ix_companion_room_backdrops_user_id"), table_name="companion_room_backdrops")
    op.drop_table("companion_room_backdrops")
