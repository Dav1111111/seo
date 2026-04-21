from celery import Celery
from celery.schedules import crontab
from app.config import settings

celery_app = Celery(
    "growth_tower",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    worker_max_tasks_per_child=50,
)

# Pipeline: collect → analyse (times in UTC; MSK = UTC+3)
celery_app.conf.beat_schedule = {
    # Phase 2: Data collection
    "collect-webmaster-daily": {
        "task": "collect_webmaster_all",
        "schedule": crontab(hour=4, minute=0),   # 07:00 MSK
    },
    "collect-metrica-daily": {
        "task": "collect_metrica_all",
        "schedule": crontab(hour=4, minute=30),  # 07:30 MSK
    },
    # Phase 3: AI Analysis (runs after collection)
    "analyse-search-visibility-daily": {
        "task": "run_search_visibility_all",
        "schedule": crontab(hour=5, minute=0),   # 08:00 MSK
    },
    "analyse-technical-indexing-daily": {
        "task": "run_technical_indexing_all",
        "schedule": crontab(hour=5, minute=15),  # 08:15 MSK
    },
    # Fingerprinting — daily 03:00 UTC (06:00 MSK, before data collection)
    "fingerprint-all-daily": {
        "task": "fingerprint_all_sites",
        "schedule": crontab(hour=3, minute=0),
    },
    # Fingerprint GC — weekly Sunday 03:30 UTC
    "fingerprint-gc-weekly": {
        "task": "fingerprint_gc_stale",
        "schedule": crontab(hour=3, minute=30, day_of_week=0),
    },
    # Intent classification — daily 04:20 UTC
    # (runs after collect_webmaster_all 04:00 to have fresh query data)
    "intent-classify-all-daily": {
        "task": "intent_classify_all",
        "schedule": crontab(hour=4, minute=20),
    },
    # Query clustering (weekly, Monday 09:00 MSK)
    "cluster-queries-weekly": {
        "task": "run_query_clustering_all",
        "schedule": crontab(hour=6, minute=0, day_of_week=1),
    },
    # Query recommendations — tactical daily (08:30 MSK)
    "recommend-queries-tactical-daily": {
        "task": "run_query_tactical_all",
        "schedule": crontab(hour=5, minute=30),
    },
    # Query recommendations — strategic weekly (Monday 09:30 MSK)
    "recommend-queries-strategic-weekly": {
        "task": "run_query_strategic_all",
        "schedule": crontab(hour=6, minute=30, day_of_week=1),
    },
    # Module 3 — Page Review (runs 45min after intent_decide to let decisions settle)
    "review-sites-nightly": {
        "task": "review_all_nightly",
        "schedule": crontab(hour=4, minute=45),
    },
    # Module 5 — Weekly SEO Report (Mondays 07:00 UTC / 10:00 MSK)
    "reports-weekly-monday": {
        "task": "report_build_all_weekly",
        "schedule": crontab(hour=7, minute=0, day_of_week=1),
    },
    # Target Demand Map — weekly build (Mondays 03:30 UTC, before intent pipeline)
    "demand-map-build-weekly": {
        "task": "demand_map_build_all_weekly",
        "schedule": crontab(hour=3, minute=30, day_of_week=1),
    },
    # Competitor discovery — weekly (Tuesdays 04:00 UTC / 07:00 MSK)
    # Refreshes competitor list + auto-chains deep-dive → opportunities
    "competitors-discover-weekly": {
        "task": "competitors_discover_all_weekly",
        "schedule": crontab(hour=4, minute=0, day_of_week=2),
    },
    # Site re-crawl — monthly (1st of each month, 02:00 UTC / 05:00 MSK)
    # Refreshes page index, title/h1/content used for page-match scoring
    "crawl-all-sites-monthly": {
        "task": "crawl_all_sites_monthly",
        "schedule": crontab(hour=2, minute=0, day_of_month=1),
    },
    # Outcome follow-up — daily (08:00 UTC / 11:00 MSK)
    # Fills delta for snapshots that matured (applied ≥14 days ago)
    "outcomes-followup-daily": {
        "task": "outcomes_followup_daily",
        "schedule": crontab(hour=8, minute=0),
    },
}

celery_app.autodiscover_tasks([
    "app.collectors", "app.agents", "app.fingerprint", "app.intent",
    "app.core_audit.review", "app.core_audit.priority", "app.core_audit.report",
    "app.core_audit.demand_map", "app.core_audit.draft_profile",
    "app.core_audit.onboarding", "app.core_audit.competitors",
    "app.core_audit.outcomes",
])
