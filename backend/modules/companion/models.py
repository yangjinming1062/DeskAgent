from datetime import datetime
from typing import TYPE_CHECKING

from common import ModelBase, TimestampMixin
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from modules.auth import User


class CompanionPreference(ModelBase, TimestampMixin):
    """每用户服务端伴侣门控；desktop 仍是打扰档位的真源并在每次变更/WS 重连时重报，持久化行保证服务端门控（proactive send_message_tool、cron kicks）在后端重启后仍生效。"""

    __tablename__ = "companion_preferences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    disturbance_tier: Mapped[str] = mapped_column(String(16), default="normal")


class CompanionModel(ModelBase, TimestampMixin):
    """供应商生成的 3D 模型；status 流转：generating → pending_download → downloading → succeeded | failed；下载阶段任意失败 → download_failed（可通过 ``companion.model.retryDownload`` 重试，付费结果保存在 provider_task_id + download_urls_json 中）。"""

    __tablename__ = "companion_models"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    asset_url: Mapped[str] = mapped_column(Text, default="")
    source_portrait_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), default="base_texture")
    species: Mapped[str] = mapped_column(String(64), default="人类", server_default=text("'人类'"))
    rig_type: Mapped[str] = mapped_column(String(32), default="biped", server_default=text("'biped'"), index=True)
    rig_naming: Mapped[str] = mapped_column(String(16), default="mixamo", server_default=text("'mixamo'"))
    # 模型生成所用的 seed 图风格（anime | realistic）—— 路由客户端渲染风格；旧行默认 realistic 以保留 PBR 外观。
    style: Mapped[str] = mapped_column(String(16), default="realistic", server_default=text("'realistic'"))
    rig_original_url: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    morph_params_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    has_rig: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    has_morph_targets: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"))
    animation_clips_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="", server_default=text("''"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    # 付费结果恢复句柄：生成完成瞬间、下载开始前写入，保证下载失败也不丢已计费资产；provider_task_id 是「再次查问即得 URL」的 id（云端 rigged 用 rig task id，其他用 submit id）。
    provider_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    download_urls_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


class CompanionExpression(ModelBase, TimestampMixin):
    """自定义情绪注册表：LLM 创建的情绪 token，可用作 [affect:NAME]；情绪头像图（内置或自定义）存在 CompanionExpressionAvatar 里按 name 索引，本表只登记 token 与 clip 匹配 / 展示元数据。"""

    __tablename__ = "companion_expressions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(32))
    valence: Mapped[str] = mapped_column(String(16), default="neutral")
    description: Mapped[str] = mapped_column(Text, default="")
    # 可选单个 emoji 图标，聊天坞站 label 旁展示。
    icon: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")


class CompanionExpressionAvatar(ModelBase, TimestampMixin):
    """聊天窗口情绪头像图缓存，按情绪 token + 头像身份复合键。Lookup 为 (user_id, name, avatar_id) 完全匹配；头像重生成后旧行作废，按需 lazy 重建，丢失可容忍（多生成一次而已）。"""

    __tablename__ = "companion_expression_avatars"
    __table_args__ = (UniqueConstraint("user_id", "name", "avatar_id", name="uq_companion_expression_avatars_key"),)

    # 不另起 user_id 索引：unique (user_id, name, avatar_id) 自带的索引已覆盖 user_id-prefix 查询。
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    avatar_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    asset_url: Mapped[str] = mapped_column(String(2048))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="", server_default=text("''"))


class Persona(ModelBase, TimestampMixin):
    """system_prompt_extras 单列，使 persona 改动只重渲一行而不是所有历史消息。"""

    __tablename__ = "personas"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    personality_tags_json: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    system_prompt_extras: Mapped[str] = mapped_column(Text, default="")
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    is_portrait_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    portrait_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="persona")


class AvatarAsset(ModelBase):
    """asset_url 存在 companion-avatars/（持久）以让重新登录跨过 24h temp-media TTL。"""

    __tablename__ = "avatar_assets"
    # 部分唯一索引（每用户一个 active）位于 alembic baseline——需要 WHERE 子句。

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt_json: Mapped[str] = mapped_column(Text)
    asset_url: Mapped[str] = mapped_column(String(2048))
    style: Mapped[str] = mapped_column(String(64), default="")
    seed_front_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed_right_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed_back_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed_left_url: Mapped[str] = mapped_column(String(2048), default="", server_default=text("''"))
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("FALSE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="avatar_assets")


class CompanionSpriteImage(ModelBase, TimestampMixin):
    """静态 2D 精灵相册条目，3D 模型尚未渲染时按需 lazy 生成。tag 为 LLM 自撰自由标签作相册匹配 key；role='waiting' 是每用户一张的等待/切换精灵（部分唯一索引在 alembic baseline）；avatar_id 与当前 active 不一致视为陈旧身份，排除在匹配之外。asset_url 是裸 companion-assets/<uid>/ 路径，读取时重新签名。"""

    __tablename__ = "companion_sprite_images"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    avatar_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tag: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    prompt: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    request_text: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    asset_url: Mapped[str] = mapped_column(String(2048))
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default="", server_default=text("''"))
