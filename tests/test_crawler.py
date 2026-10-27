import asyncio
import dataclasses

import aiohttp
import pytest

from backend import database as db
from backend.config import CrawlerConfig
from backend.crawler import WebCrawlerEngine
from backend.fetcher import FetchResult, fetch
from backend.security import BlockedURLError
from tests.conftest import HITS


def make(site, dev_settings, **overrides):
    opts = dict(start_url=site["base"] + "/", max_depth=3, max_pages=50, concurrency=3, crawl_delay=0.0)
    opts.update(overrides)
    logs = []
    engine = WebCrawlerEngine(CrawlerConfig(**opts), log_callback=lambda lvl, msg: logs.append((lvl, msg)), cfg=dev_settings)
    return engine, logs


def pages_by_path(engine, site):
    rows = db.get_session_pages(engine.session_id, limit=500, full=True)
    return {r["url"].replace(site["base"], "") or "/": r for r in rows}


async def test_full_crawl_follows_links_and_records_results(site, dev_settings):
    engine, logs = make(site, dev_settings)
    await engine.prepare()
    await engine.run()
    pages = pages_by_path(engine, site)

    assert engine.status == "completed"
    assert {"/", "/a", "/b", "/a/deep", "/a/deeper", "/missing", "/redirect-in", "/redirect-out"} <= set(pages)
    assert pages["/"]["depth"] == 0 and pages["/a/deep"]["depth"] == 2
    assert pages["/a"]["title"] == "<img src=x onerror=alert(1)> A page"  # stored verbatim; escaped on display
    assert pages["/missing"]["status_code"] == 404
    # 404 page recorded but its links are not followed
    assert "/never-followed" not in pages
    # javascript:, mailto:, external host, binary file and non-HTML are not recorded as pages
    assert not any("other.invalid" in u or u.endswith(".pdf") or u.endswith("/nothtml") for u in pages)
    assert engine.skipped_count >= 1  # /nothtml (non-HTML) at least
    # every URL fetched once despite the / <-> /a cycle
    assert site["app"][HITS]["/"] == 1
    session = db.get_session(engine.session_id)
    assert session["status"] == "completed" and session["pages_crawled"] == engine.pages_crawled


async def test_redirect_inside_scope_is_followed_and_recorded(site, dev_settings):
    engine, _ = make(site, dev_settings, max_depth=1)
    await engine.run()
    row = pages_by_path(engine, site)["/redirect-in"]
    assert row["status_code"] == 200
    assert row["redirect_url"].endswith("/a")


async def test_redirect_out_of_scope_is_not_followed(site, dev_settings):
    engine, logs = make(site, dev_settings, max_depth=1)
    await engine.run()
    row = pages_by_path(engine, site)["/redirect-out"]
    assert row["status_code"] == 302 and row["redirect_url"] == "http://example.org/"
    assert "not followed" in row["error"]


async def test_robots_txt_is_respected_and_can_be_disabled(site, dev_settings):
    engine, logs = make(site, dev_settings, max_depth=1)
    await engine.run()
    assert "/private" not in pages_by_path(engine, site)
    assert any("robots" in m.lower() and "/private" in m for _, m in logs)

    engine2, _ = make(site, dev_settings, max_depth=1, respect_robots=False)
    await engine2.run()
    assert "/private" in pages_by_path(engine2, site)


async def test_page_and_depth_limits(site, dev_settings):
    engine, _ = make(site, dev_settings, max_pages=3, concurrency=4)
    await engine.run()
    assert engine.pages_crawled == 3
    assert db.count_session_pages(engine.session_id) == 3

    engine2, _ = make(site, dev_settings, max_depth=0)
    await engine2.run()
    assert engine2.pages_crawled == 1


async def test_stop_terminates_promptly_and_is_persisted(site, dev_settings):
    # /slow never answers within the timeout, keeping workers busy while we stop
    engine, _ = make(site, dev_settings, start_url=site["base"] + "/slow", max_depth=1)
    await engine.prepare()
    task = asyncio.create_task(engine.run())
    await asyncio.sleep(0.3)
    engine.stop()
    await asyncio.wait_for(task, timeout=8)  # the original implementation hung here forever
    assert engine.status == "stopped"
    assert db.get_session(engine.session_id)["status"] == "stopped"


async def test_timeout_is_recorded_as_error_row(site, dev_settings):
    engine, _ = make(site, dev_settings, start_url=site["base"] + "/slow", max_depth=0)
    settings = dataclasses.replace(dev_settings, request_timeout=0.5)
    engine.cfg = settings
    await engine.run()
    row = db.get_session_pages(engine.session_id, full=False)[0]
    assert row["status_code"] == 0 and "Timed out" in row["error"]
    assert engine.error_count == 1 and engine.status == "completed"


async def test_oversized_responses_are_truncated(site, dev_settings):
    small = dataclasses.replace(dev_settings, max_response_bytes=10_000)
    engine, _ = make(site, small, start_url=site["base"] + "/big", max_depth=0)
    await engine.run()
    row = db.get_session_pages(engine.session_id, full=False)[0]
    assert row["size_bytes"] == 10_000
    assert "cut off" in row["error"]


async def test_redirect_loop_is_cut_off(site, dev_settings):
    engine, _ = make(site, dev_settings, start_url=site["base"] + "/loop-a", max_depth=0)
    await engine.run()
    row = db.get_session_pages(engine.session_id, full=False)[0]
    assert row["status_code"] == 0 and "redirects" in row["error"].lower()


async def test_connection_failure_is_recorded(dev_settings):
    engine = WebCrawlerEngine(CrawlerConfig(start_url="http://127.0.0.1:9/", max_depth=0, crawl_delay=0.0), cfg=dev_settings)
    await engine.run()
    row = db.get_session_pages(engine.session_id, full=False)[0]
    assert row["status_code"] == 0 and "connect" in row["error"].lower()


async def test_stay_on_domain_false_still_blocks_private_link_targets(site, dev_settings):
    # With private networks disallowed, links to loopback are never queued
    strict = dataclasses.replace(dev_settings, allow_private_networks=False)
    engine = WebCrawlerEngine(CrawlerConfig(start_url="https://example.com/", stay_on_domain=False), cfg=strict)
    engine._visited.add("https://example.com/")
    engine._enqueue_links(["http://127.0.0.1/admin", "http://169.254.169.254/latest/", "http://10.0.0.1/", "https://example.org/ok"], 1)
    queued = []
    while not engine._queue.empty():
        queued.append(engine._queue.get_nowait()[0])
    assert queued == ["https://example.org/ok"]
    assert engine.skipped_count == 3


# ---- SSRF at fetch time, using stub sessions so no network is needed ----

class _Resp:
    def __init__(self, status, headers=None, url="http://x.test/"):
        self.status, self.headers, self.url = status, headers or {}, url
        self.charset = None
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def iter_chunked(self, n):
        yield b"<html></html>"


class _Session:
    def __init__(self, responses):
        self.responses, self.requested = list(responses), []

    def get(self, url, **kw):
        assert kw.get("allow_redirects") is False, "redirects must be handled manually"
        self.requested.append(url)
        return self.responses.pop(0)


@pytest.mark.parametrize("target", [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8080/admin",
    "http://[::1]/",
    "http://localhost/",
    "http://10.0.0.5/",
    "http://2130706433/",
])
async def test_redirect_to_internal_address_is_blocked_before_connecting(target):
    strict = dataclasses.replace(__import__("backend.config", fromlist=["settings"]).settings, allow_private_networks=False)
    session = _Session([_Resp(302, {"Location": target})])
    with pytest.raises(BlockedURLError):
        await fetch(session, "https://public.example/start", cfg=strict)
    assert session.requested == ["https://public.example/start"]  # the second hop was never requested


async def test_non_html_body_is_not_downloaded(dev_settings):
    session = _Session([_Resp(200, {"Content-Type": "application/pdf"})])
    result = await fetch(session, "http://x.test/doc", cfg=dev_settings)
    assert not result.is_html and result.body is None
