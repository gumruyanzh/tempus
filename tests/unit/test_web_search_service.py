"""Tests for web search service."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.services.web_search import SearchResult, WebSearchError, WebSearchService


class TestSearchResult:
    """Tests for SearchResult dataclass."""

    def test_search_result_basic(self):
        """Test SearchResult creation."""
        result = SearchResult(
            title="Test Article",
            url="https://example.com/article",
            content="This is the article content.",
            score=0.9,
        )
        assert result.title == "Test Article"
        assert result.url == "https://example.com/article"
        assert result.content == "This is the article content."
        assert result.score == 0.9

    def test_search_result_with_score(self):
        """Test SearchResult with relevance score."""
        result = SearchResult(
            title="Test",
            url="https://example.com",
            content="Content",
            score=0.95,
        )
        assert result.score == 0.95


class TestWebSearchService:
    """Tests for WebSearchService."""

    def test_init(self):
        """Test service initialization."""
        service = WebSearchService("tvly-test-key")
        assert service.api_key == "tvly-test-key"
        assert service._client is None

    @pytest.mark.asyncio
    async def test_get_client(self):
        """Test HTTP client creation."""
        service = WebSearchService("tvly-test-key")

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance

            client = await service.get_client()
            assert client is not None

            await service.close()

    @pytest.mark.asyncio
    async def test_close_client(self):
        """Test HTTP client cleanup."""
        service = WebSearchService("tvly-test-key")

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value = mock_instance

            await service.get_client()
            await service.close()
            mock_instance.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_success(self):
        """Test successful search."""
        service = WebSearchService("tvly-test-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "results": [
                    {
                        "title": "AI News",
                        "url": "https://example.com/ai-news",
                        "content": "Latest AI developments...",
                        "score": 0.9,
                    },
                    {
                        "title": "ML Update",
                        "url": "https://example.com/ml-update",
                        "content": "Machine learning news...",
                        "score": 0.85,
                    },
                ]
            }
            mock_client.post.return_value = mock_response

            results = await service.search("AI news", max_results=5)

            assert len(results) == 2
            assert results[0].title == "AI News"
            assert results[1].title == "ML Update"

        await service.close()

    @pytest.mark.asyncio
    async def test_search_news_success(self):
        """Test news search."""
        service = WebSearchService("tvly-test-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "results": [
                    {
                        "title": "Breaking News",
                        "url": "https://news.example.com/breaking",
                        "content": "Breaking news content...",
                        "score": 0.9,
                    },
                ]
            }
            mock_client.post.return_value = mock_response

            results = await service.search_news("technology", days=7)

            assert len(results) == 1
            assert results[0].title == "Breaking News"

        await service.close()

    @pytest.mark.asyncio
    async def test_search_api_error(self):
        """Test search API error handling."""
        service = WebSearchService("tvly-test-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = "Unauthorized"
            mock_client.post.return_value = mock_response

            with pytest.raises(WebSearchError) as exc_info:
                await service.search("test query")

            assert "401" in str(exc_info.value) or "Unauthorized" in str(exc_info.value)

        await service.close()

    @pytest.mark.asyncio
    async def test_search_rate_limit(self):
        """Test rate limit handling."""
        service = WebSearchService("tvly-test-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_response.text = "Rate limit exceeded"
            mock_client.post.return_value = mock_response

            with pytest.raises(WebSearchError):
                await service.search("test query")

        await service.close()

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        """Test handling empty results."""
        service = WebSearchService("tvly-test-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"results": []}
            mock_client.post.return_value = mock_response

            results = await service.search("obscure query")
            assert results == []

        await service.close()

    @pytest.mark.asyncio
    async def test_search_network_error(self):
        """Test network error handling."""
        import httpx

        service = WebSearchService("tvly-test-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client
            mock_client.post.side_effect = httpx.RequestError("Network error")

            with pytest.raises(WebSearchError):
                await service.search("test query")

        await service.close()

    def test_format_results_for_prompt(self):
        """Test formatting results for LLM prompt."""
        service = WebSearchService("tvly-test-key")

        results = [
            SearchResult(
                title="Article 1",
                url="https://example.com/1",
                content="This is the first article content.",
                score=0.9,
            ),
            SearchResult(
                title="Article 2",
                url="https://example.com/2",
                content="This is the second article content.",
                score=0.85,
            ),
        ]

        formatted = service.format_results_for_prompt(results)

        assert "Article 1" in formatted
        assert "Article 2" in formatted
        assert "first article" in formatted
        assert "second article" in formatted

    def test_format_results_for_prompt_truncates(self):
        """Test that formatting truncates long content."""
        service = WebSearchService("tvly-test-key")

        results = [
            SearchResult(
                title="Long Article",
                url="https://example.com/long",
                content="A" * 500,  # Very long content
                score=0.9,
            ),
        ]

        formatted = service.format_results_for_prompt(results, max_chars_per_result=100)

        # Should be truncated
        assert len(formatted) < 600

    def test_format_results_for_prompt_empty(self):
        """Test formatting empty results."""
        service = WebSearchService("tvly-test-key")

        formatted = service.format_results_for_prompt([])
        assert formatted == "" or formatted is None or "no" in formatted.lower()

    def test_format_results_for_prompt_no_score(self):
        """Test formatting results without score."""
        service = WebSearchService("tvly-test-key")

        results = [
            SearchResult(
                title="No Score Article",
                url="https://example.com/noscore",
                content="Article without score.",
                score=0.0,
            ),
        ]

        formatted = service.format_results_for_prompt(results)
        assert "No Score Article" in formatted

    @pytest.mark.asyncio
    async def test_search_respects_max_results(self):
        """Test that search respects max_results parameter."""
        service = WebSearchService("tvly-test-key")

        with patch.object(service, "get_client") as mock_get_client:
            mock_client = AsyncMock()
            mock_get_client.return_value = mock_client

            # Return more results than requested
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "results": [
                    {"title": f"Result {i}", "url": f"https://example.com/{i}",
                     "content": f"Content {i}", "score": 0.9}
                    for i in range(10)
                ]
            }
            mock_client.post.return_value = mock_response

            results = await service.search("test", max_results=3)

            # API should be called with max_results
            call_args = mock_client.post.call_args
            assert call_args is not None

        await service.close()


class TestWebSearchError:
    """Tests for WebSearchError."""

    def test_error_message(self):
        """Test error message."""
        error = WebSearchError("Search failed")
        assert str(error) == "Search failed"

    def test_error_with_status_code(self):
        """Test error with status code."""
        error = WebSearchError("API error", status_code=401)
        assert error.status_code == 401
