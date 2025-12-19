"""System logging service for persistent log storage and retrieval."""

import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_log import LogCategory, LogLevel, SystemLog, TaskExecution


class SystemLoggingService:
    """Service for managing system logs."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def log(
        self,
        level: LogLevel,
        message: str,
        category: LogCategory = LogCategory.SYSTEM,
        logger_name: str = "app",
        task_name: Optional[str] = None,
        task_id: Optional[str] = None,
        details: Optional[dict] = None,
        exception: Optional[Exception] = None,
        user_id: Optional[UUID] = None,
        strategy_id: Optional[UUID] = None,
        campaign_id: Optional[UUID] = None,
        tweet_id: Optional[UUID] = None,
        request_id: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> SystemLog:
        """Create a system log entry."""
        log_entry = SystemLog(
            timestamp=datetime.now(timezone.utc),
            level=level,
            category=category,
            logger_name=logger_name,
            task_name=task_name,
            task_id=task_id,
            message=message,
            details=details,
            user_id=user_id,
            strategy_id=strategy_id,
            campaign_id=campaign_id,
            tweet_id=tweet_id,
            request_id=request_id,
            ip_address=ip_address,
        )

        if exception:
            log_entry.exception_type = type(exception).__name__
            log_entry.exception_message = str(exception)
            log_entry.traceback = traceback.format_exc()

        self.db.add(log_entry)
        await self.db.flush()
        return log_entry

    async def log_info(self, message: str, **kwargs) -> SystemLog:
        """Log an info message."""
        return await self.log(LogLevel.INFO, message, **kwargs)

    async def log_warning(self, message: str, **kwargs) -> SystemLog:
        """Log a warning message."""
        return await self.log(LogLevel.WARNING, message, **kwargs)

    async def log_error(self, message: str, **kwargs) -> SystemLog:
        """Log an error message."""
        return await self.log(LogLevel.ERROR, message, **kwargs)

    async def log_debug(self, message: str, **kwargs) -> SystemLog:
        """Log a debug message."""
        return await self.log(LogLevel.DEBUG, message, **kwargs)

    async def get_logs(
        self,
        level: Optional[LogLevel] = None,
        category: Optional[LogCategory] = None,
        logger_name: Optional[str] = None,
        task_name: Optional[str] = None,
        user_id: Optional[UUID] = None,
        strategy_id: Optional[UUID] = None,
        campaign_id: Optional[UUID] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SystemLog]:
        """Get system logs with filtering."""
        query = select(SystemLog)

        if level:
            query = query.where(SystemLog.level == level)
        if category:
            query = query.where(SystemLog.category == category)
        if logger_name:
            query = query.where(SystemLog.logger_name.ilike(f"%{logger_name}%"))
        if task_name:
            query = query.where(SystemLog.task_name == task_name)
        if user_id:
            query = query.where(SystemLog.user_id == user_id)
        if strategy_id:
            query = query.where(SystemLog.strategy_id == strategy_id)
        if campaign_id:
            query = query.where(SystemLog.campaign_id == campaign_id)
        if since:
            query = query.where(SystemLog.timestamp >= since)
        if until:
            query = query.where(SystemLog.timestamp <= until)
        if search:
            query = query.where(SystemLog.message.ilike(f"%{search}%"))

        query = query.order_by(SystemLog.timestamp.desc()).limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_logs_count(
        self,
        level: Optional[LogLevel] = None,
        category: Optional[LogCategory] = None,
        since: Optional[datetime] = None,
    ) -> int:
        """Get count of logs matching criteria."""
        query = select(func.count()).select_from(SystemLog)

        if level:
            query = query.where(SystemLog.level == level)
        if category:
            query = query.where(SystemLog.category == category)
        if since:
            query = query.where(SystemLog.timestamp >= since)

        result = await self.db.execute(query)
        return result.scalar() or 0

    async def get_log_stats(self, hours: int = 24) -> dict:
        """Get log statistics for the past N hours."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        # Count by level
        level_counts = {}
        for level in LogLevel:
            count = await self.get_logs_count(level=level, since=since)
            level_counts[level.value] = count

        # Count by category
        category_counts = {}
        for category in LogCategory:
            count = await self.get_logs_count(category=category, since=since)
            category_counts[category.value] = count

        # Recent errors
        errors = await self.get_logs(level=LogLevel.ERROR, since=since, limit=10)

        return {
            "period_hours": hours,
            "by_level": level_counts,
            "by_category": category_counts,
            "total": sum(level_counts.values()),
            "recent_errors": errors,
        }

    async def cleanup_old_logs(self, days_to_keep: int = 7) -> int:
        """Delete logs older than specified days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep)

        stmt = delete(SystemLog).where(SystemLog.timestamp < cutoff)
        result = await self.db.execute(stmt)

        return result.rowcount


class TaskExecutionService:
    """Service for tracking Celery task executions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_task_start(
        self,
        task_id: str,
        task_name: str,
        args: Optional[list] = None,
        kwargs: Optional[dict] = None,
        worker_hostname: Optional[str] = None,
    ) -> TaskExecution:
        """Record the start of a task execution."""
        execution = TaskExecution(
            task_id=task_id,
            task_name=task_name,
            started_at=datetime.now(timezone.utc),
            status="started",
            args=args,
            kwargs=kwargs,
            worker_hostname=worker_hostname,
        )
        self.db.add(execution)
        await self.db.flush()
        return execution

    async def record_task_success(
        self,
        task_id: str,
        result: Optional[dict] = None,
    ) -> Optional[TaskExecution]:
        """Record successful task completion."""
        stmt = select(TaskExecution).where(TaskExecution.task_id == task_id)
        execution_result = await self.db.execute(stmt)
        execution = execution_result.scalar_one_or_none()

        if execution:
            now = datetime.now(timezone.utc)
            execution.completed_at = now
            execution.status = "success"
            execution.result = result
            if execution.started_at:
                execution.duration_ms = int(
                    (now - execution.started_at).total_seconds() * 1000
                )
            await self.db.flush()

        return execution

    async def record_task_failure(
        self,
        task_id: str,
        error_message: str,
    ) -> Optional[TaskExecution]:
        """Record task failure."""
        stmt = select(TaskExecution).where(TaskExecution.task_id == task_id)
        execution_result = await self.db.execute(stmt)
        execution = execution_result.scalar_one_or_none()

        if execution:
            now = datetime.now(timezone.utc)
            execution.completed_at = now
            execution.status = "failure"
            execution.error_message = error_message
            if execution.started_at:
                execution.duration_ms = int(
                    (now - execution.started_at).total_seconds() * 1000
                )
            await self.db.flush()

        return execution

    async def record_task_retry(
        self,
        task_id: str,
    ) -> Optional[TaskExecution]:
        """Record task retry."""
        stmt = select(TaskExecution).where(TaskExecution.task_id == task_id)
        execution_result = await self.db.execute(stmt)
        execution = execution_result.scalar_one_or_none()

        if execution:
            execution.status = "retry"
            execution.retry_count += 1
            await self.db.flush()

        return execution

    async def get_recent_executions(
        self,
        task_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[TaskExecution]:
        """Get recent task executions."""
        query = select(TaskExecution)

        if task_name:
            query = query.where(TaskExecution.task_name == task_name)
        if status:
            query = query.where(TaskExecution.status == status)

        query = query.order_by(TaskExecution.started_at.desc()).limit(limit)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_task_stats(self, hours: int = 24) -> dict:
        """Get task execution statistics."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        # Total count
        total_stmt = select(func.count()).select_from(TaskExecution).where(
            TaskExecution.started_at >= since
        )
        total_result = await self.db.execute(total_stmt)
        total = total_result.scalar() or 0

        # Count by status
        status_counts = {}
        for status in ["started", "success", "failure", "retry"]:
            count_stmt = (
                select(func.count())
                .select_from(TaskExecution)
                .where(
                    TaskExecution.started_at >= since,
                    TaskExecution.status == status,
                )
            )
            count_result = await self.db.execute(count_stmt)
            status_counts[status] = count_result.scalar() or 0

        # Average duration for successful tasks
        avg_duration_stmt = (
            select(func.avg(TaskExecution.duration_ms))
            .where(
                TaskExecution.started_at >= since,
                TaskExecution.status == "success",
                TaskExecution.duration_ms.isnot(None),
            )
        )
        avg_result = await self.db.execute(avg_duration_stmt)
        avg_duration = avg_result.scalar()

        # Count by task name
        task_counts_stmt = (
            select(TaskExecution.task_name, func.count())
            .where(TaskExecution.started_at >= since)
            .group_by(TaskExecution.task_name)
            .order_by(func.count().desc())
            .limit(10)
        )
        task_counts_result = await self.db.execute(task_counts_stmt)
        task_counts = {row[0]: row[1] for row in task_counts_result.fetchall()}

        # Recent failures
        failures = await self.get_recent_executions(status="failure", limit=10)

        return {
            "period_hours": hours,
            "total": total,
            "by_status": status_counts,
            "by_task": task_counts,
            "avg_duration_ms": round(avg_duration, 2) if avg_duration else None,
            "success_rate": round(status_counts["success"] / total * 100, 1) if total > 0 else 0,
            "recent_failures": failures,
        }

    async def cleanup_old_executions(self, days_to_keep: int = 7) -> int:
        """Delete old task executions."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep)

        stmt = delete(TaskExecution).where(TaskExecution.started_at < cutoff)
        result = await self.db.execute(stmt)

        return result.rowcount
