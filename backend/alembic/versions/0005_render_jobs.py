"""render_jobs queue table + NOTIFY wakeup trigger for the worker LISTEN loop

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-15 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "render_jobs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claimed_by", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_render_jobs_status"), "render_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_render_jobs_user_id"), "render_jobs", ["user_id"], unique=False)
    # Mirrors the ws_events wakeup trigger from 0001: enqueue = row insert →
    # NOTIFY wakes the worker's LISTEN loop instead of it polling.
    op.execute("""
CREATE FUNCTION notify_render_job() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('render_jobs_channel', 'wakeup');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")
    op.execute("""
CREATE TRIGGER render_jobs_notify_trigger
AFTER INSERT ON render_jobs
FOR EACH ROW WHEN (NEW.status = 'queued') EXECUTE FUNCTION notify_render_job();
""")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS render_jobs_notify_trigger ON render_jobs")
    op.execute("DROP FUNCTION IF EXISTS notify_render_job()")
    op.drop_index(op.f("ix_render_jobs_user_id"), table_name="render_jobs")
    op.drop_index(op.f("ix_render_jobs_status"), table_name="render_jobs")
    op.drop_table("render_jobs")
