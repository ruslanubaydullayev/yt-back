from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SessionUser(BaseModel):
    id: str
    name: str | None = None
    email: str | None = None
    image: str | None = None


class SessionResponse(BaseModel):
    user: SessionUser | None = None
    expires: str | None = None


class ClipImportRequest(BaseModel):
    url: str
    platform: Literal["instagram", "tiktok", "youtube"]


class ClipImportResponse(BaseModel):
    clipId: str
    platform: str
    durationSeconds: float | None = None


class ClipUploadResponse(BaseModel):
    clipId: str


class RenderItem(BaseModel):
    clipId: str
    label: str
    order: int


class RenderRequest(BaseModel):
    title: str
    items: list[RenderItem]


class RenderResponse(BaseModel):
    jobId: str


class RenderStatusResponse(BaseModel):
    status: Literal["processing", "done", "failed"]
    error: str | None = None


class RenderDownloadResponse(BaseModel):
    url: str


class UsageStatusResponse(BaseModel):
    isAuthenticated: bool
    reason: Literal["ok"] = "ok"


class UpdateProfileRequest(BaseModel):
    name: str = Field(min_length=3, max_length=32)


class UpdateProfileResponse(BaseModel):
    status: int = 200
    body: dict
