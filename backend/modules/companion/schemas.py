from datetime import datetime

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class PersonaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    personality: str = Field(min_length=1, max_length=500)
    speaking_style: str = Field(min_length=1, max_length=500)
    appearance: str | None = Field(default=None, max_length=500)
    background: str | None = Field(default=None, max_length=500)
    biological_type: str | None = Field(default=None, max_length=64)
    gender: str | None = Field(default=None, max_length=64)
    user_call_name: str | None = Field(default=None, max_length=2000)
    user_gender: str | None = Field(default=None, max_length=2000)
    user_age_bucket: str | None = Field(default=None, max_length=2000)
    user_hobbies: str | None = Field(default=None, max_length=2000)
    user_freeform: str | None = Field(default=None, max_length=2000)


class PersonaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    definition_json: str
    system_prompt_extras: str
    is_complete: bool
    created_at: datetime
    updated_at: datetime


class AvatarAssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    asset_url: str
    style: str
    seed: int | None
    active: bool
    created_at: datetime


class AvatarGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str = Field(default="portrait", min_length=1, max_length=64)


class AvatarUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 8 MiB of base64 caps the decoded image well below disk-write size.
    image: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    content_type: str | None = Field(default=None, max_length=64)


class AvatarHistoryResponse(BaseModel):
    items: list[AvatarAssetResponse]


class ClipStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scene: str
    batch: int
    status: str
    url: str | None = None
    tier: int = 1
    keyframe_url: str | None = None
    keyframe_meta: dict | None = None
