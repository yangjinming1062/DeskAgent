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
    pronouns: str | None = Field(default=None, max_length=64)
    background: str | None = Field(default=None, max_length=500)
    boundaries: str | None = Field(default=None, max_length=500)


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


class AvatarHistoryResponse(BaseModel):
    items: list[AvatarAssetResponse]


class ClipStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scene: str
    batch: int
    status: str
    url: str | None = None
