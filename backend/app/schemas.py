from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

Platform = Literal['telegram', 'whatsapp', 'facebook']
Permission = Literal['unknown', 'allowed', 'forbidden']


class CategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    color: str = Field(default='#6366f1', pattern=r'^#[a-fA-F0-9]{6}$')
    emoji: str = Field(default='🎯', min_length=1, max_length=8)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    excluded_keywords: list[str] = Field(default_factory=list, max_length=20)
    platforms: list[Platform] = Field(default_factory=lambda: ['telegram', 'whatsapp', 'facebook'], min_length=1)
    search_enabled: bool = False
    frequency_hours: int = Field(default=168, ge=24, le=720)

    @field_validator('keywords', 'excluded_keywords')
    @classmethod
    def clean_words(cls, items: list[str]) -> list[str]:
        return list(dict.fromkeys(x.strip() for x in items if x.strip() and len(x.strip()) <= 60))


class CategoryOut(CategoryIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    last_searched_at: datetime | None
    created_at: datetime


class LinkImport(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)
    category_id: int | None = None


class LinkPatch(BaseModel):
    category_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    permission: Permission | None = None
    notes: str | None = Field(default=None, max_length=3000)
    favorite: bool | None = None
    archived: bool | None = None


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    category_id: int | None
    platform: Platform
    url: str
    name: str
    image_url: str | None
    members: int | None
    description: str
    source: str
    status: str
    permission: Permission
    favorite: bool
    archived: bool
    notes: str
    last_checked_at: datetime | None
    last_posted_at: datetime | None
    created_at: datetime


class CaptionIn(BaseModel):
    category_id: int | None = None
    name: str = Field(min_length=2, max_length=100)
    text: str = Field(min_length=3, max_length=5000)
    offer_url: str = Field(default='', max_length=1000)


class CaptionOut(CaptionIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime


class GenerateIn(BaseModel):
    category_id: int | None = None
    product: str = Field(min_length=5, max_length=1200)
    audience: str = Field(min_length=2, max_length=300)
    offer_url: str = Field(default='', max_length=1000)
    tone: Literal['direto', 'amigável', 'profissional'] = 'amigável'
    variants: int = Field(default=3, ge=1, le=5)


class PostIn(BaseModel):
    link_id: int
    leads: int = Field(default=0, ge=0, le=1000000)
    sales: int = Field(default=0, ge=0, le=1000000)
    notes: str = Field(default='', max_length=1000)
