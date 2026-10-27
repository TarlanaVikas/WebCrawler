"""HTML parsing: metadata, headings, outgoing links and word count."""
from __future__ import annotations

import re
from typing import Optional, Union
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from backend.urls import normalize_url

_WS = re.compile(r"\s+")
_WORD = re.compile(r"\w+")
_SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "sms:", "ftp:", "file:", "blob:")

MAX_TITLE = 500
MAX_META_VALUE = 500
MAX_META_TAGS = 60
MAX_HEADING = 300
HEADINGS_PER_LEVEL = 5


def _clean(text: Optional[str], limit: int) -> str:
    if not text:
        return ""
    return _WS.sub(" ", text).strip()[:limit]


def parse_html(
    url: str,
    html: Union[bytes, str],
    *,
    charset: Optional[str] = None,
    max_links: int = 500,
) -> dict:
    """Parse ``html`` fetched from ``url``.

    ``html`` may be raw bytes (preferred - BeautifulSoup detects the encoding) or text.
    Relative links are resolved against ``<base href>`` when present, otherwise ``url``.
    """
    if isinstance(html, bytes):
        soup = BeautifulSoup(html, "html.parser", from_encoding=charset)
        size_bytes = len(html)
    else:
        soup = BeautifulSoup(html, "html.parser")
        size_bytes = len(html.encode("utf-8", errors="replace"))

    title = _clean(soup.title.get_text() if soup.title else "", MAX_TITLE)

    meta_tags: dict = {}
    for tag in soup.find_all("meta"):
        name = tag.get("name") or tag.get("property")
        content = tag.get("content")
        if name and content and len(meta_tags) < MAX_META_TAGS:
            meta_tags[_clean(str(name), 100)] = _clean(str(content), MAX_META_VALUE)

    description = meta_tags.get("description") or meta_tags.get("og:description") or ""

    headings: dict = {}
    for level in ("h1", "h2", "h3", "h4", "h5", "h6"):
        found = [_clean(h.get_text(), MAX_HEADING) for h in soup.find_all(level)]
        found = [h for h in found if h]
        if found:
            headings[level] = found[:HEADINGS_PER_LEVEL]

    base = url
    base_tag = soup.find("base", href=True)
    if base_tag:
        try:
            candidate = urljoin(url, str(base_tag["href"]).strip())
        except ValueError:
            candidate = None
        if candidate and normalize_url(candidate):
            base = candidate

    links: list = []
    seen: set = set()
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.startswith("#") or href.lower().startswith(_SKIP_SCHEMES):
            continue
        try:
            absolute = urljoin(base, href)
        except ValueError:
            continue
        clean = normalize_url(absolute)
        if clean and clean not in seen:
            seen.add(clean)
            links.append(clean)
            if len(links) >= max_links:
                break

    for node in soup(["script", "style", "noscript", "template"]):
        node.decompose()
    word_count = len(_WORD.findall(soup.get_text(separator=" ", strip=True)))

    return {
        "url": url,
        "title": title,
        "description": description,
        "meta_tags": meta_tags,
        "headings": headings,
        "links": links,
        "word_count": word_count,
        "size_bytes": size_bytes,
    }
