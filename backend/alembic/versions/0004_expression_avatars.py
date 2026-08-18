"""Expressions move from 3D morph weights to generated avatar images

companion_expressions becomes a pure custom-emotion registry (weights_json and
scale_boost dropped — the 3D face no longer plays emotion morphs; only blink
and lip-sync survive). New companion_expression_avatars caches the generated
chat-window expression images keyed by (user_id, name, avatar_id). The
optional icon column feeds the chat dock's emotion chip for custom tokens.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
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

    op.drop_column("companion_expressions", "weights_json")
    op.drop_column("companion_expressions", "scale_boost")
    op.add_column("companion_expressions", sa.Column("icon", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("companion_expressions", "icon")
    op.add_column("companion_expressions", sa.Column("scale_boost", sa.Float(), server_default=sa.text("1.0"), nullable=False))
    op.add_column("companion_expressions", sa.Column("weights_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False))

    op.drop_table("companion_expression_avatars")
