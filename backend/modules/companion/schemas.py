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


class FullbodyConfirmFrontRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str | None = Field(default=None, max_length=64)
    front_url: str | None = Field(default=None, max_length=2048)


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
    rig_naming: str = "mixamo"
    # 模型生成所用的 seed 风格，路由客户端渲染风格。
    style: str = "realistic"
    morph_params: dict = Field(default_factory=dict)
    status: str = "succeeded"
    has_rig: bool
    has_morph_targets: bool
    content_hash: str | None = None


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


class WardrobeItemResponse(BaseModel):
    id: int
    name: str
    category: str
    material_overrides_json: str
    texture_url: str | None = None
    normal_url: str | None = None
    roughness_url: str | None = None
    metalness_url: str | None = None
    displacement_url: str | None = None
    prompt: str | None = None
    outfit_description: str | None = None
    equipped: bool
    origin: str = "user"
    gift_state: str | None = None
    gift_reason: str | None = None
    gift_message: str | None = None
    kind: str = "texture"
    mesh_url: str | None = None
    assembly_json: str = "{}"
    slot: str = "outfit"


class WardrobeGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)


class WardrobePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=500)
    image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)
    feedback: str | None = Field(default=None, max_length=500)


class WardrobePreviewResponse(BaseModel):
    url: str
    prompt: str
    file_id: str
    normal_file_id: str | None = None
    normal_url: str | None = None
    roughness_file_id: str | None = None
    roughness_url: str | None = None
    metalness_file_id: str | None = None
    metalness_url: str | None = None
    displacement_file_id: str | None = None
    displacement_url: str | None = None
    mesh_url: str | None = None
    mesh_file_id: str | None = None
    kind: str = "texture"
    assembly_json: str = "{}"


class WardrobePreviewAcceptedResponse(BaseModel):
    job_id: int
    status: str


class WardrobePreviewJobResponse(WardrobePreviewResponse):
    """轮询的 job 状态；preview 字段仅在 ``status == "succeeded"`` 时有值，因此所有继承字段被重新声明为可选。"""

    job_id: int
    status: str
    error: str | None = None
    url: str | None = None
    prompt: str | None = None
    file_id: str | None = None
    kind: str | None = None
    assembly_json: str | None = None


class WardrobeConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=64)
    prompt: str | None = Field(default=None, max_length=500)
    normal_file_id: str | None = Field(default=None, max_length=128)
    roughness_file_id: str | None = Field(default=None, max_length=128)
    metalness_file_id: str | None = Field(default=None, max_length=128)
    displacement_file_id: str | None = Field(default=None, max_length=128)
    mesh_file_id: str | None = Field(default=None, max_length=128)
    assembly_json: str | None = Field(default=None, max_length=4096)


class WardrobeEquipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: int


class AnimationGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[str] | None = Field(default=None)
    tags: list[str] | None = Field(default=None)


class AnimationClipResponse(BaseModel):
    clips: list[dict] = Field(default_factory=list)
