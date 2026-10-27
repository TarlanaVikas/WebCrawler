"""HTTP API and static frontend for Web Crawler."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend import database as db
from backend.config import APP_NAME, APP_SLUG, APP_VERSION, FRONTEND_DIR, CrawlerConfig, settings
from backend.events import LogBroker
from backend.exporter import iter_csv, iter_json
from backend.manager import CrawlInProgress, CrawlManager
from backend.models import (
    CrawlStatus, GraphResponse, Health, PageDetail, ResultsResponse, SessionDetail, SessionList,
    StartResponse, StopResponse,
)
from backend.security import BlockedURLError

SESSION_ID_PATTERN = r"^[0-9a-f]{8}$"
SessionId = Annotated[str, Query(pattern=SESSION_ID_PATTERN, description="8-character crawl session id")]
SessionPath = Annotated[str, Path(pattern=SESSION_ID_PATTERN)]

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "connect-src 'self'; frame-ancestors 'none'"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    interrupted = db.mark_interrupted_sessions()
    broker = LogBroker()
    app.state.broker = broker
    app.state.manager = CrawlManager(broker, settings)
    if interrupted:
        broker.publish(None, "warn", f"{interrupted} crawl(s) from an earlier run were marked as interrupted")
    yield
    await app.state.manager.shutdown()


app = FastAPI(
    title=f"{APP_NAME} API",
    version=APP_VERSION,
    description="Crawl a website from a start URL and inspect the pages, links and metadata that were found.",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


# ------------------------------------------------------------------ guards

def _host_allowed(host_header: str) -> bool:
    if "*" in settings.allowed_hosts:
        return True
    host = host_header.strip().lower()
    if host.startswith("["):  # [::1]:8000
        host = host[: host.find("]") + 1]
    else:
        host = host.split(":", 1)[0]
    return host in {h.lower() for h in settings.allowed_hosts}


@app.middleware("http")
async def guard(request: Request, call_next):
    """Host check (DNS rebinding), same-origin check for writes (CSRF) and security headers."""
    if not _host_allowed(request.headers.get("host", "")):
        return JSONResponse({"detail": "Host not allowed"}, status_code=400)

    if request.method in UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None:
            origin_host = urlsplit(origin).netloc
            same_origin = origin_host == request.headers.get("host", "")
            if not same_origin and origin not in settings.cors_origins:
                return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)

    response = await call_next(request)
    is_docs = request.url.path.startswith("/api/docs")
    response.headers.setdefault("Content-Security-Policy", DOCS_CSP if is_docs else CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/") and not is_docs:
        response.headers.setdefault("Cache-Control", "no-store")
    return response


if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    fields = {}
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query", "path")]
        name = ".".join(loc) or "request"
        message = str(err.get("msg", "Invalid value")).removeprefix("Value error, ")
        fields.setdefault(name, message)
    detail = "; ".join(f"{name}: {msg}" for name, msg in fields.items())
    return JSONResponse({"detail": detail, "code": "invalid_request", "fields": fields}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, exc: Exception):
    """Never leak internals; the traceback is in the server log."""
    logging.getLogger("webcrawler").exception("unhandled error", exc_info=exc)
    return JSONResponse({"detail": "Something went wrong on the server. Check the server log.", "code": "internal_error"}, status_code=500)


def _manager(request: Request) -> CrawlManager:
    return request.app.state.manager


def _error(status: int, detail: str, code: str, **extra) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code, **extra}, status_code=status)


# ------------------------------------------------------------------ meta

@app.get("/api/health", response_model=Health, tags=["meta"])
async def health():
    return {"status": "ok", "name": APP_NAME, "version": APP_VERSION}


@app.get("/api/limits", tags=["meta"], summary="Limits and defaults enforced by the server")
async def limits():
    return settings.public_limits()


# ------------------------------------------------------------------ crawl control

@app.post("/api/crawl/start", response_model=StartResponse, tags=["crawl"])
async def start_crawl(config: CrawlerConfig, request: Request):
    manager = _manager(request)
    try:
        engine = await manager.start(config)
    except CrawlInProgress as exc:
        return _error(409, "A crawl is already running", "crawl_running", session_id=exc.session_id)
    except BlockedURLError as exc:
        return _error(400, str(exc), "url_blocked", fields={"start_url": str(exc)})
    except ValueError as exc:
        return _error(422, str(exc), "limit_exceeded")
    return {"status": "started", "session_id": engine.session_id, "start_url": engine.start_url}


@app.post("/api/crawl/stop", response_model=StopResponse, tags=["crawl"])
async def stop_crawl(request: Request):
    return {"status": "stopping" if _manager(request).stop() else "not_running"}


@app.get("/api/crawl/status", response_model=CrawlStatus, tags=["crawl"])
async def crawl_status(request: Request):
    snapshot = _manager(request).status()
    if snapshot is None:
        return {"status": "idle", "is_running": False}
    return snapshot


# ------------------------------------------------------------------ sessions

@app.get("/api/sessions", response_model=SessionList, tags=["history"])
async def list_sessions(limit: Annotated[int, Query(ge=1, le=200)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    sessions, total = db.list_sessions(limit, offset)
    return {"sessions": sessions, "total": total, "limit": limit, "offset": offset}


@app.get("/api/sessions/{session_id}", response_model=SessionDetail, tags=["history"])
async def get_session(session_id: SessionPath, request: Request):
    session = db.get_session(session_id)
    if not session:
        return _error(404, "That crawl does not exist. It may have been deleted.", "not_found")
    live = None
    engine = _manager(request).engine
    if engine and engine.session_id == session_id:
        live = engine.snapshot()
        session.update(
            status=live["status"], pages_crawled=live["pages_crawled"], error_count=live["error_count"],
            skipped_count=live["skipped_count"], discovered_count=live["discovered_count"],
        )
    session["summary"] = db.get_session_summary(session_id)
    session["live"] = live
    return session


@app.delete("/api/sessions/{session_id}", tags=["history"])
async def delete_session(session_id: SessionPath, request: Request):
    active = _manager(request).active
    if active and active.session_id == session_id:
        return _error(409, "Stop the crawl before deleting it", "crawl_running")
    if not db.delete_session(session_id):
        return _error(404, "That crawl does not exist.", "not_found")
    return {"status": "deleted", "session_id": session_id}


# ------------------------------------------------------------------ results

@app.get("/api/results", response_model=ResultsResponse, tags=["results"])
async def get_results(
    session_id: SessionId,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str, Query(max_length=200)] = "",
    status: Annotated[str, Query(pattern=r"^(all|ok|redirect|error)$")] = "all",
    sort: Annotated[str, Query(pattern=r"^(id|url|status_code|title|response_time|size_bytes|word_count|depth|outlinks_count)$")] = "id",
    order: Annotated[str, Query(pattern=r"^(asc|desc)$")] = "desc",
    full: bool = False,
):
    if not db.get_session(session_id):
        return _error(404, "That crawl does not exist. It may have been deleted.", "not_found")
    flt = None if status == "all" else status
    pages = db.get_session_pages(session_id, limit, offset, search, flt, sort, order, full)
    total = db.count_session_pages(session_id, search, flt)
    return {"pages": pages, "count": len(pages), "total": total, "limit": limit, "offset": offset}


@app.get("/api/pages/{page_id}", response_model=PageDetail, tags=["results"])
async def get_page(page_id: Annotated[int, Path(ge=1)], session_id: SessionId):
    page = db.get_page(page_id, session_id)
    if not page:
        return _error(404, "That page was not found in this crawl.", "not_found")
    return page


@app.get("/api/graph", response_model=GraphResponse, tags=["results"])
async def get_graph(session_id: SessionId, limit: Annotated[int, Query(ge=1, le=3000)] = 1500):
    if not db.get_session(session_id):
        return _error(404, "That crawl does not exist. It may have been deleted.", "not_found")
    return db.get_graph_data(session_id, limit)


@app.get("/api/export", tags=["results"])
async def export_data(session_id: SessionId, format: Annotated[str, Query(pattern=r"^(csv|json)$")] = "csv"):
    if not db.get_session(session_id):
        return _error(404, "That crawl does not exist. It may have been deleted.", "not_found")
    filename = f"{APP_SLUG}-{session_id}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if format == "json":
        return StreamingResponse(iter_json(session_id), media_type="application/json", headers=headers)
    return StreamingResponse(iter_csv(session_id), media_type="text/csv; charset=utf-8", headers=headers)


# ------------------------------------------------------------------ live log (SSE)

@app.get("/api/logs/stream", tags=["crawl"], summary="Server-sent events with crawl log lines")
async def log_stream(
    request: Request,
    session_id: Annotated[Optional[str], Query(pattern=SESSION_ID_PATTERN)] = None,
    last_event_id: Annotated[Optional[str], Header()] = None,
):
    broker: LogBroker = request.app.state.broker
    try:
        after = int(last_event_id) if last_event_id else 0
    except ValueError:
        after = 0

    async def events():
        yield "retry: 3000\n\n"
        async for event in broker.subscribe(session_id, after):
            if await request.is_disconnected():
                break
            if event is None:
                yield ": keep-alive\n\n"
                continue
            payload = {k: event[k] for k in ("id", "ts", "session_id", "level", "message")}
            yield f"id: {event['id']}\nevent: log\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# The frontend is mounted last so the API routes above take precedence.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
