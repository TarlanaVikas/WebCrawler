"""URL normalization and crawl-scope helpers."""
from __future__ import annotations

from typing import Optional
from urllib.parse import SplitResult, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}

# Resources that are never HTML pages. Skipping them avoids pointless requests.
BINARY_EXTENSIONS = frozenset(
    """
    png jpg jpeg gif webp svg ico bmp tif tiff avif heic
    mp3 mp4 m4a wav ogg webm mov avi mkv flac
    zip gz tgz bz2 xz 7z rar tar dmg iso exe msi apk deb rpm
    pdf doc docx xls xlsx ppt pptx odt ods csv
    woff woff2 ttf otf eot
    css js map json xml rss atom
    """.split()
)


def _ascii_host(hostname: str) -> Optional[str]:
    host = hostname.strip().rstrip(".").lower()
    if not host:
        return None
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def split_url(url: str) -> Optional[SplitResult]:
    try:
        return urlsplit(url.strip())
    except ValueError:
        return None


def normalize_url(url: str) -> Optional[str]:
    """Return a canonical http(s) URL, or ``None`` when the URL is unusable.

    Lowercases scheme and host, applies IDNA, drops default ports and fragments and
    gives an empty path the value ``/`` so equivalent URLs deduplicate.
    """
    parts = split_url(url)
    if parts is None:
        return None
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        return None
    try:
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not hostname:
        return None
    host = _ascii_host(hostname)
    if not host:
        return None
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host
    if port and port != DEFAULT_PORTS[scheme]:
        netloc = f"{host}:{port}"
    userinfo = ""
    if parts.username:
        userinfo = parts.username + (f":{parts.password}" if parts.password else "") + "@"
    path = parts.path or "/"
    return urlunsplit((scheme, userinfo + netloc, path, parts.query, ""))


def host_of(url: str) -> str:
    parts = split_url(url)
    if parts is None:
        return ""
    try:
        return _ascii_host(parts.hostname or "") or ""
    except ValueError:
        return ""


def site_key(host: str) -> str:
    """Host used for scope comparison: ``www.example.com`` and ``example.com`` match."""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def in_scope(url: str, start_url: str) -> bool:
    return site_key(host_of(url)) == site_key(host_of(start_url))


def looks_binary(url: str) -> bool:
    parts = split_url(url)
    if parts is None:
        return True
    last = parts.path.rsplit("/", 1)[-1]
    if "." not in last:
        return False
    return last.rsplit(".", 1)[-1].lower() in BINARY_EXTENSIONS
