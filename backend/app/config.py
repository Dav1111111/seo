from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://tower:devpassword@db:5432/growthtower"
    DATABASE_URL_SYNC: str = "postgresql://tower:devpassword@db:5432/growthtower"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # Yandex
    YANDEX_OAUTH_TOKEN: str = ""
    YANDEX_OAUTH_CLIENT_ID: str = ""
    YANDEX_OAUTH_CLIENT_SECRET: str = ""
    YANDEX_WEBMASTER_USER_ID: str = ""
    YANDEX_WEBMASTER_HOST_ID: str = ""
    YANDEX_METRICA_COUNTER_ID: str = ""

    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_BASE_URL: str = ""  # Cloudflare Worker proxy URL (e.g. https://anthropic-proxy.xxx.workers.dev)
    AI_DAILY_MODEL: str = "claude-haiku-4-5-20251001"
    AI_COMPLEX_MODEL: str = "claude-sonnet-4-6"
    AI_MONTHLY_BUDGET_USD: float = 10.0

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_DEFAULT_CHAT_ID: str = ""

    # Security
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ENCRYPTION_KEY: str = ""
    JWT_SECRET: str = "dev-jwt-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 24

    # App
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    TIMEZONE: str = "Europe/Moscow"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
