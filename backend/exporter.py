"""CSV and JSON export of a crawl session.

Both exporters stream page by page so large crawls never have to fit in memory in one
piece (the original silently stopped at 5000 rows).
"""
from __future__ import annotations

import csv
import io
import json
from typing import Iterator

from backend.database import iter_session_pages

CSV_COLUMNS = [
    "ID", "URL", "Status Code", "Title", "Response Time (s)", "Size (Bytes)", "Word Count",
    "Outlinks Count", "Depth", "Description", "Redirect URL", "Error",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    """Neutralize spreadsheet formula injection in untrusted text.

    Titles and descriptions come from crawled pages. A cell that starts with ``=``, ``+``,
    ``-`` or ``@`` would be evaluated as a formula by Excel or Sheets, so it is prefixed
    with an apostrophe, which spreadsheets treat as "this is text".
    """
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def iter_csv(session_id: str) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        data = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return data

    writer.writerow(CSV_COLUMNS)
    yield flush()
    for page in iter_session_pages(session_id):
        writer.writerow([
            page.get("id"),
            csv_safe(page.get("url")),
            page.get("status_code"),
            csv_safe(page.get("title")),
            page.get("response_time"),
            page.get("size_bytes"),
            page.get("word_count"),
            page.get("outlinks_count"),
            page.get("depth"),
            csv_safe(page.get("description")),
            csv_safe(page.get("redirect_url")),
            csv_safe(page.get("error")),
        ])
        yield flush()


def iter_json(session_id: str) -> Iterator[str]:
    yield "[\n"
    first = True
    for page in iter_session_pages(session_id):
        yield ("" if first else ",\n") + json.dumps(page, ensure_ascii=False, indent=2)
        first = False
    yield "\n]\n"


def export_to_csv(session_id: str) -> str:
    return "".join(iter_csv(session_id))


def export_to_json(session_id: str) -> str:
    return "".join(iter_json(session_id))
