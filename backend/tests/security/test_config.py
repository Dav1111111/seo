import pytest
from pydantic import ValidationError

from app.config import Settings


def _prod_settings(**overrides):
    values = {
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+asyncpg://tower:secret@db:5432/growthtower",
        "DATABASE_URL_SYNC": "postgresql+psycopg://tower:secret@db:5432/growthtower",
        "REDIS_URL": "redis://redis:6379/0",
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "ADMIN_API_KEY": "prod-admin-key-with-more-than-32-chars",
        "SECRET_KEY": "prod-secret-key-with-more-than-32-chars",
        "JWT_SECRET": "prod-jwt-secret-with-more-than-32-chars",
        "ENCRYPTION_KEY": "prod-encryption-key-with-more-than-32-chars",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_settings_accept_strong_required_values():
    settings = _prod_settings()

    assert settings.APP_ENV == "production"


def test_production_settings_reject_missing_admin_key():
    with pytest.raises(ValidationError):
        _prod_settings(ADMIN_API_KEY="")


def test_production_settings_reject_default_secret_key():
    with pytest.raises(ValidationError):
        _prod_settings(SECRET_KEY="dev-secret-key-change-in-production")


def test_production_settings_reject_placeholder_secret_key():
    with pytest.raises(ValidationError):
        _prod_settings(SECRET_KEY="change-me-min-32-chars-secret-key")


def test_development_settings_allow_empty_optional_secrets():
    settings = Settings(_env_file=None, APP_ENV="development")

    assert settings.APP_ENV == "development"
