from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

# Persona blob travels as one JSON string; 32 KiB caps DoS at the HTTP
# boundary while leaving headroom for the largest persona field (2000 chars)
# plus user_* fields and JSON overhead.
_PERSONA_JSON_MAX_LEN: int = 32 * 1024


class PersonaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_json: str = Field(min_length=1, max_length=_PERSONA_JSON_MAX_LEN)


class PersonaResponse(BaseModel):
    definition_json: str
    is_complete: bool


# Generation is synchronous — every persisted asset is succeeded. Pinning
# the literal keeps the contract honest if async generation ever lands.
SucceededStatus = Literal["succeeded"]


class AvatarAssetResponse(BaseModel):
    id: int
    asset_url: str
    prompt: str = ""
    status: SucceededStatus = "succeeded"


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


class CompanionModelResponse(BaseModel):
    id: int
    asset_url: str | None = None
    provider: str
    species: str = "人类"
    morph_params: dict = Field(default_factory=dict)
    status: SucceededStatus = "succeeded"
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
