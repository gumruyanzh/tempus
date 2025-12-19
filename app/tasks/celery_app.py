"""Celery application configuration."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "tempus",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes
    task_soft_time_limit=240,  # 4 minutes
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Rate limiting
    task_default_rate_limit="10/m",
    # Retry settings
    task_default_retry_delay=60,
    task_max_retries=3,
)

# Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    "process-pending-tweets": {
        "task": "app.tasks.tweet_tasks.process_pending_tweets",
        "schedule": crontab(minute="*"),  # Every minute
    },
    "process-campaign-tweets": {
        "task": "app.tasks.campaign_tasks.process_campaign_tweets",
        "schedule": crontab(minute="*"),  # Every minute
    },
    "check-completed-campaigns": {
        "task": "app.tasks.campaign_tasks.check_completed_campaigns",
        "schedule": crontab(minute="*/5"),  # Every 5 minutes
    },
    "cleanup-old-logs": {
        "task": "app.tasks.maintenance_tasks.cleanup_old_execution_logs",
        "schedule": crontab(hour=3, minute=0),  # Daily at 3 AM UTC
    },
    # Growth strategy tasks
    "process-growth-strategies": {
        "task": "app.tasks.growth_tasks.process_growth_strategies",
        "schedule": crontab(minute="*/5"),  # Every 5 minutes
    },
    "update-all-strategy-metrics": {
        "task": "app.tasks.growth_tasks.update_all_strategy_metrics",
        "schedule": crontab(hour=0, minute=0),  # Daily at midnight UTC
    },
    "cleanup-rate-limit-trackers": {
        "task": "app.tasks.growth_tasks.cleanup_rate_limit_trackers",
        "schedule": crontab(hour=1, minute=0),  # Daily at 1 AM UTC
    },
}

# Autodiscover tasks
celery_app.autodiscover_tasks(["app.tasks"])
