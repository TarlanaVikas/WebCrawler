import time

import pytest

from backend import robots as robots_module
from backend.fetcher import FetchResult
from backend.robots import RobotsCache


def stub_fetch(monkeypatch, status=200, body=b"", error=None, calls=None):
    async def fake(session, url, **kw):
        if calls is not None:
            calls.append(url)
        if error:
            raise error
        return FetchResult(url, url, status, "text/plain", body=body)
    monkeypatch.setattr(robots_module, "fetch", fake)


async def test_rules_and_crawl_delay_are_applied(monkeypatch):
    stub_fetch(monkeypatch, body=b"User-agent: *\nDisallow: /secret\nCrawl-delay: 2\n")
    cache = RobotsCache(None, "WebCrawler/1.0")
    ok = await cache.check("https://x.test/open")
    blocked = await cache.check("https://x.test/secret/page")
    assert ok.allowed and ok.delay == 2.0
    assert not blocked.allowed and "robots" in blocked.note


async def test_agent_specific_group_wins(monkeypatch):
    stub_fetch(monkeypatch, body=b"User-agent: webcrawler\nDisallow: /\n\nUser-agent: *\nDisallow:\n")
    assert not (await RobotsCache(None, "WebCrawler/1.0 (+x)").check("https://x.test/a")).allowed


@pytest.mark.parametrize("status", [404, 403, 410])
async def test_4xx_means_everything_allowed(monkeypatch, status):
    stub_fetch(monkeypatch, status=status)
    assert (await RobotsCache(None, "WebCrawler/1.0").check("https://x.test/a")).allowed


@pytest.mark.parametrize("status", [500, 503])
async def test_5xx_means_disallowed_per_rfc_9309(monkeypatch, status):
    stub_fetch(monkeypatch, status=status)
    logs = []
    decision = await RobotsCache(None, "WebCrawler/1.0", log=lambda l, m: logs.append(m)).check("https://x.test/a")
    assert not decision.allowed and not decision.unreachable
    assert logs and str(status) in logs[0]


async def test_network_failure_is_flagged_unreachable_and_retried_later(monkeypatch):
    calls = []
    stub_fetch(monkeypatch, error=ConnectionError("down"), calls=calls)
    cache = RobotsCache(None, "WebCrawler/1.0")
    first = await cache.check("https://x.test/a")
    assert not first.allowed and first.unreachable
    await cache.check("https://x.test/b")
    assert len(calls) == 1  # negative result is cached briefly
    cache._entries["https://x.test"].expires = time.monotonic() - 1
    stub_fetch(monkeypatch, body=b"User-agent: *\nDisallow:\n", calls=calls)
    assert (await cache.check("https://x.test/a")).allowed
    assert len(calls) == 2


async def test_one_fetch_per_origin_even_when_requests_overlap(monkeypatch):
    import asyncio
    calls = []

    async def slow(session, url, **kw):
        calls.append(url)
        await asyncio.sleep(0.05)
        return FetchResult(url, url, 200, "text/plain", body=b"")

    monkeypatch.setattr(robots_module, "fetch", slow)
    cache = RobotsCache(None, "WebCrawler/1.0")
    await asyncio.gather(*(cache.check(f"https://x.test/{i}") for i in range(8)))
    assert calls == ["https://x.test/robots.txt"]
