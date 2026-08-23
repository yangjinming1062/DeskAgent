"""add Mesh2DModel table + persona.render_mode

Revision ID: 0002_add_mesh2d
Revises: 0001_baseline
Create Date: 2026-08-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_add_mesh2d"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mesh2d_models",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=True),
        sa.Column("style", sa.String(length=32), server_default="cel_shading", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="generating", nullable=False),
        sa.Column("manifest_json", sa.Text(), server_default="", nullable=False),
        sa.Column("manifest_path", sa.String(length=2048), server_default="", nullable=False),
        sa.Column("layers_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(length=8), server_default="high", nullable=False),
    )
    op.create_index(op.f("ix_mesh2d_models_user_id"), "mesh2d_models", ["user_id"], unique=False)
    op.create_index(op.f("ix_mesh2d_models_status"), "mesh2d_models", ["status"], unique=False)
    op.create_index(op.f("ix_mesh2d_models_active"), "mesh2d_models", ["active"], unique=False)

    op.add_column(
        "personas",
        sa.Column("render_mode", sa.String(length=8), server_default="2d", nullable=False),
    )
    op.create_index(op.f("ix_personas_render_mode"), "personas", ["render_mode"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_personas_render_mode"), table_name="personas")
    op.drop_column("personas", "render_mode")
    op.drop_index(op.f("ix_mesh2d_models_active"), table_name="mesh2d_models")
    op.drop_index(op.f("ix_mesh2d_models_status"), table_name="mesh2d_models")
    op.drop_index(op.f("ix_mesh2d_models_user_id"), table_name="mesh2d_models")
    op.drop_table("mesh2d_models")
