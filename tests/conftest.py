"""Pytest configuration and fixtures."""

import asyncio
import os
from datetime import datetime, timezone
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Set test environment variables before importing app modules
os.environ["APP_ENV"] = "testing"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key-for-testing"
os.environ["TWITTER_CLIENT_ID"] = "test-client-id"
os.environ["TWITTER_CLIENT_SECRET"] = "test-client-secret"
os.environ["TWITTER_REDIRECT_URI"] = "http://localhost:8000/twitter/callback"
os.environ["ENCRYPTION_KEY"] = "55fz7TDM7-nMYa-FbGjaumHYFPbSS4DCHhD3IXqBnW8="

from app.core.database import Base
from app.core.security import hash_password
from app.main import app
from app.models.audit import AuditAction, AuditLog
from app.models.oauth import OAuthAccount, OAuthProvider
from app.models.tweet import ScheduledTweet, TweetStatus
from app.models.user import EncryptedAPIKey, APIKeyType, User, UserRole


# Test database engine
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def async_engine():
    """Create async database engine for tests."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Enable foreign keys for SQLite
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a database session for tests."""
    async_session_maker = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def test_user(db_session: AsyncSession) -> User:
    """Create a test user."""
    user = User(
        id=uuid4(),
        email="test@example.com",
        hashed_password=hash_password("TestPass123!"),
        full_name="Test User",
        timezone="UTC",
        is_active=True,
        is_verified=True,
        role=UserRole.USER,
        default_tone="professional",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture(scope="function")
async def admin_user(db_session: AsyncSession) -> User:
    """Create an admin test user."""
    user = User(
        id=uuid4(),
        email="admin@example.com",
        hashed_password=hash_password("AdminPass123!"),
        full_name="Admin User",
        timezone="UTC",
        is_active=True,
        is_verified=True,
        role=UserRole.ADMIN,
        default_tone="professional",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture(scope="function")
async def twitter_user(db_session: AsyncSession) -> User:
    """Create a test user with Twitter OAuth (no email/password)."""
    user = User(
        id=uuid4(),
        email=None,
        hashed_password=None,
        full_name="Twitter User",
        timezone="UTC",
        is_active=True,
        is_verified=True,
        role=UserRole.USER,
        default_tone="professional",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture(scope="function")
async def oauth_account(db_session: AsyncSession, test_user: User) -> OAuthAccount:
    """Create a test OAuth account."""
    from app.core.security import encrypt_value

    account = OAuthAccount(
        id=uuid4(),
        user_id=test_user.id,
        provider=OAuthProvider.TWITTER,
        provider_user_id="12345678",
        provider_username="testuser",
        provider_display_name="Test User",
        provider_profile_image="https://pbs.twimg.com/profile_images/test.jpg",
        encrypted_access_token=encrypt_value("test-access-token"),
        encrypted_refresh_token=encrypt_value("test-refresh-token"),
        token_expires_at=datetime(2099, 12, 31, tzinfo=timezone.utc),
        is_active=True,
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


@pytest_asyncio.fixture(scope="function")
async def api_key(db_session: AsyncSession, test_user: User) -> EncryptedAPIKey:
    """Create a test API key."""
    from app.core.security import encrypt_value

    key = EncryptedAPIKey(
        id=uuid4(),
        user_id=test_user.id,
        key_type=APIKeyType.DEEPSEEK,
        encrypted_key=encrypt_value("sk-test-deepseek-key"),
        key_hint="key",
        is_valid=True,
    )
    db_session.add(key)
    await db_session.commit()
    await db_session.refresh(key)
    return key


@pytest_asyncio.fixture(scope="function")
async def scheduled_tweet(db_session: AsyncSession, test_user: User) -> ScheduledTweet:
    """Create a test scheduled tweet."""
    tweet = ScheduledTweet(
        id=uuid4(),
        user_id=test_user.id,
        content="Test tweet content #testing",
        status=TweetStatus.PENDING,
        scheduled_for=datetime(2099, 12, 31, 12, 0, 0, tzinfo=timezone.utc),
        is_thread=False,
    )
    db_session.add(tweet)
    await db_session.commit()
    await db_session.refresh(tweet)
    return tweet


@pytest_asyncio.fixture(scope="function")
async def posted_tweet(db_session: AsyncSession, test_user: User) -> ScheduledTweet:
    """Create a test posted tweet."""
    tweet = ScheduledTweet(
        id=uuid4(),
        user_id=test_user.id,
        content="Posted tweet content",
        status=TweetStatus.POSTED,
        scheduled_for=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        posted_at=datetime(2024, 1, 1, 12, 0, 5, tzinfo=timezone.utc),
        twitter_tweet_id="1234567890",
        is_thread=False,
    )
    db_session.add(tweet)
    await db_session.commit()
    await db_session.refresh(tweet)
    return tweet


@pytest.fixture
def mock_twitter_api():
    """Mock Twitter API responses."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_client.return_value.__aenter__.return_value = mock_instance

        # Mock token exchange
        mock_instance.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "expires_in": 7200,
                "token_type": "bearer",
            },
        )

        # Mock user info
        mock_instance.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": {
                    "id": "12345678",
                    "name": "Test User",
                    "username": "testuser",
                    "profile_image_url": "https://pbs.twimg.com/profile_images/test.jpg",
                }
            },
        )

        yield mock_instance


@pytest.fixture
def mock_deepseek_api():
    """Mock DeepSeek API responses."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_client.return_value.__aenter__.return_value = mock_instance

        mock_instance.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "choices": [
                    {
                        "message": {
                            "content": "This is a generated tweet about AI and technology. #AI #Tech"
                        }
                    }
                ]
            },
        )

        yield mock_instance


@pytest.fixture
def app_client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client."""
    from app.core.database import get_db

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(test_user: User) -> dict:
    """Create authentication headers with a valid JWT token."""
    from app.services.auth import AuthService

    # Create a mock auth service to generate tokens
    auth_service = AuthService(None)
    tokens = auth_service.create_tokens(test_user)

    return {"Cookie": f"access_token={tokens['access_token']}"}


@pytest.fixture
def admin_auth_headers(admin_user: User) -> dict:
    """Create authentication headers for admin user."""
    from app.services.auth import AuthService

    auth_service = AuthService(None)
    tokens = auth_service.create_tokens(admin_user)

    return {"Cookie": f"access_token={tokens['access_token']}"}
