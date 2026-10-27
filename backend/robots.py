"""robots.txt handling.

Semantics follow RFC 9309:

* 2xx          - the file is parsed and applied.
* 4xx          - robots.txt is "unavailable": everything is allowed.
* 5xx / errors - robots.txt is "unreachable": the origin is treated as fully disallowed
                 (the crawl log says so, and the user can switch robots handling off).

The file is fetched asynchronously through the same safe fetcher as pages, cached per
origin, and concurrent requests for the same origin share one fetch.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import aiohttp

from backend.config import Settings, settings as default_settings
from backend.fetcher import fetch, describe_error
from backend.security import is_blocked_error


UNREACHABLE_RETRY_SECONDS = 30.0


@dataclass
class RobotsDecision:
    allowed: bool
    delay: Optional[float] = None  # Crawl-delay requested by the site, in seconds
    note: Optional[str] = None
    unreachable: bool = False  # robots.txt could not be fetched at all (network failure)


@dataclass
class _Entry:
    parser: Optional[RobotFileParser]
    disallow_all: bool = False
    note: Optional[str] = None
    unreachable: bool = False
    expires: Optional[float] = None  # monotonic deadline for retrying an unreachable origin


class RobotsCache:
    def __init__(self, session: aiohttp.ClientSession, user_agent: str, cfg: Settings = default_settings,
                 log: Optional[Callable[[str, str], None]] = None):
        self._session = session
        self._ua = user_agent
        self._cfg = cfg
        self._log = log or (lambda level, msg: None)
        self._entries: Dict[str, _Entry] = {}
        self._pending: Dict[str, asyncio.Future] = {}

    @staticmethod
    def origin_of(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    async def _load(self, origin: str) -> _Entry:
        robots_url = f"{origin}/robots.txt"
        try:
            result = await fetch(self._session, robots_url, cfg=self._cfg, html_only=False,
                                 max_bytes=self._cfg.max_robots_bytes)
        except Exception as exc:  # network failure, timeout or blocked target
            reason = describe_error(exc, self._cfg)
            if is_blocked_error(exc):
                reason = f"blocked ({exc})"
            self._log("warn", f"robots.txt for {origin} is unreachable ({reason}); not crawling it without robots rules")
            return _Entry(None, disallow_all=True, note=f"robots.txt unreachable: {reason}", unreachable=True,
                          expires=time.monotonic() + UNREACHABLE_RETRY_SECONDS)

        if 200 <= result.status < 300:
            text = (result.body or b"").decode("utf-8", errors="replace")
            parser = RobotFileParser()
            parser.parse(text.splitlines())
            return _Entry(parser)
        if 400 <= result.status < 500:
            return _Entry(None, note="no robots.txt")
        self._log("warn", f"robots.txt for {origin} returned {result.status}; treating the site as disallowed")
        return _Entry(None, disallow_all=True, note=f"robots.txt returned {result.status}")

    async def _entry(self, origin: str) -> _Entry:
        cached = self._entries.get(origin)
        if cached is not None and (cached.expires is None or time.monotonic() < cached.expires):
            return cached
        pending = self._pending.get(origin)
        if pending is not None:
            return await asyncio.shield(pending)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[origin] = future
        try:
            entry = await self._load(origin)
            self._entries[origin] = entry
            future.set_result(entry)
            return entry
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                future.exception()  # mark as retrieved to avoid "never retrieved" warnings
            raise
        finally:
            self._pending.pop(origin, None)

    async def check(self, url: str) -> RobotsDecision:
        entry = await self._entry(self.origin_of(url))
        if entry.disallow_all:
            return RobotsDecision(False, None, entry.note, unreachable=entry.unreachable)
        if entry.parser is None:
            return RobotsDecision(True, None, entry.note)
        allowed = entry.parser.can_fetch(self._ua, url)
        delay = None
        try:
            raw = entry.parser.crawl_delay(self._ua)
            delay = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            delay = None
        return RobotsDecision(allowed, delay, None if allowed else "disallowed by robots.txt")
