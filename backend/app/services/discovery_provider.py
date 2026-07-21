from datetime import datetime
from importlib import import_module
from typing import Any, Protocol, cast

from pydantic import BaseModel, Field


class TavilyAsyncClient(Protocol):
    async def search(self, **kwargs: object) -> dict[str, Any]: ...


class TavilyClientFactory(Protocol):
    def __call__(self, *, api_key: str) -> TavilyAsyncClient: ...


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    score: float = Field(ge=0, le=1)
    published_at: datetime | None = None
    provider: str


class CompetitorSearchProvider(Protocol):
    """Replaceable web-search boundary for competitor discovery."""

    async def search(self, *, query: str, max_results: int) -> list[SearchResult]: ...


class DiscoveryProviderError(Exception):
    """A search provider could not complete a bounded request."""


class UnavailableCompetitorSearchProvider:
    async def search(self, *, query: str, max_results: int) -> list[SearchResult]:
        del query, max_results
        raise DiscoveryProviderError


class TavilyCompetitorSearchProvider:
    """Map Tavily search responses into application-owned result models."""

    def __init__(self, api_key: str) -> None:
        module = import_module("tavily")
        client_class = cast(TavilyClientFactory, module.AsyncTavilyClient)
        self._client = client_class(api_key=api_key)

    async def search(self, *, query: str, max_results: int) -> list[SearchResult]:
        try:
            response = await self._client.search(
                query=query,
                search_depth="basic",
                max_results=max_results,
                include_answer=False,
                include_raw_content=False,
                include_images=False,
            )
        except Exception as exc:
            raise DiscoveryProviderError from exc
        raw_results = response.get("results", []) if isinstance(response, dict) else []
        return [result for item in raw_results if (result := self._map_result(item)) is not None]

    @staticmethod
    def _map_result(item: object) -> SearchResult | None:
        if not isinstance(item, dict):
            return None
        data: dict[str, Any] = item
        title, url = data.get("title"), data.get("url")
        if not isinstance(title, str) or not isinstance(url, str):
            return None
        content = data.get("content")
        score = data.get("score")
        return SearchResult(
            title=title,
            url=url,
            snippet=content if isinstance(content, str) else "",
            score=max(0.0, min(float(score), 1.0)) if isinstance(score, int | float) else 0.0,
            provider="tavily",
        )
