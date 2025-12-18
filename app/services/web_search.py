"""Web search service for campaign research using Tavily API."""

from dataclasses import dataclass
from typing import List, Optional

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SearchResult:
    """A single search result."""

    title: str
    url: str
    content: str
    score: float


class WebSearchError(Exception):
    """Web search error."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


class WebSearchService:
    """Service for web search operations using Tavily API."""

    TAVILY_API_URL = "https://api.tavily.com"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.TAVILY_API_URL,
                timeout=30.0,
                headers={
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> List[SearchResult]:
        """
        Search the web using Tavily API.

        Args:
            query: The search query
            max_results: Maximum number of results (default 5)
            search_depth: "basic" or "advanced" (more comprehensive)
            include_domains: Only include results from these domains
            exclude_domains: Exclude results from these domains

        Returns:
            List of SearchResult objects
        """
        client = await self.get_client()

        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": False,
            "include_raw_content": False,
        }

        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains

        try:
            response = await client.post("/search", json=payload)

            if response.status_code != 200:
                error_text = response.text
                logger.error(
                    "Tavily API error",
                    status_code=response.status_code,
                    response=error_text,
                )
                raise WebSearchError(
                    f"Search failed: {error_text}",
                    status_code=response.status_code,
                )

            data = response.json()
            results = []

            for item in data.get("results", []):
                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        content=item.get("content", ""),
                        score=item.get("score", 0.0),
                    )
                )

            logger.info(
                "Web search completed",
                query=query[:50],
                results_count=len(results),
            )

            return results

        except httpx.RequestError as e:
            logger.error("Tavily API request error", error=str(e))
            raise WebSearchError(f"Request failed: {str(e)}")

    async def search_news(
        self,
        query: str,
        max_results: int = 5,
        days: int = 7,
    ) -> List[SearchResult]:
        """
        Search recent news using Tavily API.

        Args:
            query: The search query
            max_results: Maximum number of results
            days: Only include news from the last N days

        Returns:
            List of SearchResult objects
        """
        # Add "news" context to the query for better results
        news_query = f"{query} latest news"

        client = await self.get_client()

        payload = {
            "api_key": self.api_key,
            "query": news_query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "topic": "news",
            "days": days,
        }

        try:
            response = await client.post("/search", json=payload)

            if response.status_code != 200:
                error_text = response.text
                logger.error(
                    "Tavily news search error",
                    status_code=response.status_code,
                    response=error_text,
                )
                raise WebSearchError(
                    f"News search failed: {error_text}",
                    status_code=response.status_code,
                )

            data = response.json()
            results = []

            for item in data.get("results", []):
                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        content=item.get("content", ""),
                        score=item.get("score", 0.0),
                    )
                )

            logger.info(
                "News search completed",
                query=query[:50],
                results_count=len(results),
            )

            return results

        except httpx.RequestError as e:
            logger.error("Tavily news search request error", error=str(e))
            raise WebSearchError(f"News search request failed: {str(e)}")

    async def validate_api_key(self) -> bool:
        """Validate that the API key is working."""
        try:
            results = await self.search("test query", max_results=1)
            return True
        except WebSearchError as e:
            if e.status_code == 401:
                return False
            # Other errors might be transient
            logger.warning("API key validation uncertain", error=str(e))
            return True
        except Exception as e:
            logger.error("API key validation failed", error=str(e))
            return False

    def format_results_for_prompt(
        self,
        results: List[SearchResult],
        max_chars_per_result: int = 500,
    ) -> str:
        """
        Format search results into a string suitable for LLM prompts.

        Args:
            results: List of search results
            max_chars_per_result: Maximum characters per result content

        Returns:
            Formatted string with search context
        """
        if not results:
            return "No recent information found."

        formatted_parts = []
        for i, result in enumerate(results, 1):
            content = result.content[:max_chars_per_result]
            if len(result.content) > max_chars_per_result:
                content += "..."

            formatted_parts.append(
                f"{i}. {result.title}\n   {content}"
            )

        return "\n\n".join(formatted_parts)
