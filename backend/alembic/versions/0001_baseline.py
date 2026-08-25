"""baseline：完整 schema + pgvector/pg_trgm 扩展、partial unique 与 HNSW/GIN 索引、ws_events NOTIFY 触发器、2D 模型管线与 persona.render_mode；首次压缩版本。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# Alembic 用的版本标识符。
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 先建扩展：memories.embedding 是 vector(1536)，必须在 create_table 之前存在该类型。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "admin_sessions",
        sa.Column("token_jti", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("client_version", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_jti", name="uq_admin_sessions_token_jti"),
    )
    op.create_index(op.f("ix_admin_sessions_created_at"), "admin_sessions", ["created_at"], unique=False)
    op.create_index(op.f("ix_admin_sessions_is_active"), "admin_sessions", ["is_active"], unique=False)
    op.create_index(op.f("ix_admin_sessions_token_jti"), "admin_sessions", ["token_jti"], unique=False)
    op.create_index(op.f("ix_admin_sessions_username"), "admin_sessions", ["username"], unique=False)
    op.create_table(
        "update_versions",
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("release_notes", sa.Text(), nullable=False),
        sa.Column("exe_filename", sa.String(length=256), nullable=False),
        sa.Column("exe_sha512", sa.String(length=128), nullable=False),
        sa.Column("exe_size", sa.Integer(), nullable=False),
        sa.Column("mac_filename", sa.String(length=256), nullable=True),
        sa.Column("mac_sha512", sa.String(length=128), nullable=True),
        sa.Column("mac_size", sa.Integer(), nullable=True),
        sa.Column("runner_filename", sa.String(length=256), nullable=True),
        sa.Column("runner_sha512", sa.String(length=128), nullable=True),
        sa.Column("runner_size", sa.Integer(), nullable=True),
        sa.Column("runner_version", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_update_versions_is_active"), "update_versions", ["is_active"], unique=False)
    op.create_index(op.f("ix_update_versions_version"), "update_versions", ["version"], unique=True)
    op.create_table(
        "users",
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("activation_code", sa.Text(), nullable=True),
        sa.Column("activation_token_hash", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("nightly_activity_enabled", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_activation_token_hash"), "users", ["activation_token_hash"], unique=True)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_table(
        "avatar_assets",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("prompt_json", sa.Text(), nullable=False),
        sa.Column("asset_url", sa.String(length=2048), nullable=False),
        sa.Column("style", sa.String(length=64), nullable=False),
        sa.Column("seed_front_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("seed_back_url", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_avatar_assets_active"), "avatar_assets", ["active"], unique=False)
    op.create_index(op.f("ix_avatar_assets_user_id"), "avatar_assets", ["user_id"], unique=False)
    op.create_table(
        "companion_expressions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("valence", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("icon", sa.String(length=16), nullable=True),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_expressions_user_id"), "companion_expressions", ["user_id"], unique=False)
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
    op.create_table(
        "companion_3d_models",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("asset_url", sa.Text(), nullable=False),
        sa.Column("source_portrait_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("species", sa.String(length=64), server_default=sa.text("'人类'"), nullable=False),
        sa.Column("rig_type", sa.String(length=32), server_default=sa.text("'biped'"), nullable=False),
        sa.Column("rig_naming", sa.String(length=16), server_default=sa.text("'tripo'"), nullable=False),
        sa.Column("style", sa.String(length=16), server_default=sa.text("'realistic'"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("has_rig", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("clip_map_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("provider_phase", sa.String(length=16), server_default=sa.text("'submit'"), nullable=False),
        sa.Column("content_hash", sa.String(length=64), server_default=sa.text("''"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("provider_task_id", sa.String(length=128), nullable=True),
        sa.Column("download_urls_json", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_3d_models_active"), "companion_3d_models", ["active"], unique=False)
    op.create_index(op.f("ix_companion_3d_models_rig_type"), "companion_3d_models", ["rig_type"], unique=False)
    op.create_index(op.f("ix_companion_3d_models_user_id"), "companion_3d_models", ["user_id"], unique=False)
    op.create_table(
        "companion_outfits",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("fullbody_url", sa.String(length=2048), nullable=False),
        sa.Column("style", sa.String(length=32), server_default=sa.text("'cel_shading'"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("source_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("pending_wear", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_outfits_user_id"), "companion_outfits", ["user_id"], unique=False)
    op.create_index(op.f("ix_companion_outfits_status"), "companion_outfits", ["status"], unique=False)
    op.create_index(op.f("ix_companion_outfits_active"), "companion_outfits", ["active"], unique=False)
    op.create_table(
        "companion_2d_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=True),
        sa.Column("outfit_id", sa.Integer(), nullable=True),
        sa.Column("style", sa.String(length=32), server_default=sa.text("'cel_shading'"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'generating'"), nullable=False),
        sa.Column("manifest_json", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("manifest_path", sa.String(length=2048), server_default=sa.text("''"), nullable=False),
        sa.Column("layers_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("priority", sa.String(length=8), server_default=sa.text("'high'"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["outfit_id"], ["companion_outfits.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_companion_2d_models_active"), "companion_2d_models", ["active"], unique=False)
    op.create_index(op.f("ix_companion_2d_models_status"), "companion_2d_models", ["status"], unique=False)
    op.create_index(op.f("ix_companion_2d_models_user_id"), "companion_2d_models", ["user_id"], unique=False)
    op.create_table(
        "conversations",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), server_default=sa.text("'standard'"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("cwd", sa.String(length=1024), nullable=True),
        sa.Column("settings_json", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_conversations_parent_id"), "conversations", ["parent_id"], unique=False)
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=False)
    op.create_table(
        "cron_jobs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("schedule", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("deliver", sa.String(length=64), nullable=False),
        sa.Column("is_paused", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("one_shot", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cron_jobs_name"), "cron_jobs", ["name"], unique=False)
    op.create_index(op.f("ix_cron_jobs_next_run_at"), "cron_jobs", ["next_run_at"], unique=False)
    op.create_index(op.f("ix_cron_jobs_user_id"), "cron_jobs", ["user_id"], unique=False)
    op.create_table(
        "login_records",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_jti", sa.String(length=64), nullable=False),
        sa.Column("client_version", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=False),
        sa.Column("login_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("logout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_jti", name="uq_login_records_token_jti"),
    )
    op.create_index(op.f("ix_login_records_is_active"), "login_records", ["is_active"], unique=False)
    op.create_index(op.f("ix_login_records_login_at"), "login_records", ["login_at"], unique=False)
    op.create_index(op.f("ix_login_records_token_jti"), "login_records", ["token_jti"], unique=False)
    op.create_index(op.f("ix_login_records_user_id"), "login_records", ["user_id"], unique=False)
    op.create_table(
        "memories",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("importance", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("embedding", Vector(dim=1536), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_memories_user_id"), "memories", ["user_id"], unique=False)
    op.create_table(
        "personas",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("personality_tags_json", sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("system_prompt_extras", sa.Text(), nullable=False),
        sa.Column("is_complete", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("is_portrait_confirmed", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("portrait_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("render_mode", sa.String(length=8), server_default=sa.text("'2d'"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_personas_is_complete"), "personas", ["is_complete"], unique=False)
    op.create_index(op.f("ix_personas_is_portrait_confirmed"), "personas", ["is_portrait_confirmed"], unique=False)
    op.create_index(op.f("ix_personas_render_mode"), "personas", ["render_mode"], unique=False)
    op.create_index(op.f("ix_personas_user_id"), "personas", ["user_id"], unique=True)
    op.create_table(
        "user_model_configs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("llm_provider", sa.String(length=64), nullable=False),
        sa.Column("llm_base_url", sa.String(length=255), nullable=False),
        sa.Column("llm_api_key", sa.Text(), nullable=False),
        sa.Column("llm_model_name", sa.String(length=128), nullable=False),
        sa.Column("stt_provider", sa.String(length=64), nullable=False),
        sa.Column("stt_base_url", sa.String(length=255), nullable=False),
        sa.Column("stt_api_key", sa.Text(), nullable=False),
        sa.Column("stt_model_name", sa.String(length=128), nullable=False),
        sa.Column("tts_provider", sa.String(length=64), nullable=False),
        sa.Column("tts_base_url", sa.String(length=255), nullable=False),
        sa.Column("tts_api_key", sa.Text(), nullable=False),
        sa.Column("tts_model_name", sa.String(length=128), nullable=False),
        sa.Column("image_gen_provider", sa.String(length=64), nullable=False),
        sa.Column("image_gen_base_url", sa.String(length=255), nullable=False),
        sa.Column("image_gen_api_key", sa.Text(), nullable=False),
        sa.Column("image_gen_model_name", sa.String(length=128), nullable=False),
        sa.Column("video_gen_provider", sa.String(length=64), nullable=False),
        sa.Column("video_gen_base_url", sa.String(length=255), nullable=False),
        sa.Column("video_gen_api_key", sa.Text(), nullable=False),
        sa.Column("video_gen_model_name", sa.String(length=128), nullable=False),
        sa.Column("provider_config", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_model_configs_user_id"), "user_model_configs", ["user_id"], unique=True)
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("setting_key", sa.String(length=128), nullable=False),
        sa.Column("setting_value", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "setting_key", name="uq_user_settings_user_key"),
    )
    op.create_index(op.f("ix_user_settings_setting_key"), "user_settings", ["setting_key"], unique=False)
    op.create_index(op.f("ix_user_settings_user_id"), "user_settings", ["user_id"], unique=False)
    op.create_table(
        "video_gen_jobs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_task_id", sa.String(length=128), nullable=True),
        sa.Column("provider_file_id", sa.String(length=128), nullable=True),
        sa.Column("file_id", sa.String(length=64), nullable=True),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("error_reason", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_video_gen_jobs_created_at"), "video_gen_jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_provider_task_id"), "video_gen_jobs", ["provider_task_id"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_status"), "video_gen_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_video_gen_jobs_user_id"), "video_gen_jobs", ["user_id"], unique=False)
    op.create_index("ix_video_gen_jobs_user_status", "video_gen_jobs", ["user_id", "status"], unique=False)
    op.create_table(
        "ws_events",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ws_events_created_at"), "ws_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_ws_events_next_retry_at"), "ws_events", ["next_retry_at"], unique=False)
    op.create_index(op.f("ix_ws_events_status"), "ws_events", ["status"], unique=False)
    op.create_index(op.f("ix_ws_events_user_id"), "ws_events", ["user_id"], unique=False)
    op.create_index("ix_ws_events_poll", "ws_events", ["user_id", "status", "next_retry_at"], unique=False)
    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("subtype", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", sa.Text(), nullable=True),
        sa.Column("tool_call_id", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("turn_duration_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("content_type", sa.String(length=32), server_default=sa.text("'text'"), nullable=False),
        sa.Column("summary_date", sa.String(length=10), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_messages_subtype"), "messages", ["subtype"], unique=False)
    op.create_index(op.f("ix_messages_summary_date"), "messages", ["summary_date"], unique=False)
    op.create_table(
        "companion_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("disturbance_tier", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_companion_preferences_user_id"),
    )

    # Partial unique 索引（声明式模型无法表达）。
    # 并发 POST /model 否则会留下两条 active 行。
    op.create_index("uq_avatar_assets_one_active", "avatar_assets", ["user_id"], unique=True, postgresql_where=sa.text("active"))
    op.create_index("uq_companion_3d_models_one_active", "companion_3d_models", ["user_id"], unique=True, postgresql_where=sa.text("active"))
    # 每用户一个穿着中外观；一个切分中外观（并发 confirm 的硬保证，服务层另有用户级锁）
    op.create_index("uq_companion_outfits_one_active", "companion_outfits", ["user_id"], unique=True, postgresql_where=sa.text("active"))
    op.create_index("uq_companion_outfits_one_splitting", "companion_outfits", ["user_id"], unique=True, postgresql_where=sa.text("status = 'splitting'"))
    # 每用户一条 main 会话；全 (user_id, kind) 唯一会禁止多条 "standard" 会话。防御并发 boot / cron kick / prompt.submit 的 get_or_create 竞态。
    op.create_index("uq_conversations_user_main", "conversations", ["user_id"], unique=True, postgresql_where=sa.text("kind = 'main'"))
    # 每用户一个 waiting/switch 精灵；resolve_sprite 在插入前删旧行，因此也覆盖并发请求。
    op.create_index("uq_companion_expressions_user_name", "companion_expressions", ["user_id", "name"], unique=True)
    op.create_index("uq_memories_user_context", "memories", ["user_id", "context"], unique=True, postgresql_where=sa.text("context LIKE 'user_profile:%'"))
    # 每 (user, slot) 一行，让 memory_retain(kind='auto_inject') 原子 upsert。
    op.create_index("uq_memories_auto_inject_slot", "memories", ["user_id", "context"], unique=True, postgresql_where=sa.text("context LIKE 'auto_inject:%'"))
    op.create_index("uq_memories_inferred_profile_slot", "memories", ["user_id", "context"], unique=True, postgresql_where=sa.text("context LIKE 'inferred_profile:%'"))
    op.create_index("uq_memories_diary_day", "memories", ["user_id", "context"], unique=True, postgresql_where=sa.text("context LIKE 'diary:%'"))
    # 加速 recall consolidator 的 count-and-recent 查询。
    op.create_index("ix_memories_recall_user_updated", "memories", ["user_id", sa.text("updated_at DESC")], unique=False, postgresql_where=sa.text("context LIKE 'recall:%'"))

    # 向量 / trigram 检索索引。
    op.create_index("ix_memories_embedding", "memories", ["embedding"], unique=False, postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"})
    op.create_index("ix_memories_content_trgm", "memories", ["content"], unique=False, postgresql_using="gin", postgresql_ops={"content": "gin_trgm_ops"})
    op.create_index("ix_memories_context_trgm", "memories", ["context"], unique=False, postgresql_using="gin", postgresql_ops={"context": "gin_trgm_ops"})

    # Outbox 表的 LISTEN/NOTIFY 唤醒触发器（ARCHITECTURE.md §5）。
    op.execute("""
CREATE FUNCTION notify_ws_event() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('ws_events_channel', 'wakeup');
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")
    op.execute("""
CREATE TRIGGER ws_event_notify_trigger
AFTER INSERT ON ws_events
FOR EACH STATEMENT EXECUTE FUNCTION notify_ws_event();
""")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ws_event_notify_trigger ON ws_events")
    op.execute("DROP FUNCTION IF EXISTS notify_ws_event()")
    # 先子表再父表（messages → conversations → users）。
    for table in (
        "messages",
        "ws_events",
        "video_gen_jobs",
        "user_settings",
        "user_model_configs",
        "personas",
        "memories",
        "login_records",
        "cron_jobs",
        "companion_expression_avatars",
        "companion_3d_models",
        "companion_2d_models",
        "companion_outfits",
        "companion_expressions",
        "avatar_assets",
        "companion_preferences",
        "conversations",
        "users",
        "update_versions",
        "admin_sessions",
    ):
        op.drop_table(table)
