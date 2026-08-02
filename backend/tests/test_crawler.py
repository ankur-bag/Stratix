from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.core.config import Settings
from app.crawlers.crawl4ai_adapter import Crawl4AIWebsiteCrawler
from app.crawlers.models import CrawlErrorCode, CrawlRequest
from app.crawlers.policy import CrawlPolicy


class PublicResolver:
    async def resolve(self, hostname: str) -> list[str]:
        del hostname
        return ["93.184.216.34"]


class FakeAsyncWebCrawler:
    requested_urls: ClassVar[list[str]] = []
    redirect_to_auth: ClassVar[bool] = False

    def __init__(self, *, config: Any) -> None:
        del config

    async def __aenter__(self) -> "FakeAsyncWebCrawler":
        type(self).requested_urls = []
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def arun(self, *, url: str, config: Any) -> list[SimpleNamespace]:
        del config
        type(self).requested_urls.append(url)
        if type(self).redirect_to_auth:
            final_url = "https://avento-ai.vercel.app/sign-in"
            links: dict[str, list[dict[str, str]]] = {"internal": []}
        elif url.rstrip("/") == "https://avento-ai.vercel.app":
            final_url = url
            links = {
                "internal": [
                    {"href": "/sign-in"},
                    {"href": "https://avento-ai.vercel.app/sign-up?source=header"},
                    {"href": "/pricing"},
                    {"href": "/features"},
                    {"href": "/about"},
                ]
            }
        else:
            final_url = url
            links = {"internal": []}
        return [
            SimpleNamespace(
                error_message="",
                redirected_status_code=None,
                status_code=200,
                response_headers={"content-type": "text/html"},
                success=True,
                html="<html><body>Public product content</body></html>",
                redirected_url=final_url if final_url != url else None,
                url=url,
                markdown="# Public product content",
                metadata={"title": "Avento"},
                links=links,
            )
        ]


def fake_crawl4ai_module() -> SimpleNamespace:
    return SimpleNamespace(
        AsyncWebCrawler=FakeAsyncWebCrawler,
        BrowserConfig=lambda **kwargs: kwargs,
        CrawlerRunConfig=lambda **kwargs: kwargs,
        CacheMode=SimpleNamespace(BYPASS="bypass"),
    )


@pytest.mark.anyio
async def test_avento_auth_links_are_never_queued_and_do_not_consume_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.crawlers.crawl4ai_adapter.importlib.import_module",
        lambda name: fake_crawl4ai_module(),
    )
    FakeAsyncWebCrawler.redirect_to_auth = False
    crawler = Crawl4AIWebsiteCrawler(
        Settings(
            app_env="testing",
            crawler_min_delay_seconds=0,
            crawler_max_delay_seconds=0,
            _env_file=None,
        ),
        CrawlPolicy(PublicResolver()),
    )

    result = await crawler.crawl(
        CrawlRequest(
            url="https://avento-ai.vercel.app/",
            organization_id="org-test",
            company_profile_id="company-test",
            source_id="source-test",
            max_depth=1,
            max_pages=3,
            max_retries=0,
        )
    )

    assert [page.final_url for page in result.pages] == [
        "https://avento-ai.vercel.app",
        "https://avento-ai.vercel.app/pricing",
        "https://avento-ai.vercel.app/features",
    ]
    assert not any(
        "sign-in" in url or "sign-up" in url for url in FakeAsyncWebCrawler.requested_urls
    )
    assert len(result.pages) == 3


@pytest.mark.anyio
async def test_public_page_redirecting_to_auth_path_is_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.crawlers.crawl4ai_adapter.importlib.import_module",
        lambda name: fake_crawl4ai_module(),
    )
    FakeAsyncWebCrawler.redirect_to_auth = True
    crawler = Crawl4AIWebsiteCrawler(
        Settings(app_env="testing", _env_file=None),
        CrawlPolicy(PublicResolver()),
    )

    result = await crawler.crawl(
        CrawlRequest(
            url="https://avento-ai.vercel.app/",
            organization_id="org-test",
            company_profile_id="company-test",
            source_id="source-test",
            max_depth=0,
            max_pages=1,
            max_retries=0,
        )
    )

    assert result.pages == []
    assert result.errors[0].code == CrawlErrorCode.INVALID_CRAWL_TARGET
    assert result.errors[0].url == "https://avento-ai.vercel.app/sign-in"
