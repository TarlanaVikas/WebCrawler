"""Configuration for Web Crawler.

Two kinds of configuration live here:

* ``Settings``  - server-side settings read from the environment (and an optional
  ``.env`` file). These include the hard limits that no API client can raise.
* ``CrawlerConfig`` - the per-crawl options a client sends to ``/api/crawl/start``.
  Every field is bounded by ``Settings`` so a request can never exceed the limits
  the operator configured.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field, field_validator

APP_NAME = "Web Crawler"
APP_SLUG = "web-crawler"
APP_VERSION = "1.0.0"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"


def _load_dotenv(path: Path) -> None:
    """Minimal ``.env`` reader. Real environment variables always win."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> List[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: str = str(PROJECT_ROOT / "crawler.db")
    cors_origins: List[str] = field(default_factory=list)
    allowed_hosts: List[str] = field(default_factory=lambda: ["localhost", "127.0.0.1", "[::1]"])

    # Safety. Crawling loopback/private networks is refused unless this is switched
    # on explicitly. It exists for local development and the automated tests only.
    allow_private_networks: bool = False

    # Hard limits (the API clamps requests to these values)
    max_depth_cap: int = 8
    max_pages_cap: int = 1000
    max_concurrency_cap: int = 16
    min_crawl_delay: float = 0.1
    max_crawl_delay: float = 10.0
    max_runtime_seconds: int = 1800

    # Network behaviour
    connect_timeout: float = 8.0
    request_timeout: float = 20.0
    max_response_bytes: int = 2 * 1024 * 1024
    max_robots_bytes: int = 512 * 1024
    max_redirects: int = 5
    max_links_per_page: int = 500
    max_discovered_urls: int = 20000
    user_agent: str = f"WebCrawler/{APP_VERSION} (+local crawler; respects robots.txt)"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv(PROJECT_ROOT / ".env")
        defaults = cls()
        allowed = defaults.allowed_hosts + _env_list("WEBCRAWLER_ALLOWED_HOSTS")
        return cls(
            host=os.environ.get("WEBCRAWLER_HOST", defaults.host),
            port=_env_int("WEBCRAWLER_PORT", defaults.port, 1, 65535),
            db_path=os.environ.get("WEBCRAWLER_DB_PATH", defaults.db_path),
            cors_origins=_env_list("WEBCRAWLER_CORS_ORIGINS"),
            allowed_hosts=sorted(set(allowed)),
            allow_private_networks=_env_bool("WEBCRAWLER_ALLOW_PRIVATE_NETWORKS", False),
            max_depth_cap=_env_int("WEBCRAWLER_MAX_DEPTH", defaults.max_depth_cap, 1, 20),
            max_pages_cap=_env_int("WEBCRAWLER_MAX_PAGES", defaults.max_pages_cap, 1, 10000),
            max_concurrency_cap=_env_int("WEBCRAWLER_MAX_CONCURRENCY", defaults.max_concurrency_cap, 1, 64),
            min_crawl_delay=_env_float("WEBCRAWLER_MIN_DELAY", defaults.min_crawl_delay, 0.0, 10.0),
            max_runtime_seconds=_env_int("WEBCRAWLER_MAX_RUNTIME", defaults.max_runtime_seconds, 10, 86400),
            max_response_bytes=_env_int("WEBCRAWLER_MAX_RESPONSE_BYTES", defaults.max_response_bytes, 10_000, 50_000_000),
            user_agent=os.environ.get("WEBCRAWLER_USER_AGENT", defaults.user_agent)[:200],
        )

    def public_limits(self) -> dict:
        """Limits and defaults that are safe to show to the UI."""
        return {
            "max_depth": {"min": 0, "max": self.max_depth_cap, "default": 2},
            "max_pages": {"min": 1, "max": self.max_pages_cap, "default": 50},
            "concurrency": {"min": 1, "max": self.max_concurrency_cap, "default": 4},
            "crawl_delay": {"min": self.min_crawl_delay, "max": self.max_crawl_delay, "default": max(0.5, self.min_crawl_delay)},
            "max_runtime_seconds": self.max_runtime_seconds,
            "max_response_bytes": self.max_response_bytes,
            "request_timeout": self.request_timeout,
            "max_redirects": self.max_redirects,
            "max_links_per_page": self.max_links_per_page,
            "allow_private_networks": self.allow_private_networks,
            "user_agent": self.user_agent,
        }


settings = Settings.from_env()


_UA_RE = re.compile(r"^[\x20-\x7e]{1,200}$")


class CrawlerConfig(BaseModel):
    """Options for one crawl. Out-of-range values are rejected, not silently kept."""

    start_url: str = Field(default="https://example.com", max_length=2048)
    max_depth: int = Field(default=2, ge=0)
    max_pages: int = Field(default=50, ge=1)
    concurrency: int = Field(default=4, ge=1)
    stay_on_domain: bool = True
    respect_robots: bool = True
    crawl_delay: float = Field(default=0.5, ge=0)
    user_agent: str | None = Field(default=None, description="Optional override of the server's User-Agent")

    @field_validator("start_url")
    @classmethod
    def _strip_url(cls, value: str) -> str:
        return value.strip()

    @field_validator("user_agent")
    @classmethod
    def _check_ua(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not _UA_RE.match(value):
            raise ValueError("user_agent must be 1-200 printable ASCII characters")
        return value

    def check_limits(self, cfg: Settings = settings) -> None:
        """Raise ``ValueError`` when the request exceeds the server's hard limits."""
        problems = []
        if self.max_depth > cfg.max_depth_cap:
            problems.append(f"max_depth must be at most {cfg.max_depth_cap}")
        if self.max_pages > cfg.max_pages_cap:
            problems.append(f"max_pages must be at most {cfg.max_pages_cap}")
        if self.concurrency > cfg.max_concurrency_cap:
            problems.append(f"concurrency must be at most {cfg.max_concurrency_cap}")
        if self.crawl_delay < cfg.min_crawl_delay:
            problems.append(f"crawl_delay must be at least {cfg.min_crawl_delay} seconds")
        if self.crawl_delay > cfg.max_crawl_delay:
            problems.append(f"crawl_delay must be at most {cfg.max_crawl_delay} seconds")
        if problems:
            raise ValueError("; ".join(problems))
