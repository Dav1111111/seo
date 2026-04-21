import uuid
import re
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SiteCreate(BaseModel):
    domain: str = Field(..., min_length=3, max_length=255)
    display_name: str | None = Field(None, max_length=255)
    yandex_webmaster_host_id: str | None = None
    yandex_metrica_counter_id: str | None = None
    yandex_oauth_token: str | None = None

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        v = v.strip().lower()
        v = v.removeprefix("https://").removeprefix("http://").rstrip("/")
        if not re.match(r"^[a-z0-9\-\.а-яё]+\.[a-z0-9а-яё\-]{2,}$", v):
            raise ValueError("Некорректный домен")
        return v


class SiteUpdate(BaseModel):
    display_name: str | None = Field(None, max_length=255)
    operating_mode: str | None = Field(None, pattern="^(readonly|recommend)$")
    yandex_webmaster_host_id: str | None = None
    yandex_metrica_counter_id: str | None = None
    yandex_oauth_token: str | None = None
    is_active: bool | None = None


class SiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    domain: str
    display_name: str | None
    operating_mode: str
    is_active: bool
    yandex_webmaster_host_id: str | None
    yandex_metrica_counter_id: str | None
    onboarding_step: str | None = None
    competitor_domains: list[str] | None = None
    created_at: datetime | None
    updated_at: datetime | None
    # yandex_oauth_token excluded intentionally — never expose tokens in API
