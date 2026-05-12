from pydantic import model_validator
from pydantic_settings import BaseSettings


_PRODUCTION_ENVS = {"production", "prod"}
_DEFAULT_SECRET_VALUES = {
    "SECRET_KEY": "dev-secret-key-change-in-production",
    "JWT_SECRET": "dev-jwt-secret-change-in-production",
}


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

    # Anthropic (primary)
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_BASE_URL: str = ""  # Cloudflare Worker proxy URL (e.g. https://anthropic-proxy.xxx.workers.dev)
    # Flipped daily to Sonnet 4.6 — latest, stronger Russian + reasoning.
    # Monthly spend jumps ~5-6× (Haiku $0.35 → Sonnet ~$2 at current
    # volume). Safely inside AI_MONTHLY_BUDGET_USD ceiling.
    AI_DAILY_MODEL: str = "claude-sonnet-4-6"
    AI_COMPLEX_MODEL: str = "claude-sonnet-4-6"
    AI_MONTHLY_BUDGET_USD: float = 10.0

    # OpenAI (fallback when Anthropic balance is exhausted).
    # NOTE: OpenAI geo-blocks requests from Russia. Set OPENAI_BASE_URL
    # to a Cloudflare/Vercel proxy hosted outside RU (mirror the
    # ANTHROPIC_BASE_URL setup) — without a proxy the fallback will
    # fail with 403 unsupported_country_region_territory.
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""

    # Provider routing. "anthropic" (default) → Claude with OpenAI as
    # auto-fallback on balance-exhaustion. "openai" → every LLM call goes
    # directly to OpenAI; Anthropic is not contacted at all. Use this to
    # switch the whole stack to gpt-5.4 (and gpt-5.4-mini for the cheap
    # tier) — useful when Claude relevance classification mis-tags too
    # many queries and a smarter model is required.
    LLM_PROVIDER: str = "anthropic"

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

    @model_validator(mode="after")
    def validate_production_config(self):
        # Fail-fast on a malformed provider switch regardless of env —
        # otherwise the typo "openAI" silently falls through the
        # llm_client router and ends up calling Anthropic in prod.
        provider = (self.LLM_PROVIDER or "anthropic").strip().lower()
        if provider not in ("anthropic", "openai"):
            raise ValueError(
                f"LLM_PROVIDER={self.LLM_PROVIDER!r} is invalid; "
                "must be 'anthropic' or 'openai'"
            )

        if (self.APP_ENV or "").strip().lower() not in _PRODUCTION_ENVS:
            return self

        # Provider-aware required set. When LLM_PROVIDER=openai we don't
        # contact Anthropic at all, so its key isn't needed — but
        # OPENAI_API_KEY then becomes mandatory (without it the first
        # LLM call would explode in openai_fallback.get_openai_client).
        llm_required = (
            "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
        )
        required = (
            "DATABASE_URL",
            "DATABASE_URL_SYNC",
            "REDIS_URL",
            llm_required,
            "ADMIN_API_KEY",
            "SECRET_KEY",
            "JWT_SECRET",
            "ENCRYPTION_KEY",
        )
        missing = [
            name for name in required
            if not str(getattr(self, name, "") or "").strip()
        ]
        weak = [
            name for name, default in _DEFAULT_SECRET_VALUES.items()
            if getattr(self, name) == default
        ]
        for name in ("ADMIN_API_KEY", "SECRET_KEY", "JWT_SECRET", "ENCRYPTION_KEY"):
            value = str(getattr(self, name, "") or "").strip().lower()
            if value.startswith("change-me"):
                weak.append(name)
        if self.ENCRYPTION_KEY and len(self.ENCRYPTION_KEY.strip()) < 32:
            weak.append("ENCRYPTION_KEY")
        if self.ADMIN_API_KEY.startswith("admin_dev_"):
            weak.append("ADMIN_API_KEY")
        if missing or weak:
            parts = []
            if missing:
                parts.append(f"missing: {', '.join(missing)}")
            if weak:
                parts.append(f"weak/default: {', '.join(sorted(set(weak)))}")
            raise ValueError(
                "Invalid production configuration; fix " + "; ".join(parts)
            )
        return self

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
