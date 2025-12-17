"""Twitter (X) API integration service."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import decrypt_value, encrypt_value, generate_state_token
from app.models.oauth import OAuthAccount, OAuthProvider

logger = get_logger(__name__)


class TwitterAPIError(Exception):
    """Twitter API error."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class TwitterRateLimitError(TwitterAPIError):
    """Rate limit exceeded error."""

    def __init__(self, retry_after: Optional[int] = None) -> None:
        super().__init__("Rate limit exceeded", status_code=429)
        self.retry_after = retry_after


class TwitterService:
    """Service for Twitter API operations."""

    TWITTER_AUTH_URL = "https://twitter.com/i/oauth2/authorize"
    TWITTER_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
    TWITTER_API_BASE = "https://api.twitter.com/2"
    TWITTER_SCOPES = ["tweet.read", "tweet.write", "users.read", "offline.access"]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._client: Optional[httpx.AsyncClient] = None

    async def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def get_authorization_url(self, state: Optional[str] = None) -> tuple[str, str]:
        """Generate Twitter OAuth 2.0 authorization URL."""
        if state is None:
            state = generate_state_token()

        # PKCE code verifier and challenge
        import base64
        import hashlib
        import secrets

        code_verifier = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip("=")

        params = {
            "response_type": "code",
            "client_id": settings.twitter_client_id,
            "redirect_uri": settings.twitter_redirect_uri,
            "scope": " ".join(self.TWITTER_SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        auth_url = f"{self.TWITTER_AUTH_URL}?{urlencode(params)}"
        # Return both state and code_verifier for session storage
        return auth_url, f"{state}:{code_verifier}"

    async def exchange_code_for_tokens(
        self,
        code: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        """Exchange authorization code for access tokens."""
        client = await self.get_client()

        import base64

        # Basic auth header
        credentials = f"{settings.twitter_client_id}:{settings.twitter_client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}",
        }

        data = {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.twitter_redirect_uri,
            "code_verifier": code_verifier,
        }

        response = await client.post(
            self.TWITTER_TOKEN_URL,
            headers=headers,
            data=data,
        )

        if response.status_code != 200:
            logger.error(
                "Token exchange failed",
                status_code=response.status_code,
                response=response.text,
            )
            raise TwitterAPIError(
                f"Token exchange failed: {response.text}",
                status_code=response.status_code,
            )

        return response.json()

    async def refresh_access_token(
        self,
        refresh_token: str,
    ) -> dict[str, Any]:
        """Refresh an access token using a refresh token."""
        client = await self.get_client()

        import base64

        credentials = f"{settings.twitter_client_id}:{settings.twitter_client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}",
        }

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        response = await client.post(
            self.TWITTER_TOKEN_URL,
            headers=headers,
            data=data,
        )

        if response.status_code != 200:
            logger.error(
                "Token refresh failed",
                status_code=response.status_code,
                response=response.text,
            )
            raise TwitterAPIError(
                f"Token refresh failed: {response.text}",
                status_code=response.status_code,
            )

        return response.json()

    async def get_current_user(self, access_token: str) -> dict[str, Any]:
        """Get the current authenticated Twitter user."""
        client = await self.get_client()

        headers = {"Authorization": f"Bearer {access_token}"}

        response = await client.get(
            f"{self.TWITTER_API_BASE}/users/me",
            headers=headers,
            params={"user.fields": "id,name,username,profile_image_url"},
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code != 200:
            raise TwitterAPIError(
                f"Failed to get user: {response.text}",
                status_code=response.status_code,
            )

        return response.json()

    async def post_tweet(
        self,
        access_token: str,
        text: str,
        reply_to_tweet_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Post a tweet."""
        if len(text) > 280:
            raise TwitterAPIError("Tweet exceeds 280 character limit")

        client = await self.get_client()

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        payload: dict[str, Any] = {"text": text}

        if reply_to_tweet_id:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to_tweet_id}

        response = await client.post(
            f"{self.TWITTER_API_BASE}/tweets",
            headers=headers,
            json=payload,
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code not in [200, 201]:
            error_data = response.json() if response.text else {}
            raise TwitterAPIError(
                f"Failed to post tweet: {response.text}",
                status_code=response.status_code,
                error_code=error_data.get("errors", [{}])[0].get("code"),
            )

        logger.info("Tweet posted successfully")
        return response.json()

    async def post_thread(
        self,
        access_token: str,
        tweets: list[str],
    ) -> list[dict[str, Any]]:
        """Post a thread of tweets."""
        results = []
        previous_tweet_id = None

        for tweet_text in tweets:
            result = await self.post_tweet(
                access_token,
                tweet_text,
                reply_to_tweet_id=previous_tweet_id,
            )
            results.append(result)
            previous_tweet_id = result["data"]["id"]

        return results

    # OAuth Account Management

    async def get_oauth_account(
        self,
        user_id: UUID,
    ) -> Optional[OAuthAccount]:
        """Get Twitter OAuth account for a user."""
        stmt = select(OAuthAccount).where(
            OAuthAccount.user_id == user_id,
            OAuthAccount.provider == OAuthProvider.TWITTER,
            OAuthAccount.is_active == True,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def save_oauth_account(
        self,
        user_id: UUID,
        token_data: dict[str, Any],
        user_data: dict[str, Any],
    ) -> OAuthAccount:
        """Save or update Twitter OAuth account."""
        twitter_user = user_data.get("data", {})

        # Check for existing account
        existing = await self.get_oauth_account(user_id)

        if existing:
            # Update existing account
            existing.encrypted_access_token = encrypt_value(token_data["access_token"])
            if "refresh_token" in token_data:
                existing.encrypted_refresh_token = encrypt_value(
                    token_data["refresh_token"]
                )
            existing.token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=token_data.get("expires_in", 7200)
            )
            existing.provider_username = twitter_user.get("username")
            existing.provider_display_name = twitter_user.get("name")
            existing.provider_profile_image = twitter_user.get("profile_image_url")
            existing.token_scope = token_data.get("scope")
            existing.is_active = True
            existing.clear_errors()

            await self.db.flush()
            await self.db.refresh(existing)

            logger.info("Twitter account updated", user_id=str(user_id))
            return existing

        # Create new account
        oauth_account = OAuthAccount(
            user_id=user_id,
            provider=OAuthProvider.TWITTER,
            provider_user_id=twitter_user.get("id"),
            provider_username=twitter_user.get("username"),
            provider_display_name=twitter_user.get("name"),
            provider_profile_image=twitter_user.get("profile_image_url"),
            encrypted_access_token=encrypt_value(token_data["access_token"]),
            encrypted_refresh_token=(
                encrypt_value(token_data["refresh_token"])
                if "refresh_token" in token_data
                else None
            ),
            token_expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=token_data.get("expires_in", 7200)),
            token_scope=token_data.get("scope"),
            is_active=True,
        )

        self.db.add(oauth_account)
        await self.db.flush()
        await self.db.refresh(oauth_account)

        logger.info("Twitter account connected", user_id=str(user_id))
        return oauth_account

    async def get_valid_access_token(
        self,
        user_id: UUID,
    ) -> Optional[str]:
        """Get a valid access token, refreshing if necessary."""
        oauth_account = await self.get_oauth_account(user_id)
        if not oauth_account:
            return None

        # Check if token needs refresh
        if oauth_account.needs_refresh and oauth_account.encrypted_refresh_token:
            try:
                refresh_token = decrypt_value(oauth_account.encrypted_refresh_token)
                new_tokens = await self.refresh_access_token(refresh_token)

                # Update stored tokens
                oauth_account.encrypted_access_token = encrypt_value(
                    new_tokens["access_token"]
                )
                if "refresh_token" in new_tokens:
                    oauth_account.encrypted_refresh_token = encrypt_value(
                        new_tokens["refresh_token"]
                    )
                oauth_account.token_expires_at = datetime.now(timezone.utc) + timedelta(
                    seconds=new_tokens.get("expires_in", 7200)
                )
                oauth_account.clear_errors()

                await self.db.flush()

                logger.info("Twitter token refreshed", user_id=str(user_id))
                return new_tokens["access_token"]

            except TwitterAPIError as e:
                oauth_account.record_error(str(e))
                await self.db.flush()
                logger.error(
                    "Token refresh failed",
                    user_id=str(user_id),
                    error=str(e),
                )
                return None

        return decrypt_value(oauth_account.encrypted_access_token)

    async def disconnect_account(self, user_id: UUID) -> bool:
        """Disconnect Twitter account from user."""
        oauth_account = await self.get_oauth_account(user_id)
        if not oauth_account:
            return False

        oauth_account.is_active = False
        await self.db.flush()

        logger.info("Twitter account disconnected", user_id=str(user_id))
        return True

    async def verify_connection(self, user_id: UUID) -> bool:
        """Verify Twitter connection is working."""
        access_token = await self.get_valid_access_token(user_id)
        if not access_token:
            return False

        try:
            await self.get_current_user(access_token)
            return True
        except TwitterAPIError:
            return False
