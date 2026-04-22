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

    # Resilience: keep retrying broker connection instead of dying.
    # Previously Celery died on `UNBLOCKED force unblock from blocking
    # operation` when Redis transitioned master/replica — worker exited
    # and (without restart policy) stayed dead overnight. With these,
    # worker retries forever in the background on any connection glitch
    # and resumes consuming when Redis is back.
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=None,

    # If a task is running when connection drops, let it finish instead
    # of being cancelled mid-flight — ours are short (SERP crawls,
    # deep-dive) and don't benefit from an abort.
    worker_cancel_long_running_tasks_on_connection_loss=False,

    # Redis-specific transport hygiene.
    broker_transport_options={
        # ACKs aren't delivered for up to an hour after a restart
        # (reclaim hung tasks that a dead worker left dangling).
        "visibility_timeout": 3600,
        # TCP keepalive — detects broker disappearances before BRPOP
        # times out the hard way.
        "socket_keepalive": True,
        "socket_keepalive_options": {},
    },
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
    # Queue depth watchdog — runs every 2 minutes, writes an alert event
    # to every active site if Redis queue > STUCK_THRESHOLD. Makes worker
    # outages visible on the dashboard instead of silent "nothing happens".
    "queue-health-2min": {
        "task": "queue_health_check",
        "schedule": 120.0,  # seconds — plain float = every N sec
    },
}

celery_app.autodiscover_tasks([
    "app.collectors", "app.agents", "app.fingerprint", "app.intent",
    "app.core_audit.review", "app.core_audit.priority", "app.core_audit.report",
    "app.core_audit.demand_map", "app.core_audit.draft_profile",
    "app.core_audit.onboarding", "app.core_audit.competitors",
    "app.core_audit.outcomes", "app.core_audit.health",
])
