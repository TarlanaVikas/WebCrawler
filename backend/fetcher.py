"""HTTP fetching with the crawler's safety rules applied to every request.

* Redirects are followed manually (at most ``max_redirects``); every hop is validated by
  ``validate_url`` and connects through ``SafeResolver``.
* Bodies are streamed and cut off at ``max_bytes``.
* Only HTML is read; other content types are reported without downloading the body.
* Proxy environment variables and cookies are ignored on purpose: a proxy would make the
  resolver check meaningless, and cookies would leak state between pages.
"""
from __future__ import annotations

import asyncio
import ssl
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from urllib.parse import urljoin

import aiohttp

from backend.config import Settings, settings as default_settings
from backend.security import SafeResolver, validate_url
from backend.urls import normalize_url

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
# ClientConnectorDNSError only exists in newer aiohttp releases; fall back to an empty tuple.
_DNS_ERRORS = tuple(c for c in (getattr(aiohttp, "ClientConnectorDNSError", None),) if c)
HTML_TYPES = {"text/html", "application/xhtml+xml"}


class FetchError(Exception):
    """A request failed in a way worth showing to the user."""


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str = ""
    charset: Optional[str] = None
    body: Optional[bytes] = None
    truncated: bool = False
    is_html: bool = False
    elapsed: float = 0.0
    redirects: List[str] = field(default_factory=list)
    blocked_redirect: Optional[str] = None  # redirect target that was out of scope


def make_session(cfg: Settings = default_settings, user_agent: Optional[str] = None, concurrency: int = 4) -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(
        resolver=SafeResolver(allow_private=cfg.allow_private_networks),
        limit=max(4, concurrency * 2),
        limit_per_host=max(2, concurrency),
        ttl_dns_cache=30,
        enable_cleanup_closed=False,
    )
    timeout = aiohttp.ClientTimeout(total=cfg.request_timeout, connect=cfg.connect_timeout)
    return aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        headers={
            "User-Agent": user_agent or cfg.user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        },
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
        raise_for_status=False,
    )


async def fetch(
    session: aiohttp.ClientSession,
    url: str,
    *,
    cfg: Settings = default_settings,
    in_scope: Optional[Callable[[str], bool]] = None,
    html_only: bool = True,
    max_bytes: Optional[int] = None,
) -> FetchResult:
    """Fetch ``url``; raises ``BlockedURLError`` (blocked target) or ``FetchError``/aiohttp errors."""
    limit = max_bytes or cfg.max_response_bytes
    started = time.monotonic()
    current = validate_url(url, cfg=cfg).url
    chain: List[str] = []

    for _hop in range(cfg.max_redirects + 1):
        async with session.get(current, allow_redirects=False) as resp:
            if resp.status in REDIRECT_STATUSES:
                location = resp.headers.get("Location")
                if not location:
                    return FetchResult(url, current, resp.status, resp.headers.get("Content-Type", ""),
                                       elapsed=time.monotonic() - started, redirects=chain)
                try:
                    candidate = normalize_url(urljoin(str(resp.url), location.strip()))
                except ValueError:
                    candidate = None
                if not candidate:
                    raise FetchError("Redirected to an invalid URL")
                if in_scope is not None and not in_scope(candidate):
                    return FetchResult(url, current, resp.status, resp.headers.get("Content-Type", ""),
                                       elapsed=time.monotonic() - started, redirects=chain,
                                       blocked_redirect=candidate)
                current = validate_url(candidate, cfg=cfg).url  # raises if the hop is not allowed
                chain.append(current)
                continue

            content_type = resp.headers.get("Content-Type", "")
            mime = content_type.split(";", 1)[0].strip().lower()
            result = FetchResult(url, current, resp.status, content_type, charset=resp.charset,
                                 redirects=chain, is_html=mime in HTML_TYPES)
            if html_only and not result.is_html:
                result.elapsed = time.monotonic() - started
                return result

            chunks: List[bytes] = []
            total = 0
            async for chunk in resp.content.iter_chunked(65536):
                if total + len(chunk) > limit:
                    chunks.append(chunk[: limit - total])
                    result.truncated = True
                    break
                chunks.append(chunk)
                total += len(chunk)
            result.body = b"".join(chunks)
            result.elapsed = time.monotonic() - started
            return result

    raise FetchError(f"Stopped after {cfg.max_redirects} redirects")


def describe_error(exc: BaseException, cfg: Settings = default_settings) -> str:
    """Short, user-readable description of a failed request."""
    if isinstance(exc, asyncio.TimeoutError):
        return f"Timed out after {cfg.request_timeout:g}s"
    if isinstance(exc, aiohttp.ClientConnectorCertificateError):
        return "TLS certificate could not be verified"
    if isinstance(exc, ssl.SSLError) or isinstance(exc, aiohttp.ClientSSLError):
        return "TLS handshake failed"
    if isinstance(exc, _DNS_ERRORS):
        return "Host name could not be resolved"
    if isinstance(exc, aiohttp.ClientConnectorError):
        return "Could not connect to the server"
    if isinstance(exc, aiohttp.ServerDisconnectedError):
        return "Server closed the connection"
    if isinstance(exc, aiohttp.TooManyRedirects):
        return "Too many redirects"
    if isinstance(exc, (aiohttp.ClientPayloadError, aiohttp.ClientResponseError)):
        return "Response could not be read"
    if isinstance(exc, FetchError):
        return str(exc)
    text = str(exc).strip()
    return text[:200] if text else exc.__class__.__name__
