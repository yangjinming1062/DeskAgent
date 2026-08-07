from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class PersonaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The full persona definition (including user_* fields routed to Memory)
    # travels as one JSON blob; the backend parses + validates it.
    definition_json: str = Field(min_length=1)


class PersonaResponse(BaseModel):
    definition_json: str
    is_complete: bool


class AvatarAssetResponse(BaseModel):
    id: int
    asset_url: str
    # Generation completes synchronously, so every returned asset is succeeded.
    prompt: str = ""
    status: str = "succeeded"


class AvatarGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional verbatim prompt override; omitted → prompt built from persona.
    prompt_override: str | None = Field(default=None, max_length=2000)


class AvatarUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 8 MiB of base64 caps the decoded image well below disk-write size.
    image: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)


class AvatarFromImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Same 8 MiB cap as upload: the image is both a provider seed (via a
    # signed URL) and the source of truth the provider re-renders from.
    image: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)


class AvatarHistoryResponse(BaseModel):
    history: list[AvatarAssetResponse]


class ClipStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scene: str
    batch: int
    status: str
    url: str | None = None
    tier: int = 1
    keyframe_url: str | None = None
    keyframe_meta: dict | None = None


class CompanionModelResponse(BaseModel):
    id: int
    asset_url: str | None = None
    provider: str
    species: str = "人类"
    morph_params: dict = Field(default_factory=dict)
    status: str = "succeeded"
    has_rig: bool
    has_morph_targets: bool


class ModelGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional species override; omitted → species derived from the persona.
    species_override: str | None = Field(default=None, max_length=64)


class WardrobeItemResponse(BaseModel):
    id: int
    name: str
    category: str
    material_overrides_json: str = "{}"
    texture_url: str | None = None
    prompt: str | None = None
    equipped: bool


class WardrobeGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)


class WardrobeEquipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: int
