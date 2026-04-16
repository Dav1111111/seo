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
    # Query clustering (weekly, Monday 09:00 MSK)
    "cluster-queries-weekly": {
        "task": "run_query_clustering_all",
        "schedule": crontab(hour=6, minute=0, day_of_week=1),
    },
}

celery_app.autodiscover_tasks(["app.collectors", "app.agents"])
