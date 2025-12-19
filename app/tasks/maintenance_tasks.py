"""Maintenance tasks for cleanup and housekeeping."""

import asyncio
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import delete

from app.core.database import get_celery_db_context
from app.core.logging import get_logger
from app.models.tweet import TweetExecutionLog

logger = get_logger(__name__)


def run_async(coro):
    """Run async function in sync context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@shared_task
def cleanup_old_execution_logs(days_to_keep: int = 30) -> dict:
    """Clean up old execution logs."""
    return run_async(_cleanup_old_execution_logs_async(days_to_keep))


async def _cleanup_old_execution_logs_async(days_to_keep: int) -> dict:
    """Async implementation of execution log cleanup."""
    async with get_celery_db_context() as db:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_to_keep)

        stmt = delete(TweetExecutionLog).where(
            TweetExecutionLog.created_at < cutoff_date
        )
        result = await db.execute(stmt)
        await db.commit()

        deleted_count = result.rowcount

        logger.info(
            "Cleaned up old execution logs",
            deleted_count=deleted_count,
            days_to_keep=days_to_keep,
        )

        return {"deleted": deleted_count}


@shared_task
def health_check() -> dict:
    """Health check task for monitoring."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
