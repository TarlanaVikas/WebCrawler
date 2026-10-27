"""SSRF protection for the crawler.

Layers, in order of enforcement:

1. ``validate_url``      - syntax and literal-address checks. Run on the start URL,
                           on every redirect target and on every discovered link.
2. ``SafeResolver``      - an aiohttp resolver that validates every address a hostname
                           resolves to *at connection time*. aiohttp then connects to the
                           validated address, so a DNS answer cannot change between the
                           check and the connection (DNS rebinding).
3. Manual redirects      - redirects are never followed by aiohttp; the crawler validates
                           each hop through layers 1 and 2 (see ``backend/fetcher.py``).

``resolve_public`` offers the same hostname check up front so the API can reject a bad
start URL immediately with a readable reason.

Crawling loopback/private networks is refused unless ``allow_private_networks`` is set in
``Settings`` (development and tests only).
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Callable, List, Optional, Union

from aiohttp.abc import AbstractResolver

from backend.config import Settings, settings as default_settings
from backend.urls import DEFAULT_PORTS, normalize_url, split_url

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

MAX_URL_LENGTH = 2048
BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata", "metadata.google.internal", "instance-data", "kubernetes.default"})
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa", ".svc", ".cluster.local")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]+|[0-9]+)$", re.IGNORECASE)


class BlockedURLError(ValueError):
    """Raised when a URL is malformed or points somewhere the crawler must not go."""


def ip_is_public(ip: Union[str, IPAddress]) -> bool:
    """True only for globally routable unicast addresses.

    Addresses that embed an IPv4 address (IPv4-mapped, 6to4, Teredo, NAT64) are judged by
    the embedded address, so ``::ffff:127.0.0.1`` and ``64:ff9b::7f00:1`` are not public.
    """
    if isinstance(ip, str):
        try:
            ip = ipaddress.ip_address(ip.split("%", 1)[0])
        except ValueError:
            return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip_is_public(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return ip_is_public(ip.sixtofour) and not ip.is_private
        if ip.teredo is not None:
            server, client = ip.teredo
            return ip_is_public(server) and ip_is_public(client)
        if ip in _NAT64:
            return ip_is_public(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    if (
        ip.is_multicast
        or ip.is_unspecified
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
    ):
        return False
    return bool(ip.is_global)


def _is_ambiguous_numeric_host(host: str) -> bool:
    """Hosts such as ``2130706433`` or ``0x7f.1`` are read as IPv4 by inet_aton."""
    labels = host.split(".")
    if not all(_NUMERIC_LABEL.match(label) for label in labels):
        return False
    try:
        ipaddress.IPv4Address(host)
        return False  # a strict dotted quad is handled as a normal IP literal
    except ValueError:
        return True


@dataclass(frozen=True)
class Target:
    url: str
    scheme: str
    host: str
    port: int
    is_ip_literal: bool


def validate_url(url: str, *, allow_private: Optional[bool] = None, cfg: Settings = default_settings) -> Target:
    """Validate ``url`` syntactically and return its normalized form.

    Raises ``BlockedURLError`` with a human-readable reason. Hostnames are checked against
    the denylist here; the addresses they resolve to are checked by ``SafeResolver``.
    """
    allow = cfg.allow_private_networks if allow_private is None else allow_private
    if not isinstance(url, str) or not url.strip():
        raise BlockedURLError("Enter a URL to crawl")
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise BlockedURLError(f"URL is longer than {MAX_URL_LENGTH} characters")
    parts = split_url(url)
    if parts is None:
        raise BlockedURLError("The URL is malformed")
    if parts.scheme.lower() not in DEFAULT_PORTS:
        raise BlockedURLError("Only http:// and https:// URLs can be crawled")
    try:
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise BlockedURLError("The URL has an invalid host or port") from None
    if not hostname:
        raise BlockedURLError("The URL has no host name")
    if parts.username or parts.password:
        raise BlockedURLError("URLs that contain credentials are not allowed")
    normalized = normalize_url(url)
    if normalized is None:
        raise BlockedURLError("The URL could not be normalized")
    norm_parts = split_url(normalized)
    assert norm_parts is not None
    host = norm_parts.hostname or ""
    scheme = norm_parts.scheme
    port = norm_parts.port or DEFAULT_PORTS[scheme]

    literal: Optional[IPAddress] = None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass

    if not allow:
        if literal is not None:
            if not ip_is_public(literal):
                raise BlockedURLError(f"{host} is a private, loopback or reserved address")
        else:
            if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
                raise BlockedURLError(f"{host} is an internal host name")
            if _is_ambiguous_numeric_host(host):
                raise BlockedURLError("Numeric host names that are not plain IPv4 addresses are not allowed")
            if "." not in host:
                raise BlockedURLError("Single-label host names are not allowed")
    return Target(url=normalized, scheme=scheme, host=host, port=port, is_ip_literal=literal is not None)


GetAddrInfo = Callable[..., "asyncio.Future"]


class SafeResolver(AbstractResolver):
    """aiohttp resolver that only returns public addresses.

    Every address returned by DNS must be public; if any is not, the hostname is refused.
    aiohttp connects to exactly the addresses returned here.
    """

    def __init__(self, allow_private: bool = False, getaddrinfo: Optional[GetAddrInfo] = None):
        self._allow_private = allow_private
        self._getaddrinfo = getaddrinfo

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC) -> List[dict]:
        getaddrinfo = self._getaddrinfo or asyncio.get_running_loop().getaddrinfo
        infos = await getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        results = []
        for fam, _type, _proto, _canon, sockaddr in infos:
            ip = sockaddr[0]
            if not self._allow_private and not ip_is_public(ip):
                raise BlockedURLError(f"{host} resolves to a private or reserved address ({ip})")
            results.append(
                {
                    "hostname": host,
                    "host": ip,
                    "port": port,
                    "family": fam,
                    "proto": 0,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        if not results:
            raise OSError(f"No addresses found for {host}")
        return results

    async def close(self) -> None:  # pragma: no cover - nothing to release
        return None


async def resolve_public(host: str, port: int, *, allow_private: bool = False, getaddrinfo: Optional[GetAddrInfo] = None) -> List[str]:
    """Resolve ``host`` and make sure every answer is public. Used for early feedback."""
    resolver = SafeResolver(allow_private=allow_private, getaddrinfo=getaddrinfo)
    try:
        records = await resolver.resolve(host, port)
    except socket.gaierror as exc:
        raise BlockedURLError(f"Could not resolve {host}") from exc
    return [r["host"] for r in records]


def is_blocked_error(exc: BaseException) -> bool:
    """True when ``exc`` or one of its causes is a ``BlockedURLError``."""
    seen = 0
    while exc is not None and seen < 6:
        if isinstance(exc, BlockedURLError):
            return True
        exc = exc.__cause__ or exc.__context__  # type: ignore[assignment]
        seen += 1
    return False
