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

    # Admin API (header-gated endpoints for ops/manual re-runs)
    ADMIN_API_KEY: str = ""

    # Yandex Cloud / AI Studio — Search API (successor to the legacy xml.yandex.ru).
    # A single Api-Key can cover Search, YandexGPT and other Yandex Cloud services.
    YANDEX_SEARCH_API_KEY: str = ""
    # Folder id the API key is scoped to. Required for some async operations.
    YANDEX_CLOUD_FOLDER_ID: str = "b1g5af8jsj8qhjecb4pi"

    # Demand Map — Phase B feature flag.
    # When False, Celery task runs Cartesian only (no Suggest / LLM).
    # Default True so Phase B pipeline is exercised in dev/tests.
    USE_DEMAND_MAP_ENRICHMENT: bool = True

    # Target Demand Map — Phase D feature flag.
    # When True, Decisioner + PriorityService route through the
    # target_clusters coverage path (Phase C) instead of the legacy
    # IntentCode enum path. Default False — parity-safe until we flip
    # it in a later phase.
    USE_TARGET_DEMAND_MAP: bool = False

    # BusinessTruth discovery — Week 2 shadow-mode flag.
    # False: old _pick_top_queries is the source of truth for discovery,
    #        query_picker_v2 runs alongside and only logs diff into
    #        analysis_events.extra for comparison.
    # True:  query_picker_v2 drives discovery queries.
    # Keep False until shadow data confirms new picker produces at
    # least parity coverage + reduces the Abkhazia skew.
    USE_BUSINESS_TRUTH_DISCOVERY: bool = False

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
