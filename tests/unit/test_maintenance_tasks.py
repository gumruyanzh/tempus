"""Tests for maintenance Celery tasks."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.tasks.maintenance_tasks import (
    _cleanup_old_execution_logs_async,
    health_check,
)


class TestCleanupOldExecutionLogs:
    """Tests for cleanup_old_execution_logs task."""

    @pytest.mark.asyncio
    async def test_cleanup_no_old_logs(self):
        """Test cleanup when no old logs exist."""
        with patch("app.tasks.maintenance_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.rowcount = 0
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _cleanup_old_execution_logs_async(days_to_keep=30)

            assert result["deleted"] == 0
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_deletes_old_logs(self):
        """Test cleanup deletes old logs."""
        with patch("app.tasks.maintenance_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.rowcount = 150
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _cleanup_old_execution_logs_async(days_to_keep=30)

            assert result["deleted"] == 150
            mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_custom_days(self):
        """Test cleanup with custom days to keep."""
        with patch("app.tasks.maintenance_tasks.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_factory.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.rowcount = 50
            mock_session.execute = AsyncMock(return_value=mock_result)

            result = await _cleanup_old_execution_logs_async(days_to_keep=7)

            assert result["deleted"] == 50


class TestHealthCheck:
    """Tests for health_check task."""

    def test_health_check_returns_healthy(self):
        """Test health check returns healthy status."""
        result = health_check()

        assert result["status"] == "healthy"
        assert "timestamp" in result

    def test_health_check_timestamp_is_valid(self):
        """Test health check timestamp is valid ISO format."""
        result = health_check()

        # Should not raise
        datetime.fromisoformat(result["timestamp"].replace("Z", "+00:00"))
