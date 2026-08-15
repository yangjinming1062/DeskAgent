"""timestamptz: all DateTime columns become timezone-aware

Whole-database naive→aware flip. Existing naive values were written as UTC by
the old ``naive_utc_now()`` convention, so every conversion pins
``AT TIME ZONE 'UTC'`` — without it Postgres would reinterpret each value in
the session timezone and shift every timestamp.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-15 10:46:04.175695

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: list[tuple[str, str]] = [
    ("admin_sessions", "created_at"),
    ("admin_sessions", "revoked_at"),
    ("avatar_assets", "created_at"),
    ("companion_expressions", "created_at"),
    ("companion_expressions", "updated_at"),
    ("companion_models", "created_at"),
    ("companion_models", "updated_at"),
    ("companion_sprite_images", "created_at"),
    ("companion_sprite_images", "updated_at"),
    ("conversations", "created_at"),
    ("conversations", "updated_at"),
    ("cron_jobs", "next_run_at"),
    ("cron_jobs", "created_at"),
    ("login_records", "login_at"),
    ("login_records", "logout_at"),
    ("login_records", "last_seen_at"),
    ("memories", "created_at"),
    ("memories", "updated_at"),
    ("messages", "created_at"),
    ("personas", "created_at"),
    ("personas", "updated_at"),
    ("update_versions", "created_at"),
    ("user_model_configs", "created_at"),
    ("user_model_configs", "updated_at"),
    ("user_settings", "created_at"),
    ("user_settings", "updated_at"),
    ("users", "expires_at"),
    ("users", "created_at"),
    ("users", "updated_at"),
    ("video_gen_jobs", "created_at"),
    ("video_gen_jobs", "updated_at"),
    ("video_gen_jobs", "expires_at"),
    ("wardrobe_items", "created_at"),
    ("wardrobe_items", "updated_at"),
    ("ws_events", "created_at"),
]


def upgrade() -> None:
    for table, col in _COLUMNS:
        op.alter_column(table, col, existing_type=postgresql.TIMESTAMP(), type_=sa.DateTime(timezone=True), postgresql_using=f"{col} AT TIME ZONE 'UTC'")


def downgrade() -> None:
    for table, col in reversed(_COLUMNS):
        op.alter_column(table, col, existing_type=sa.DateTime(timezone=True), type_=postgresql.TIMESTAMP(), postgresql_using=f"{col} AT TIME ZONE 'UTC'")
