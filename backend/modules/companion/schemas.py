from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

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
    personality_tags: list[str] = Field(default_factory=list)


# Generation is synchronous — every persisted asset is succeeded. Pinning
# the literal keeps the contract honest if async generation ever lands.
SucceededStatus = Literal["succeeded"]


class AvatarAssetResponse(BaseModel):
    id: int
    asset_url: str
    # Step-1 (avatar-only) rows leave seed URLs empty until the user confirms
    # the face and triggers ``POST /avatar/{id}/fullbody``.
    seed_front_url: str | None = ""
    seed_right_url: str | None = ""
    seed_back_url: str | None = ""
    prompt: str = ""
    status: SucceededStatus = "succeeded"


class FullbodyGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: Literal["front", "right", "back"] | None = None
    stage: Literal["front", "aux"] | None = None

    @model_validator(mode="after")
    def _check_exclusive(self):
        if bool(self.stage) == bool(self.view):
            raise ValueError("exactly one of 'stage' or 'view' is required")
        return self


class AvatarGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    rig_type: str = "biped"
    rig_naming: str = "mixamo"
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
    # PBR channels paired with ``texture_url`` (albedo). Legacy rows and
    # colour-preset rows carry ``None`` for all three.
    normal_url: str | None = None
    roughness_url: str | None = None
    metalness_url: str | None = None
    prompt: str | None = None
    equipped: bool


class WardrobeGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)


class WardrobeEquipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: int


class AnimationGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[str] | None = Field(default=None)
    tags: list[str] | None = Field(default=None)


class AnimationClipResponse(BaseModel):
    clips: list[dict] = Field(default_factory=list)
