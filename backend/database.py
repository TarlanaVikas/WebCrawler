"""SQLite persistence for Web Crawler.

The schema is backward compatible with databases created by earlier versions: the
original ``sessions``, ``pages`` and ``links`` tables are kept as they were and new
columns are added with ``ALTER TABLE`` the first time ``init_db`` runs.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

from backend.config import settings

# Module-level so tests (and ``configure``) can point the app at another file.
DB_FILE: str = settings.db_path


def configure(path: str) -> None:
    global DB_FILE
    DB_FILE = path


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error and always close it."""
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- schema

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        start_url TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT NOT NULL,
        pages_crawled INTEGER DEFAULT 0,
        error_count INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        url TEXT NOT NULL,
        status_code INTEGER,
        title TEXT,
        description TEXT,
        content_type TEXT,
        response_time REAL,
        size_bytes INTEGER,
        word_count INTEGER,
        links_json TEXT,
        meta_json TEXT,
        headings_json TEXT,
        UNIQUE(session_id, url)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        source_url TEXT NOT NULL,
        target_url TEXT NOT NULL,
        UNIQUE(session_id, source_url, target_url)
    )
    """,
]

# Columns added after the first release: (table, column, definition)
_MIGRATIONS = [
    ("sessions", "config_json", "TEXT"),
    ("sessions", "skipped_count", "INTEGER DEFAULT 0"),
    ("sessions", "discovered_count", "INTEGER DEFAULT 0"),
    ("sessions", "finished_at", "TIMESTAMP"),
    ("sessions", "error_message", "TEXT"),
    ("pages", "depth", "INTEGER DEFAULT 0"),
    ("pages", "redirect_url", "TEXT"),
    ("pages", "error", "TEXT"),
    ("pages", "outlinks_count", "INTEGER"),
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pages_session ON pages(session_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_pages_status ON pages(session_id, status_code)",
    "CREATE INDEX IF NOT EXISTS idx_links_session_source ON links(session_id, source_url)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at)",
]


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db() -> None:
    with get_db() as conn:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:  # e.g. read-only media; the default mode still works
            pass
        for statement in _SCHEMA:
            conn.execute(statement)
        for table, column, definition in _MIGRATIONS:
            if column not in _columns(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        for statement in _INDEXES:
            conn.execute(statement)
        # Legacy rows have no stored outlink count; derive it once from the stored links.
        legacy = conn.execute("SELECT id, links_json FROM pages WHERE outlinks_count IS NULL").fetchall()
        for row in legacy:
            try:
                count = len(json.loads(row["links_json"])) if row["links_json"] else 0
            except (TypeError, ValueError):
                count = 0
            conn.execute("UPDATE pages SET outlinks_count = ? WHERE id = ?", (count, row["id"]))


def mark_interrupted_sessions() -> int:
    """Sessions left in ``running`` by a previous process can never finish; flag them."""
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE sessions SET status = 'interrupted', finished_at = CURRENT_TIMESTAMP WHERE status = 'running'"
        )
        return cur.rowcount


# --------------------------------------------------------------------------- helpers

def iso_utc(value: Optional[str]) -> Optional[str]:
    """SQLite stores ``YYYY-MM-DD HH:MM:SS`` in UTC; return ISO-8601 with a ``Z``."""
    if not value:
        return None
    value = str(value)
    if value.endswith("Z") or "+" in value[10:]:
        return value
    return value.replace(" ", "T") + "Z"


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


STATUS_FILTERS = {
    "ok": "status_code BETWEEN 200 AND 299",
    "redirect": "status_code BETWEEN 300 AND 399",
    "error": "(status_code >= 400 OR status_code = 0 OR status_code IS NULL)",
}

SORT_COLUMNS = {
    "id": "id",
    "url": "url COLLATE NOCASE",
    "status_code": "status_code",
    "title": "title COLLATE NOCASE",
    "response_time": "response_time",
    "size_bytes": "size_bytes",
    "word_count": "word_count",
    "depth": "depth",
    "outlinks_count": "outlinks_count",
}

SUMMARY_COLUMNS = (
    "id, session_id, url, status_code, title, description, content_type, response_time, "
    "size_bytes, word_count, depth, redirect_url, error, COALESCE(outlinks_count, 0) AS outlinks_count"
)


def _session_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["created_at"] = iso_utc(item.get("created_at"))
    item["finished_at"] = iso_utc(item.get("finished_at"))
    raw = item.pop("config_json", None)
    try:
        item["config"] = json.loads(raw) if raw else None
    except ValueError:
        item["config"] = None
    for key in ("pages_crawled", "error_count", "skipped_count", "discovered_count"):
        item[key] = item.get(key) or 0
    return item


def _page_row(row: sqlite3.Row, full: bool) -> Dict[str, Any]:
    item = dict(row)
    if full:
        item["links"] = json.loads(item.pop("links_json") or "[]")
        item["meta_tags"] = json.loads(item.pop("meta_json") or "{}")
        item["headings"] = json.loads(item.pop("headings_json") or "{}")
    item["depth"] = item.get("depth") or 0
    item["outlinks_count"] = item.get("outlinks_count") or (len(item["links"]) if full else 0)
    return item


# --------------------------------------------------------------------------- sessions

def save_session(session_id: str, start_url: str, status: str = "running", config: Optional[dict] = None) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, start_url, status, config_json) VALUES (?, ?, ?, ?)",
            (session_id, start_url, status, json.dumps(config) if config else None),
        )


def update_session_status(
    session_id: str,
    status: str,
    pages_crawled: int = 0,
    error_count: int = 0,
    skipped_count: int = 0,
    discovered_count: int = 0,
    error_message: Optional[str] = None,
) -> None:
    finished = status != "running"
    with get_db() as conn:
        conn.execute(
            """
            UPDATE sessions
               SET status = ?, pages_crawled = ?, error_count = ?, skipped_count = ?, discovered_count = ?,
                   error_message = COALESCE(?, error_message),
                   finished_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE finished_at END
             WHERE session_id = ?
            """,
            (status, pages_crawled, error_count, skipped_count, discovered_count, error_message, finished, session_id),
        )


def update_session_stats(
    session_id: str, pages_crawled: int, error_count: int, skipped_count: int, discovered_count: int
) -> None:
    """Periodic progress update. Only touches rows still ``running`` so that a late write
    can never overwrite the final status."""
    with get_db() as conn:
        conn.execute(
            "UPDATE sessions SET pages_crawled = ?, error_count = ?, skipped_count = ?, discovered_count = ? "
            "WHERE session_id = ? AND status = 'running'",
            (pages_crawled, error_count, skipped_count, discovered_count, session_id),
        )


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return _session_row(row) if row else None


def list_sessions(limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [_session_row(r) for r in rows], total


def delete_session(session_id: str) -> bool:
    with get_db() as conn:
        conn.execute("DELETE FROM links WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM pages WHERE session_id = ?", (session_id,))
        cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        return cur.rowcount > 0


# --------------------------------------------------------------------------- pages

def save_page_record(session_id: str, page_data: dict) -> None:
    links = page_data.get("links") or []
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO pages
            (session_id, url, status_code, title, description, content_type, response_time, size_bytes,
             word_count, links_json, meta_json, headings_json, depth, redirect_url, error, outlinks_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                page_data.get("url"),
                page_data.get("status_code", 0),
                page_data.get("title", ""),
                page_data.get("description", ""),
                page_data.get("content_type", "text/html"),
                page_data.get("response_time", 0.0),
                page_data.get("size_bytes", 0),
                page_data.get("word_count", 0),
                json.dumps(links),
                json.dumps(page_data.get("meta_tags", {})),
                json.dumps(page_data.get("headings", {})),
                page_data.get("depth", 0),
                page_data.get("redirect_url"),
                page_data.get("error"),
                len(links),
            ),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO links (session_id, source_url, target_url) VALUES (?, ?, ?)",
            [(session_id, page_data.get("url"), target) for target in links],
        )


def _where(session_id: str, search: str, status: Optional[str]) -> Tuple[str, list]:
    clause = "session_id = ?"
    params: list = [session_id]
    if search:
        clause += " AND (url LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\')"
        like = f"%{_escape_like(search)}%"
        params.extend([like, like])
    if status in STATUS_FILTERS:
        clause += f" AND {STATUS_FILTERS[status]}"
    return clause, params


def get_session_pages(
    session_id: str,
    limit: int = 100,
    offset: int = 0,
    search: str = "",
    status: Optional[str] = None,
    sort: str = "id",
    order: str = "desc",
    full: bool = True,
) -> List[Dict[str, Any]]:
    clause, params = _where(session_id, search, status)
    column = SORT_COLUMNS.get(sort, "id")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    select = "*" if full else SUMMARY_COLUMNS
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT {select} FROM pages WHERE {clause} ORDER BY {column} {direction}, id {direction} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [_page_row(r, full) for r in rows]


def count_session_pages(session_id: str, search: str = "", status: Optional[str] = None) -> int:
    clause, params = _where(session_id, search, status)
    with get_db() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM pages WHERE {clause}", params).fetchone()[0]


def get_page(page_id: int, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        if session_id:
            row = conn.execute("SELECT * FROM pages WHERE id = ? AND session_id = ?", (page_id, session_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        return _page_row(row, True) if row else None


def iter_session_pages(session_id: str, batch: int = 500) -> Iterator[Dict[str, Any]]:
    """Yield every page of a session in crawl order, ``batch`` rows at a time."""
    last_id = 0
    while True:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM pages WHERE session_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                (session_id, last_id, batch),
            ).fetchall()
        if not rows:
            return
        for row in rows:
            yield _page_row(row, True)
        last_id = rows[-1]["id"]


def get_graph_data(session_id: str, limit: int = 1500) -> Dict[str, Any]:
    with get_db() as conn:
        pages = conn.execute(
            "SELECT id, url, title, status_code, depth, COALESCE(outlinks_count, 0) AS outlinks "
            "FROM pages WHERE session_id = ? ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        total_pages = conn.execute("SELECT COUNT(*) FROM pages WHERE session_id = ?", (session_id,)).fetchone()[0]
        nodes = [
            {
                "id": p["url"],
                "page_id": p["id"],
                "label": p["title"] or p["url"],
                "status": p["status_code"],
                "depth": p["depth"] or 0,
                "outlinks": p["outlinks"],
            }
            for p in pages
        ]
        known = {n["id"] for n in nodes}
        edge_rows = conn.execute(
            "SELECT l.source_url, l.target_url FROM links l "
            "JOIN pages t ON t.session_id = l.session_id AND t.url = l.target_url "
            "WHERE l.session_id = ? LIMIT 20000",
            (session_id,),
        ).fetchall()
        edges = [
            {"source": e["source_url"], "target": e["target_url"]}
            for e in edge_rows
            if e["source_url"] in known and e["target_url"] in known and e["source_url"] != e["target_url"]
        ]
        return {"nodes": nodes, "edges": edges, "total_nodes": total_pages, "truncated": total_pages > len(nodes)}


def get_session_summary(session_id: str) -> Dict[str, Any]:
    """Aggregates computed from the stored pages (works for finished crawls too)."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS pages,
                   SUM(CASE WHEN status_code BETWEEN 200 AND 299 THEN 1 ELSE 0 END) AS ok,
                   SUM(CASE WHEN status_code BETWEEN 300 AND 399 THEN 1 ELSE 0 END) AS redirects,
                   SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS http_errors,
                   SUM(CASE WHEN status_code = 0 OR status_code IS NULL THEN 1 ELSE 0 END) AS failed,
                   AVG(CASE WHEN status_code > 0 THEN response_time END) AS avg_response_time,
                   SUM(size_bytes) AS total_bytes,
                   SUM(word_count) AS total_words,
                   MAX(depth) AS max_depth,
                   SUM(COALESCE(outlinks_count, 0)) AS total_links
              FROM pages WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        summary = {k: (row[k] or 0) for k in row.keys()}
        summary["avg_response_time"] = round(row["avg_response_time"] or 0.0, 3)
        return summary
