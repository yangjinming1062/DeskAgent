from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Persona blob 整体作为 JSON 字符串传输；32 KiB 在 HTTP 边界把 DoS 封顶，同时给最大 persona 字段（2000 字符）+ user_* 字段 + JSON 开销留余量。
_PERSONA_JSON_MAX_LEN: int = 32 * 1024


class PersonaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_json: str = Field(min_length=1, max_length=_PERSONA_JSON_MAX_LEN)


class PersonaResponse(BaseModel):
    definition_json: str
    is_complete: bool
    personality_tags: list[str] = Field(default_factory=list)


# 生成是同步的——所有持久化资产都是 succeeded；钉死字面量以便未来若改为异步时契约仍清楚。
SucceededStatus = Literal["succeeded"]


class AvatarAssetResponse(BaseModel):
    id: int
    asset_url: str
    seed_front_url: str = ""
    seed_back_url: str = ""
    supports_multiview: bool = False
    # 已选 fullbody 风格 + 持久 style-sample URL（读取时重签名），作为全身确认阶段的 resume 入口。
    fullbody_style: str = ""
    fullbody_samples: dict[str, str] = Field(default_factory=dict)
    prompt: str = ""
    status: SucceededStatus = "succeeded"


class FullbodyStyleItem(BaseModel):
    id: str
    label_zh: str
    description_zh: str = ""


class FullbodySamplesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)


class FullbodySamplesResponse(BaseModel):
    samples: dict[str, str] = Field(default_factory=dict)


class FullbodyFrontGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str = Field(default="cel_shading", max_length=64)
    feedback: str | None = Field(default=None, max_length=500)
    image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)


class FullbodyBackGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str = Field(default="cel_shading", max_length=64)
    feedback: str | None = Field(default=None, max_length=500)
    front_url: str | None = Field(default=None, max_length=2048)


class FullbodyConfirmFrontRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str | None = Field(default=None, max_length=64)
    front_url: str | None = Field(default=None, max_length=2048)
    back_url: str | None = Field(default=None, max_length=2048)


class FullbodySelectStyleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str = Field(min_length=1, max_length=64)


class AvatarGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AvatarFromImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 与上传相同的 8 MiB 上限：图同时是供应商 seed（经签名 URL）和供应商重新渲染的真相源。
    image: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    # 身份锚之外的呈现/风格参考图（可选）。
    presentation_image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    presentation_content_type: str | None = Field(default=None, max_length=64)


class AvatarHistoryResponse(BaseModel):
    history: list[AvatarAssetResponse]


class CompanionModelResponse(BaseModel):
    id: int
    asset_url: str | None = None
    provider: str
    species: str = "人类"
    rig_type: str = "biped"
    rig_naming: str = "tripo"
    # 模型生成所用的 seed 风格，路由客户端渲染风格。
    style: str = "realistic"
    status: str = "succeeded"
    has_rig: bool
    content_hash: str | None = None
    # 语义键 → GLB 内 clip 名；客户端据此兑现动作，自身不持有任何供应商命名。
    clip_map: dict[str, str] = Field(default_factory=dict)


class ModelGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species_override: str | None = Field(default=None, max_length=64)
    provider: Literal["tripo", "hunyuan"] | None = None
    # False 幂等返回现有 active 模型；True 强制付费重新生成。
    force: bool = False


class SpriteResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=500)
    # "waiting" = 最高优先级等待/切换精灵（每用户一张，跳过相册匹配）。
    role: Literal["waiting"] | None = None
    force_new: bool = False


class SpriteImageResponse(BaseModel):
    id: int
    url: str
    tag: str
    content_hash: str | None = None
    generated: bool


class ExpressionAvatarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    force_new: bool = False
