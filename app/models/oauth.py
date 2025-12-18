"""OAuth account model for Twitter integration."""

import enum
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import GUID, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class OAuthProvider(str, enum.Enum):
    """Supported OAuth providers."""

    TWITTER = "twitter"


class OAuthAccount(Base, UUIDMixin, TimestampMixin):
    """OAuth account model for external service connections."""

    __tablename__ = "oauth_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[OAuthProvider] = mapped_column(
        Enum(OAuthProvider),
        nullable=False,
        index=True,
    )
    provider_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    provider_username: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    provider_display_name: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    provider_profile_image: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Encrypted tokens
    encrypted_access_token: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    encrypted_refresh_token: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Token metadata
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    token_scope: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    error_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    # Relationship
    user: Mapped["User"] = relationship(
        "User",
        back_populates="oauth_accounts",
    )

    @property
    def is_token_expired(self) -> bool:
        """Check if the access token is expired."""
        if self.token_expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.token_expires_at

    @property
    def needs_refresh(self) -> bool:
        """Check if token should be refreshed (within 5 minutes of expiry)."""
        if self.token_expires_at is None:
            return False
        from datetime import timedelta

        buffer = timedelta(minutes=5)
        return datetime.now(timezone.utc) >= (self.token_expires_at - buffer)

    def update_last_used(self) -> None:
        """Update the last used timestamp."""
        self.last_used_at = datetime.now(timezone.utc)

    def record_error(self, error_message: str) -> None:
        """Record an error and increment error count."""
        self.last_error = error_message
        self.error_count += 1

    def clear_errors(self) -> None:
        """Clear error state after successful operation."""
        self.last_error = None
        self.error_count = 0
