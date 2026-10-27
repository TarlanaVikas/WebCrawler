"""Pydantic models describing the API's responses (they drive the OpenAPI schema)."""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class PageSummary(_Open):
    id: int
    session_id: Optional[str] = None
    url: str
    status_code: Optional[int] = 0
    title: Optional[str] = ""
    description: Optional[str] = ""
    content_type: Optional[str] = ""
    response_time: Optional[float] = 0.0
    size_bytes: Optional[int] = 0
    word_count: Optional[int] = 0
    depth: int = 0
    redirect_url: Optional[str] = None
    error: Optional[str] = None
    outlinks_count: int = 0


class PageDetail(PageSummary):
    links: List[str] = Field(default_factory=list)
    meta_tags: Dict[str, str] = Field(default_factory=dict)
    headings: Dict[str, List[str]] = Field(default_factory=dict)


class ResultsResponse(_Open):
    pages: List[PageSummary]
    count: int
    total: int
    limit: int
    offset: int


class CrawlStatus(_Open):
    session_id: Optional[str] = None
    start_url: Optional[str] = None
    status: str = "idle"
    is_running: bool = False
    pages_crawled: int = 0
    error_count: int = 0
    skipped_count: int = 0
    discovered_count: int = 0
    queued: int = 0
    active: int = 0
    elapsed: float = 0.0
    pages_per_second: float = 0.0
    max_pages: Optional[int] = None
    max_depth: Optional[int] = None
    stop_reason: Optional[str] = None
    error_message: Optional[str] = None


class StartResponse(BaseModel):
    status: str = "started"
    session_id: str
    start_url: str


class StopResponse(BaseModel):
    status: str


class GraphNode(_Open):
    id: str
    page_id: Optional[int] = None
    label: str
    status: Optional[int] = 0
    depth: int = 0
    outlinks: int = 0


class GraphEdge(BaseModel):
    source: str
    target: str


class GraphResponse(BaseModel):
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    total_nodes: int
    truncated: bool


class SessionOut(_Open):
    session_id: str
    start_url: str
    status: str
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    pages_crawled: int = 0
    error_count: int = 0
    skipped_count: int = 0
    discovered_count: int = 0
    error_message: Optional[str] = None
    config: Optional[dict] = None


class SessionDetail(SessionOut):
    summary: Dict[str, float] = Field(default_factory=dict)
    live: Optional[CrawlStatus] = None


class SessionList(BaseModel):
    sessions: List[SessionOut]
    total: int
    limit: int
    offset: int


class Health(BaseModel):
    status: str
    name: str
    version: str
