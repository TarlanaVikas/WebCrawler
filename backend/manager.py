"""Owns the single active crawl and connects it to the log broker."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional

from backend import database as db
from backend.config import CrawlerConfig, Settings, settings as default_settings
from backend.crawler import WebCrawlerEngine
from backend.events import LogBroker

logger = logging.getLogger("webcrawler")


class CrawlInProgress(Exception):
    def __init__(self, session_id: str):
        super().__init__("A crawl is already running")
        self.session_id = session_id


class CrawlManager:
    """Runs one crawl at a time. The most recent engine is kept so status stays readable."""

    def __init__(self, broker: LogBroker, cfg: Settings = default_settings):
        self.broker = broker
        self.cfg = cfg
        self.engine: Optional[WebCrawlerEngine] = None
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def active(self) -> Optional[WebCrawlerEngine]:
        return self.engine if self.engine and self.engine.is_running else None

    async def start(self, config: CrawlerConfig) -> WebCrawlerEngine:
        """Validate and start a crawl.

        Raises ``CrawlInProgress``, ``ValueError`` (limits) or ``BlockedURLError``.
        """
        async with self._lock:
            if self.active:
                raise CrawlInProgress(self.active.session_id)
            config.check_limits(self.cfg)
            session_id = uuid.uuid4().hex[:8]

            def sink(level: str, message: str) -> None:
                self.broker.publish(session_id, level, message)

            engine = WebCrawlerEngine(config, log_callback=sink, cfg=self.cfg, session_id=session_id)
            await engine.prepare()  # raises BlockedURLError for unsafe or unresolvable URLs
            self.engine = engine
            self._task = asyncio.create_task(engine.run(), name=f"crawl-{session_id}")
            self._task.add_done_callback(self._on_done)
            return engine

    @staticmethod
    def _on_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:  # run() handles its own errors; this is a last line of defence
            logger.error("crawl task ended with an unhandled error", exc_info=exc)

    def stop(self) -> bool:
        engine = self.active
        if not engine:
            return False
        engine.stop()
        return True

    def status(self) -> Optional[dict]:
        return self.engine.snapshot() if self.engine else None

    async def shutdown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
