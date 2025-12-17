"""Audit logging service."""

from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.audit import AuditAction, AuditLog

logger = get_logger(__name__)


class AuditService:
    """Service for audit logging operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def log(
        self,
        action: AuditAction,
        user_id: Optional[UUID] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        old_value: Optional[dict[str, Any]] = None,
        new_value: Optional[dict[str, Any]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> AuditLog:
        """Create an audit log entry."""
        audit_log = AuditLog(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            details=details,
            old_value=old_value,
            new_value=new_value,
            success=success,
            error_message=error_message,
        )

        self.db.add(audit_log)
        await self.db.flush()

        # Also log to structured logger
        logger.info(
            "Audit event",
            action=action.value,
            user_id=str(user_id) if user_id else None,
            resource_type=resource_type,
            resource_id=resource_id,
            success=success,
        )

        return audit_log

    async def log_login(
        self,
        user_id: UUID,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> AuditLog:
        """Log a login attempt."""
        action = AuditAction.USER_LOGIN if success else AuditAction.USER_LOGIN_FAILED
        return await self.log(
            action=action,
            user_id=user_id,
            resource_type="user",
            resource_id=str(user_id),
            ip_address=ip_address,
            user_agent=user_agent,
            success=success,
            error_message=error_message,
        )

    async def log_registration(
        self,
        user_id: UUID,
        email: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuditLog:
        """Log a user registration."""
        return await self.log(
            action=AuditAction.USER_REGISTERED,
            user_id=user_id,
            resource_type="user",
            resource_id=str(user_id),
            ip_address=ip_address,
            user_agent=user_agent,
            details={"email": email},
        )

    async def log_logout(
        self,
        user_id: UUID,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuditLog:
        """Log a user logout."""
        return await self.log(
            action=AuditAction.USER_LOGOUT,
            user_id=user_id,
            resource_type="user",
            resource_id=str(user_id),
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_twitter_connected(
        self,
        user_id: UUID,
        twitter_username: str,
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        """Log Twitter account connection."""
        return await self.log(
            action=AuditAction.TWITTER_CONNECTED,
            user_id=user_id,
            resource_type="oauth_account",
            details={"twitter_username": twitter_username},
            ip_address=ip_address,
        )

    async def log_twitter_disconnected(
        self,
        user_id: UUID,
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        """Log Twitter account disconnection."""
        return await self.log(
            action=AuditAction.TWITTER_DISCONNECTED,
            user_id=user_id,
            resource_type="oauth_account",
            ip_address=ip_address,
        )

    async def log_api_key_created(
        self,
        user_id: UUID,
        key_type: str,
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        """Log API key creation."""
        return await self.log(
            action=AuditAction.API_KEY_CREATED,
            user_id=user_id,
            resource_type="api_key",
            details={"key_type": key_type},
            ip_address=ip_address,
        )

    async def log_api_key_rotated(
        self,
        user_id: UUID,
        key_type: str,
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        """Log API key rotation."""
        return await self.log(
            action=AuditAction.API_KEY_ROTATED,
            user_id=user_id,
            resource_type="api_key",
            details={"key_type": key_type},
            ip_address=ip_address,
        )

    async def log_tweet_scheduled(
        self,
        user_id: UUID,
        tweet_id: UUID,
        scheduled_for: str,
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        """Log tweet scheduling."""
        return await self.log(
            action=AuditAction.TWEET_SCHEDULED,
            user_id=user_id,
            resource_type="scheduled_tweet",
            resource_id=str(tweet_id),
            details={"scheduled_for": scheduled_for},
            ip_address=ip_address,
        )

    async def log_tweet_posted(
        self,
        user_id: UUID,
        tweet_id: UUID,
        twitter_tweet_id: str,
    ) -> AuditLog:
        """Log successful tweet posting."""
        return await self.log(
            action=AuditAction.TWEET_POSTED,
            user_id=user_id,
            resource_type="scheduled_tweet",
            resource_id=str(tweet_id),
            details={"twitter_tweet_id": twitter_tweet_id},
        )

    async def log_tweet_failed(
        self,
        user_id: UUID,
        tweet_id: UUID,
        error_message: str,
    ) -> AuditLog:
        """Log failed tweet posting."""
        return await self.log(
            action=AuditAction.TWEET_FAILED,
            user_id=user_id,
            resource_type="scheduled_tweet",
            resource_id=str(tweet_id),
            success=False,
            error_message=error_message,
        )

    async def log_tweet_cancelled(
        self,
        user_id: UUID,
        tweet_id: UUID,
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        """Log tweet cancellation."""
        return await self.log(
            action=AuditAction.TWEET_CANCELLED,
            user_id=user_id,
            resource_type="scheduled_tweet",
            resource_id=str(tweet_id),
            ip_address=ip_address,
        )

    async def log_settings_updated(
        self,
        user_id: UUID,
        changes: dict[str, Any],
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        """Log settings update."""
        return await self.log(
            action=AuditAction.SETTINGS_UPDATED,
            user_id=user_id,
            resource_type="user",
            resource_id=str(user_id),
            details={"changes": list(changes.keys())},
            ip_address=ip_address,
        )
