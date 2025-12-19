"""System log model for persistent application logging."""

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum, Index, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import GUID, UUIDMixin


class LogLevel(str, enum.Enum):
    """Log severity levels."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LogCategory(str, enum.Enum):
    """Log categories for filtering."""
    SYSTEM = "system"
    CELERY = "celery"
    GROWTH = "growth"
    CAMPAIGN = "campaign"
    TWEET = "tweet"
    AUTH = "auth"
    API = "api"
    DATABASE = "database"
    TWITTER = "twitter"
    DEEPSEEK = "deepseek"


class SystemLog(Base, UUIDMixin):
    """Persistent system log storage for monitoring."""

    __tablename__ = "system_logs"

    # Timestamp
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    # Log classification
    level: Mapped[LogLevel] = mapped_column(
        Enum(LogLevel),
        nullable=False,
        index=True,
    )
    category: Mapped[LogCategory] = mapped_column(
        Enum(LogCategory),
        default=LogCategory.SYSTEM,
        nullable=False,
        index=True,
    )

    # Source information
    logger_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    task_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    # Content
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    details: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )

    # Error information
    exception_type: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    exception_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    traceback: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Context
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        nullable=True,
        index=True,
    )
    strategy_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        nullable=True,
        index=True,
    )
    campaign_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        nullable=True,
        index=True,
    )
    tweet_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(),
        nullable=True,
    )

    # Request context (if from web request)
    request_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45),
        nullable=True,
    )

    # Composite indexes for common queries
    __table_args__ = (
        Index('ix_system_logs_timestamp_level', 'timestamp', 'level'),
        Index('ix_system_logs_category_timestamp', 'category', 'timestamp'),
    )


class TaskExecution(Base, UUIDMixin):
    """Track Celery task executions for monitoring."""

    __tablename__ = "task_executions"

    # Task identification
    task_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )
    task_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    # Timing
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_ms: Mapped[Optional[int]] = mapped_column(
        nullable=True,
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(50),
        default="started",
        nullable=False,
        index=True,
    )  # started, success, failure, retry

    # Arguments
    args: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )
    kwargs: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )

    # Result
    result: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Retry info
    retry_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    # Context
    worker_hostname: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    __table_args__ = (
        Index('ix_task_executions_name_started', 'task_name', 'started_at'),
    )
