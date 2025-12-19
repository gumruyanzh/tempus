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
from app.models.user import User

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
    # offline.access is required to get refresh tokens for long-lived sessions
    # like.read/write for liking tweets, follows.read/write for following users
    TWITTER_SCOPES = [
        "tweet.read",
        "tweet.write",
        "users.read",
        "offline.access",
        "like.read",
        "like.write",
        "follows.read",
        "follows.write",
    ]

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

    async def find_user_by_twitter_id(self, twitter_id: str) -> Optional[User]:
        """Find a user by their Twitter provider ID."""
        stmt = select(OAuthAccount).where(
            OAuthAccount.provider == OAuthProvider.TWITTER,
            OAuthAccount.provider_user_id == twitter_id,
            OAuthAccount.is_active == True,
        )
        result = await self.db.execute(stmt)
        oauth_account = result.scalar_one_or_none()

        if oauth_account:
            # Load the user
            stmt = select(User).where(
                User.id == oauth_account.user_id,
                User.deleted_at.is_(None),
                User.is_active == True,
            )
            result = await self.db.execute(stmt)
            return result.scalar_one_or_none()

        return None

    async def create_user_from_twitter(
        self,
        token_data: dict[str, Any],
        user_data: dict[str, Any],
    ) -> tuple[User, OAuthAccount]:
        """Create a new user from Twitter OAuth data."""
        twitter_user = user_data.get("data", {})

        # Create new user
        user = User(
            email=None,  # Twitter-only users don't have email initially
            hashed_password=None,
            full_name=twitter_user.get("name"),
            is_active=True,
            is_verified=True,  # Twitter users are verified via OAuth
        )
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)

        # Create OAuth account
        oauth_account = OAuthAccount(
            user_id=user.id,
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

        logger.info(
            "User created from Twitter",
            user_id=str(user.id),
            twitter_username=twitter_user.get("username"),
        )

        return user, oauth_account

    async def sign_in_or_sign_up_with_twitter(
        self,
        token_data: dict[str, Any],
        user_data: dict[str, Any],
    ) -> tuple[User, bool]:
        """Sign in existing user or create new user from Twitter.

        Returns tuple of (user, is_new_user).
        """
        twitter_user = user_data.get("data", {})
        twitter_id = twitter_user.get("id")

        # Check if user already exists with this Twitter account
        existing_user = await self.find_user_by_twitter_id(twitter_id)

        if existing_user:
            # Update OAuth tokens for existing user
            await self.save_oauth_account(
                user_id=existing_user.id,
                token_data=token_data,
                user_data=user_data,
            )
            existing_user.update_last_login()
            return existing_user, False

        # Create new user
        user, oauth_account = await self.create_user_from_twitter(
            token_data=token_data,
            user_data=user_data,
        )
        user.update_last_login()

        return user, True

    # Twitter Search and Trends

    async def search_recent_tweets(
        self,
        access_token: str,
        query: str,
        max_results: int = 10,
        sort_order: str = "relevancy",
    ) -> list[dict[str, Any]]:
        """
        Search for recent tweets matching a query.

        Args:
            access_token: Valid Twitter access token
            query: Search query (supports Twitter search operators)
            max_results: Maximum number of results (10-100)
            sort_order: "recency" or "relevancy"

        Returns:
            List of tweet objects with text, metrics, and author info
        """
        client = await self.get_client()

        headers = {"Authorization": f"Bearer {access_token}"}

        # Build search query - exclude retweets and replies for cleaner results
        search_query = f"{query} -is:retweet -is:reply lang:en"

        params = {
            "query": search_query,
            "max_results": min(max(max_results, 10), 100),  # API limits: 10-100
            "sort_order": sort_order,
            "tweet.fields": "created_at,public_metrics,author_id,text",
            "expansions": "author_id",
            "user.fields": "username,name,verified",
        }

        try:
            response = await client.get(
                f"{self.TWITTER_API_BASE}/tweets/search/recent",
                headers=headers,
                params=params,
            )

            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                raise TwitterRateLimitError(
                    retry_after=int(retry_after) if retry_after else None
                )

            if response.status_code == 403:
                # Likely insufficient permissions
                logger.warning("Twitter search not available - may need elevated access")
                return []

            if response.status_code != 200:
                logger.error(
                    "Twitter search failed",
                    status_code=response.status_code,
                    response=response.text,
                )
                return []

            data = response.json()
            tweets = data.get("data", [])
            users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}

            # Enrich tweets with author info
            results = []
            for tweet in tweets:
                author = users.get(tweet.get("author_id"), {})
                results.append({
                    "id": tweet.get("id"),
                    "text": tweet.get("text"),
                    "created_at": tweet.get("created_at"),
                    "metrics": tweet.get("public_metrics", {}),
                    "author_username": author.get("username"),
                    "author_name": author.get("name"),
                    "author_verified": author.get("verified", False),
                })

            logger.info(
                "Twitter search completed",
                query=query[:50],
                results_count=len(results),
            )

            return results

        except httpx.RequestError as e:
            logger.error("Twitter search request error", error=str(e))
            return []

    async def get_trending_topics(
        self,
        access_token: str,
        woeid: int = 1,  # 1 = Worldwide, 23424977 = USA
    ) -> list[dict[str, Any]]:
        """
        Get trending topics for a location.

        Note: Requires elevated API access. Returns empty list if not available.

        Args:
            access_token: Valid Twitter access token
            woeid: Yahoo! Where On Earth ID (1 = worldwide)

        Returns:
            List of trending topics with name and tweet volume
        """
        client = await self.get_client()

        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            # Twitter API v2 trends endpoint
            response = await client.get(
                f"{self.TWITTER_API_BASE}/trends/by/woeid/{woeid}",
                headers=headers,
            )

            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                raise TwitterRateLimitError(
                    retry_after=int(retry_after) if retry_after else None
                )

            if response.status_code in [403, 404]:
                # Trends endpoint requires elevated access
                logger.info("Twitter trends not available - requires elevated access")
                return []

            if response.status_code != 200:
                logger.warning(
                    "Twitter trends request failed",
                    status_code=response.status_code,
                )
                return []

            data = response.json()
            trends = []

            for trend in data.get("data", []):
                trends.append({
                    "name": trend.get("name"),
                    "tweet_count": trend.get("tweet_count"),
                    "description": trend.get("description"),
                })

            logger.info("Twitter trends fetched", count=len(trends))
            return trends

        except httpx.RequestError as e:
            logger.error("Twitter trends request error", error=str(e))
            return []

    async def get_popular_tweets_about_topic(
        self,
        access_token: str,
        topic: str,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Get popular/engaging tweets about a topic.

        Searches for recent tweets and sorts by engagement.

        Args:
            access_token: Valid Twitter access token
            topic: Topic to search for
            max_results: Number of top tweets to return

        Returns:
            List of popular tweets sorted by engagement
        """
        # Search for more tweets than needed, then filter by engagement
        tweets = await self.search_recent_tweets(
            access_token=access_token,
            query=topic,
            max_results=50,
            sort_order="relevancy",
        )

        if not tweets:
            return []

        # Score tweets by engagement
        def engagement_score(tweet: dict) -> int:
            metrics = tweet.get("metrics", {})
            return (
                metrics.get("like_count", 0) * 1 +
                metrics.get("retweet_count", 0) * 3 +
                metrics.get("reply_count", 0) * 2 +
                metrics.get("quote_count", 0) * 4
            )

        # Sort by engagement and return top results
        sorted_tweets = sorted(tweets, key=engagement_score, reverse=True)
        return sorted_tweets[:max_results]

    def format_twitter_context_for_prompt(
        self,
        tweets: list[dict[str, Any]],
        trends: list[dict[str, Any]] = None,
    ) -> str:
        """
        Format Twitter search results and trends for LLM prompt.

        Args:
            tweets: List of tweet objects from search
            trends: Optional list of trending topics

        Returns:
            Formatted string for prompt context
        """
        parts = []

        if trends:
            trending_names = [t["name"] for t in trends[:5] if t.get("name")]
            if trending_names:
                parts.append(f"Currently trending on Twitter: {', '.join(trending_names)}")

        if tweets:
            parts.append("\nPopular tweets about this topic:")
            for i, tweet in enumerate(tweets[:5], 1):
                author = tweet.get("author_username", "unknown")
                text = tweet.get("text", "")[:200]
                metrics = tweet.get("metrics", {})
                likes = metrics.get("like_count", 0)
                retweets = metrics.get("retweet_count", 0)

                parts.append(
                    f"{i}. @{author} ({likes} likes, {retweets} RTs):\n   \"{text}\""
                )

        if not parts:
            return ""

        return "\n".join(parts)

    # Engagement Methods for Growth Strategy

    async def follow_user(
        self,
        access_token: str,
        target_user_id: str,
    ) -> dict[str, Any]:
        """
        Follow a Twitter user.

        Args:
            access_token: Valid Twitter access token
            target_user_id: Twitter user ID to follow

        Returns:
            Response data with following status
        """
        client = await self.get_client()

        # First get current user's ID
        current_user = await self.get_current_user(access_token)
        source_user_id = current_user["data"]["id"]

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        response = await client.post(
            f"{self.TWITTER_API_BASE}/users/{source_user_id}/following",
            headers=headers,
            json={"target_user_id": target_user_id},
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code not in [200, 201]:
            raise TwitterAPIError(
                f"Failed to follow user: {response.text}",
                status_code=response.status_code,
            )

        logger.info("User followed", target_user_id=target_user_id)
        return response.json()

    async def unfollow_user(
        self,
        access_token: str,
        target_user_id: str,
    ) -> dict[str, Any]:
        """
        Unfollow a Twitter user.

        Args:
            access_token: Valid Twitter access token
            target_user_id: Twitter user ID to unfollow

        Returns:
            Response data with following status
        """
        client = await self.get_client()

        # Get current user's ID
        current_user = await self.get_current_user(access_token)
        source_user_id = current_user["data"]["id"]

        headers = {"Authorization": f"Bearer {access_token}"}

        response = await client.delete(
            f"{self.TWITTER_API_BASE}/users/{source_user_id}/following/{target_user_id}",
            headers=headers,
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code != 200:
            raise TwitterAPIError(
                f"Failed to unfollow user: {response.text}",
                status_code=response.status_code,
            )

        logger.info("User unfollowed", target_user_id=target_user_id)
        return response.json()

    async def get_following(
        self,
        access_token: str,
        user_id: str,
        max_results: int = 100,
        pagination_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Get list of users that a user is following.

        Args:
            access_token: Valid Twitter access token
            user_id: Twitter user ID
            max_results: Maximum results per page (1-1000)
            pagination_token: Token for pagination

        Returns:
            Dict with users list and pagination info
        """
        client = await self.get_client()

        headers = {"Authorization": f"Bearer {access_token}"}

        params = {
            "max_results": min(max_results, 1000),
            "user.fields": "id,username,name,description,public_metrics,verified",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token

        response = await client.get(
            f"{self.TWITTER_API_BASE}/users/{user_id}/following",
            headers=headers,
            params=params,
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code != 200:
            raise TwitterAPIError(
                f"Failed to get following: {response.text}",
                status_code=response.status_code,
            )

        return response.json()

    async def get_followers(
        self,
        access_token: str,
        user_id: str,
        max_results: int = 100,
        pagination_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Get list of users following a user.

        Args:
            access_token: Valid Twitter access token
            user_id: Twitter user ID
            max_results: Maximum results per page (1-1000)
            pagination_token: Token for pagination

        Returns:
            Dict with users list and pagination info
        """
        client = await self.get_client()

        headers = {"Authorization": f"Bearer {access_token}"}

        params = {
            "max_results": min(max_results, 1000),
            "user.fields": "id,username,name,description,public_metrics,verified",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token

        response = await client.get(
            f"{self.TWITTER_API_BASE}/users/{user_id}/followers",
            headers=headers,
            params=params,
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code != 200:
            raise TwitterAPIError(
                f"Failed to get followers: {response.text}",
                status_code=response.status_code,
            )

        return response.json()

    async def like_tweet(
        self,
        access_token: str,
        tweet_id: str,
    ) -> dict[str, Any]:
        """
        Like a tweet.

        Args:
            access_token: Valid Twitter access token
            tweet_id: Tweet ID to like

        Returns:
            Response data with liked status
        """
        client = await self.get_client()

        # Get current user's ID
        current_user = await self.get_current_user(access_token)
        user_id = current_user["data"]["id"]

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        response = await client.post(
            f"{self.TWITTER_API_BASE}/users/{user_id}/likes",
            headers=headers,
            json={"tweet_id": tweet_id},
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code not in [200, 201]:
            raise TwitterAPIError(
                f"Failed to like tweet: {response.text}",
                status_code=response.status_code,
            )

        logger.info("Tweet liked", tweet_id=tweet_id)
        return response.json()

    async def unlike_tweet(
        self,
        access_token: str,
        tweet_id: str,
    ) -> dict[str, Any]:
        """
        Unlike a tweet.

        Args:
            access_token: Valid Twitter access token
            tweet_id: Tweet ID to unlike

        Returns:
            Response data with liked status
        """
        client = await self.get_client()

        # Get current user's ID
        current_user = await self.get_current_user(access_token)
        user_id = current_user["data"]["id"]

        headers = {"Authorization": f"Bearer {access_token}"}

        response = await client.delete(
            f"{self.TWITTER_API_BASE}/users/{user_id}/likes/{tweet_id}",
            headers=headers,
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code != 200:
            raise TwitterAPIError(
                f"Failed to unlike tweet: {response.text}",
                status_code=response.status_code,
            )

        logger.info("Tweet unliked", tweet_id=tweet_id)
        return response.json()

    async def retweet(
        self,
        access_token: str,
        tweet_id: str,
    ) -> dict[str, Any]:
        """
        Retweet a tweet.

        Args:
            access_token: Valid Twitter access token
            tweet_id: Tweet ID to retweet

        Returns:
            Response data with retweet status
        """
        client = await self.get_client()

        # Get current user's ID
        current_user = await self.get_current_user(access_token)
        user_id = current_user["data"]["id"]

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        response = await client.post(
            f"{self.TWITTER_API_BASE}/users/{user_id}/retweets",
            headers=headers,
            json={"tweet_id": tweet_id},
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code not in [200, 201]:
            raise TwitterAPIError(
                f"Failed to retweet: {response.text}",
                status_code=response.status_code,
            )

        logger.info("Tweet retweeted", tweet_id=tweet_id)
        return response.json()

    async def unretweet(
        self,
        access_token: str,
        tweet_id: str,
    ) -> dict[str, Any]:
        """
        Remove a retweet.

        Args:
            access_token: Valid Twitter access token
            tweet_id: Tweet ID to unretweet

        Returns:
            Response data with retweet status
        """
        client = await self.get_client()

        # Get current user's ID
        current_user = await self.get_current_user(access_token)
        user_id = current_user["data"]["id"]

        headers = {"Authorization": f"Bearer {access_token}"}

        response = await client.delete(
            f"{self.TWITTER_API_BASE}/users/{user_id}/retweets/{tweet_id}",
            headers=headers,
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code != 200:
            raise TwitterAPIError(
                f"Failed to unretweet: {response.text}",
                status_code=response.status_code,
            )

        logger.info("Tweet unretweeted", tweet_id=tweet_id)
        return response.json()

    async def reply_to_tweet(
        self,
        access_token: str,
        tweet_id: str,
        text: str,
    ) -> dict[str, Any]:
        """
        Reply to a tweet.

        Args:
            access_token: Valid Twitter access token
            tweet_id: Tweet ID to reply to
            text: Reply text content

        Returns:
            Response data with reply tweet info
        """
        return await self.post_tweet(
            access_token=access_token,
            text=text,
            reply_to_tweet_id=tweet_id,
        )

    async def get_user_metrics(
        self,
        access_token: str,
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Get user public metrics (followers, following, tweet count).

        Args:
            access_token: Valid Twitter access token
            user_id: User ID (defaults to current user)

        Returns:
            User data with public metrics
        """
        client = await self.get_client()

        if user_id is None:
            # Get current user
            current_user = await self.get_current_user(access_token)
            user_id = current_user["data"]["id"]

        headers = {"Authorization": f"Bearer {access_token}"}

        params = {
            "user.fields": "public_metrics,verified,description,created_at",
        }

        response = await client.get(
            f"{self.TWITTER_API_BASE}/users/{user_id}",
            headers=headers,
            params=params,
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code != 200:
            raise TwitterAPIError(
                f"Failed to get user metrics: {response.text}",
                status_code=response.status_code,
            )

        return response.json()

    async def search_users(
        self,
        access_token: str,
        query: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search for Twitter users by username or name.

        Note: This uses tweet search and extracts unique authors as
        there's no direct user search in Twitter API v2 free tier.

        Args:
            access_token: Valid Twitter access token
            query: Search query
            max_results: Maximum number of users to return

        Returns:
            List of user objects with profile info
        """
        # Search for tweets mentioning the query to find relevant users
        tweets = await self.search_recent_tweets(
            access_token=access_token,
            query=query,
            max_results=100,
            sort_order="relevancy",
        )

        # Extract unique users
        seen_users = set()
        users = []

        for tweet in tweets:
            username = tweet.get("author_username")
            if username and username not in seen_users:
                seen_users.add(username)
                users.append({
                    "username": username,
                    "name": tweet.get("author_name"),
                    "verified": tweet.get("author_verified", False),
                })

                if len(users) >= max_results:
                    break

        return users

    async def get_user_by_username(
        self,
        access_token: str,
        username: str,
    ) -> Optional[dict[str, Any]]:
        """
        Get user info by username.

        Args:
            access_token: Valid Twitter access token
            username: Twitter username (without @)

        Returns:
            User data or None if not found
        """
        client = await self.get_client()

        headers = {"Authorization": f"Bearer {access_token}"}

        params = {
            "user.fields": "id,username,name,description,public_metrics,verified,profile_image_url",
        }

        response = await client.get(
            f"{self.TWITTER_API_BASE}/users/by/username/{username}",
            headers=headers,
            params=params,
        )

        if response.status_code == 404:
            return None

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

    async def get_user_timeline(
        self,
        access_token: str,
        user_id: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Get recent tweets from a user's timeline.

        Args:
            access_token: Valid Twitter access token
            user_id: Twitter user ID
            max_results: Maximum number of tweets (5-100)

        Returns:
            List of tweet objects
        """
        client = await self.get_client()

        headers = {"Authorization": f"Bearer {access_token}"}

        params = {
            "max_results": min(max(max_results, 5), 100),
            "tweet.fields": "created_at,public_metrics,text",
            "exclude": "retweets,replies",
        }

        response = await client.get(
            f"{self.TWITTER_API_BASE}/users/{user_id}/tweets",
            headers=headers,
            params=params,
        )

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise TwitterRateLimitError(
                retry_after=int(retry_after) if retry_after else None
            )

        if response.status_code != 200:
            raise TwitterAPIError(
                f"Failed to get user timeline: {response.text}",
                status_code=response.status_code,
            )

        data = response.json()
        return data.get("data", [])
