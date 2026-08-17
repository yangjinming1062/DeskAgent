from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # Free-text nudge from the portrait-phase textarea ("头发再短一点"). Empty
    # / whitespace is treated as no nudge by build_fullbody_prompt.
    feedback: str | None = Field(default=None, max_length=2000)
    reference_image: str | None = Field(default=None, max_length=8 * 1024 * 1024)
    reference_content_type: str | None = Field(default=None, max_length=64)

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
    # Optional presentation/style reference alongside the identity anchor.
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
    morph_params: dict = Field(default_factory=dict)
    status: str = "succeeded"
    has_rig: bool
    has_morph_targets: bool
    content_hash: str | None = None


class ModelGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species_override: str | None = Field(default=None, max_length=64)
    provider: Literal["tripo", "blender_llm"] | None = None
    # False returns existing active model idempotently; True forces paid regeneration.
    force: bool = False


class SpriteResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=500)
    # "waiting" = the first-priority waiting/switch sprite (one per user, album-match bypassed).
    role: Literal["waiting"] | None = None
    force_new: bool = False


class SpriteImageResponse(BaseModel):
    id: int
    url: str
    tag: str
    content_hash: str | None = None
    generated: bool


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
    """Polled job status; the preview fields only carry values once
    ``status == "succeeded``" (hence every inherited field re-declared
    optional)."""

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
