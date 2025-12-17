"""Celery worker entry point."""

from app.tasks.celery_app import celery_app

# Import tasks to register them
from app.tasks import maintenance_tasks, tweet_tasks  # noqa: F401

if __name__ == "__main__":
    celery_app.start()
