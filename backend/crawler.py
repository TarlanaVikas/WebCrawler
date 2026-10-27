"""The asynchronous crawl engine."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Callable, Dict, Optional, Set, Tuple

from backend import database as db
from backend.config import CrawlerConfig, Settings, settings as default_settings
from backend.fetcher import describe_error, fetch, make_session
from backend.parser import parse_html
from backend.robots import RobotsCache
from backend.security import BlockedURLError, is_blocked_error, resolve_public, validate_url
from backend.urls import host_of, in_scope, looks_binary

logger = logging.getLogger("webcrawler")

LogCallback = Callable[[str, str], None]  # (level, message)


class WebCrawlerEngine:
    """Breadth-first crawler for a single start URL.

    Life cycle: ``await prepare()`` validates the start URL and creates the session row,
    ``await run()`` performs the crawl, ``stop()`` ends it early.
    """

    def __init__(
        self,
        config: CrawlerConfig,
        log_callback: Optional[LogCallback] = None,
        cfg: Settings = default_settings,
        session_id: Optional[str] = None,
    ):
        self.config = config
        self.cfg = cfg
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.start_url = config.start_url
        self.user_agent = config.user_agent or cfg.user_agent
        self._log_callback = log_callback or (lambda level, msg: logger.info(msg))

        self.status = "pending"  # pending, running, completed, stopped, failed
        self.error_message: Optional[str] = None
        self.stop_reason: Optional[str] = None
        self.pages_crawled = 0
        self.error_count = 0
        self.skipped_count = 0
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None

        self._queue: "asyncio.Queue[Tuple[str, int]]" = asyncio.Queue()
        self._visited: Set[str] = set()
        self._active = 0
        self._done = asyncio.Event()
        self._stop_requested = False
        self._next_slot: Dict[str, float] = {}
        self._robots: Optional[RobotsCache] = None

    # ------------------------------------------------------------------ state

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    def snapshot(self) -> dict:
        end = self.finished_at or time.monotonic()
        elapsed = (end - self.started_at) if self.started_at else 0.0
        return {
            "session_id": self.session_id,
            "start_url": self.start_url,
            "status": self.status,
            "is_running": self.is_running,
            "pages_crawled": self.pages_crawled,
            "error_count": self.error_count,
            "skipped_count": self.skipped_count,
            "discovered_count": len(self._visited),
            "queued": self._queue.qsize(),
            "active": self._active,
            "elapsed": round(elapsed, 2),
            "pages_per_second": round(self.pages_crawled / elapsed, 2) if elapsed > 0.5 else 0.0,
            "max_pages": self.config.max_pages,
            "max_depth": self.config.max_depth,
            "stop_reason": self.stop_reason,
            "error_message": self.error_message,
        }

    def log(self, message: str, level: str = "info") -> None:
        try:
            self._log_callback(level, message)
        except Exception:  # a broken log sink must never break the crawl
            logger.exception("log callback failed")

    # ------------------------------------------------------------------ life cycle

    async def prepare(self) -> None:
        """Validate the start URL (including DNS) and create the session record.

        Raises ``BlockedURLError`` with a user-readable message.
        """
        target = validate_url(self.config.start_url, cfg=self.cfg)
        if not target.is_ip_literal:
            await resolve_public(target.host, target.port, allow_private=self.cfg.allow_private_networks)
        self.start_url = target.url
        await asyncio.to_thread(
            db.save_session, self.session_id, self.start_url, "running", self.config.model_dump()
        )
        self.status = "running"
        self.started_at = time.monotonic()

    def stop(self) -> None:
        if self.is_running and not self._stop_requested:
            self._stop_requested = True
            self.stop_reason = "stopped by user"
            self.log("Stop requested; finishing requests already in flight", "system")
            self._done.set()

    async def run(self) -> None:
        if self.status == "pending":
            await self.prepare()
        final = "completed"
        persist_error: Optional[str] = None
        self.log(f"Crawl {self.session_id} started at {self.start_url}", "system")
        try:
            async with make_session(self.cfg, self.user_agent, self.config.concurrency) as http:
                self._robots = RobotsCache(http, self.user_agent, self.cfg, log=self.log)
                self._visited.add(self.start_url)
                self._queue.put_nowait((self.start_url, 0))
                workers = [asyncio.create_task(self._worker(http)) for _ in range(self.config.concurrency)]
                try:
                    await asyncio.wait_for(self._done.wait(), timeout=self.cfg.max_runtime_seconds)
                except asyncio.TimeoutError:
                    self.stop_reason = "time limit"
                    self.log(f"Time limit of {self.cfg.max_runtime_seconds}s reached; ending the crawl", "warn")
                finally:
                    for worker in workers:
                        worker.cancel()
                    await asyncio.gather(*workers, return_exceptions=True)
            if self._stop_requested:
                final = "stopped"
            elif self.stop_reason is None:
                self.stop_reason = "page limit" if self.pages_crawled >= self.config.max_pages else "no more links"
        except asyncio.CancelledError:
            final = "stopped"
            self.stop_reason = "server shutting down"
            raise
        except Exception as exc:
            final = "failed"
            persist_error = describe_error(exc, self.cfg)
            self.error_message = persist_error
            logger.exception("crawl %s failed", self.session_id)
            self.log(f"Crawl failed: {persist_error}", "error")
        finally:
            self.status = final
            self.finished_at = time.monotonic()
            try:
                db.update_session_status(
                    self.session_id, final, self.pages_crawled, self.error_count,
                    self.skipped_count, len(self._visited), persist_error,
                )
            except Exception:
                logger.exception("could not persist final state of %s", self.session_id)
            self.log(
                f"Crawl {self.session_id} {final}: {self.pages_crawled} pages, {self.error_count} errors, "
                f"{self.skipped_count} skipped" + (f" ({self.stop_reason})" if self.stop_reason else ""),
                "system",
            )

    # ------------------------------------------------------------------ workers

    def _check_done(self) -> None:
        if self._queue.empty() and self._active == 0:
            self._done.set()

    async def _worker(self, http) -> None:
        while True:
            url, depth = await self._queue.get()
            try:
                if self.pages_crawled + self._active >= self.config.max_pages:
                    if self._active == 0:
                        self._done.set()  # budget spent and nothing in flight can free it
                    else:
                        # An in-flight request may still be skipped and release its slot.
                        self._queue.put_nowait((url, depth))
                        await asyncio.sleep(0.05)
                    continue
                self._active += 1
                try:
                    await self._process(http, url, depth)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("unexpected error while crawling %s", url)
                    await self._record_error(url, depth, f"Internal error: {exc.__class__.__name__}", 0.0)
                finally:
                    self._active -= 1
            finally:
                self._queue.task_done()
                self._check_done()

    async def _throttle(self, host: str, delay: float) -> None:
        """Space requests to the same host at least ``delay`` seconds apart."""
        now = time.monotonic()
        slot = max(now, self._next_slot.get(host, 0.0))
        self._next_slot[host] = slot + delay
        if slot > now:
            await asyncio.sleep(slot - now)

    def _scope_check(self) -> Optional[Callable[[str], bool]]:
        if not self.config.stay_on_domain:
            return None
        start = self.start_url
        return lambda candidate: in_scope(candidate, start)

    async def _process(self, http, url: str, depth: int) -> None:
        delay = self.config.crawl_delay
        if self.config.respect_robots and self._robots is not None:
            decision = await self._robots.check(url)
            if not decision.allowed and decision.unreachable:
                await self._record_error(url, depth, decision.note or "robots.txt unreachable", 0.0)
                return
            if not decision.allowed:
                self.skipped_count += 1
                self.log(f"Skipped ({decision.note or 'robots.txt'}): {url}", "warn")
                return
            if decision.delay:
                delay = max(delay, min(decision.delay, self.cfg.max_crawl_delay))

        await self._throttle(host_of(url), delay)
        if self._stop_requested:
            return

        started = time.monotonic()
        try:
            result = await fetch(http, url, cfg=self.cfg, in_scope=self._scope_check())
        except BlockedURLError as exc:
            await self._record_error(url, depth, f"Blocked: {exc}", time.monotonic() - started)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reason = f"Blocked: {exc.__cause__ or exc}" if is_blocked_error(exc) else describe_error(exc, self.cfg)
            await self._record_error(url, depth, reason, time.monotonic() - started)
            return

        for hop in result.redirects:
            self._visited.add(hop)
        if result.final_url != url:
            self._visited.add(result.final_url)

        if result.blocked_redirect:
            await self._record_page(
                {
                    "url": url, "status_code": result.status, "content_type": result.content_type,
                    "response_time": round(result.elapsed, 3), "depth": depth,
                    "redirect_url": result.blocked_redirect, "title": "", "description": "",
                    "error": "Redirects outside the crawl scope; not followed",
                },
                is_error=False,
            )
            self.log(f"Redirect [{result.status}] leaves the site, not followed: {url}", "warn")
            return

        if not result.is_html or result.body is None:
            if 300 <= result.status < 400:
                await self._record_page(
                    {"url": url, "status_code": result.status, "content_type": result.content_type,
                     "response_time": round(result.elapsed, 3), "depth": depth, "title": "",
                     "description": "", "error": "Redirect without a usable Location header"},
                    is_error=False,
                )
                return
            self.skipped_count += 1
            kind = result.content_type.split(";", 1)[0] or "unknown type"
            self.log(f"Skipped non-HTML ({kind}): {url}", "info")
            return

        parsed = await asyncio.to_thread(
            parse_html, result.final_url, result.body, charset=result.charset, max_links=self.cfg.max_links_per_page
        )
        parsed.update(
            url=url,
            status_code=result.status,
            content_type=result.content_type,
            response_time=round(result.elapsed, 3),
            depth=depth,
            redirect_url=result.final_url if result.final_url != url else None,
            error="Response was larger than the size limit and was cut off" if result.truncated else None,
        )
        is_error = result.status >= 400
        await self._record_page(parsed, is_error=is_error)

        level = "warn" if is_error else "info"
        self.log(f"{result.status} {url} ({result.elapsed:.2f}s, depth {depth}, {len(parsed['links'])} links)", level)

        if is_error or depth >= self.config.max_depth:
            return
        self._enqueue_links(parsed["links"], depth + 1)

    # ------------------------------------------------------------------ bookkeeping

    def _enqueue_links(self, links, depth: int) -> None:
        scope = self._scope_check()
        for link in links:
            if link in self._visited:
                continue
            if scope is not None and not scope(link):
                continue
            if looks_binary(link):
                continue
            if len(self._visited) >= self.cfg.max_discovered_urls:
                break
            try:
                validate_url(link, cfg=self.cfg)
            except BlockedURLError:
                self.skipped_count += 1
                self._visited.add(link)
                continue
            self._visited.add(link)
            self._queue.put_nowait((link, depth))

    async def _record_page(self, page: dict, is_error: bool) -> None:
        await asyncio.to_thread(db.save_page_record, self.session_id, page)
        self.pages_crawled += 1
        if is_error or page.get("status_code", 0) >= 400:
            self.error_count += 1
        if self.pages_crawled % 5 == 0:
            await asyncio.to_thread(
                db.update_session_stats, self.session_id, self.pages_crawled, self.error_count,
                self.skipped_count, len(self._visited),
            )

    async def _record_error(self, url: str, depth: int, reason: str, elapsed: float) -> None:
        self.log(f"Failed: {url} ({reason})", "error")
        await self._record_page(
            {
                "url": url, "status_code": 0, "content_type": "", "response_time": round(elapsed, 3),
                "size_bytes": 0, "word_count": 0, "depth": depth, "title": "", "description": "",
                "links": [], "meta_tags": {}, "headings": {}, "error": reason,
            },
            is_error=True,
        )
