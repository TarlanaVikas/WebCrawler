import json
import sqlite3

from backend import database as db

LEGACY_SCHEMA = """
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, start_url TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL,
    pages_crawled INTEGER DEFAULT 0, error_count INTEGER DEFAULT 0);
CREATE TABLE pages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, url TEXT NOT NULL,
    status_code INTEGER, title TEXT, description TEXT, content_type TEXT, response_time REAL,
    size_bytes INTEGER, word_count INTEGER, links_json TEXT, meta_json TEXT, headings_json TEXT,
    UNIQUE(session_id, url));
CREATE TABLE links (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    source_url TEXT NOT NULL, target_url TEXT NOT NULL, UNIQUE(session_id, source_url, target_url));
"""


def test_legacy_database_is_migrated_and_stays_readable(tmp_path):
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute("INSERT INTO sessions (session_id, start_url, status, pages_crawled) VALUES ('abcd1234','https://old.test','completed',2)")
    conn.execute(
        "INSERT INTO pages (session_id,url,status_code,title,links_json,meta_json,headings_json) VALUES (?,?,?,?,?,?,?)",
        ("abcd1234", "https://old.test/", 200, "Old", json.dumps(["https://old.test/a", "https://old.test/b"]), "{}", "{}"),
    )
    conn.commit()
    conn.close()

    db.configure(path)
    db.init_db()
    db.init_db()  # idempotent

    session = db.get_session("abcd1234")
    assert session["status"] == "completed" and session["pages_crawled"] == 2
    assert session["created_at"].endswith("Z")
    page = db.get_session_pages("abcd1234", full=False)[0]
    assert page["title"] == "Old" and page["outlinks_count"] == 2 and page["depth"] == 0
    assert db.get_session_pages("abcd1234", full=True)[0]["links"] == ["https://old.test/a", "https://old.test/b"]


def test_save_filter_sort_and_search():
    db.save_session("s1", "https://x.test/")
    for i, (code, title) in enumerate([(200, "Alpha"), (404, "Missing"), (301, "Moved"), (0, "")]):
        db.save_page_record("s1", {"url": f"https://x.test/{i}", "status_code": code, "title": title,
                                    "response_time": 0.1 * (i + 1), "word_count": 10 * i, "depth": i,
                                    "links": [f"https://x.test/{i + 1}"], "error": "boom" if code == 0 else None})
    assert db.count_session_pages("s1") == 4
    assert db.count_session_pages("s1", status="ok") == 1
    assert db.count_session_pages("s1", status="redirect") == 1
    assert db.count_session_pages("s1", status="error") == 2
    assert [p["title"] for p in db.get_session_pages("s1", search="alp", full=False)] == ["Alpha"]
    assert db.get_session_pages("s1", sort="word_count", order="desc", limit=1, full=False)[0]["word_count"] == 30
    # sort column is whitelisted; hostile values fall back to id
    assert len(db.get_session_pages("s1", sort="id; DROP TABLE pages", full=False)) == 4
    assert db.count_session_pages("s1") == 4


def test_like_wildcards_in_search_are_literal():
    db.save_session("s2", "https://x.test/")
    db.save_page_record("s2", {"url": "https://x.test/a_b", "status_code": 200, "title": "100% sure"})
    db.save_page_record("s2", {"url": "https://x.test/axb", "status_code": 200, "title": "other"})
    assert db.count_session_pages("s2", search="a_b") == 1
    assert db.count_session_pages("s2", search="%") == 1
    assert db.count_session_pages("s2", search="_") == 1


def test_sessions_graph_summary_and_delete():
    db.save_session("s3", "https://x.test/", config={"max_pages": 5})
    db.save_page_record("s3", {"url": "https://x.test/", "status_code": 200, "title": "R", "links": ["https://x.test/a", "https://x.test/zzz"], "response_time": 0.2})
    db.save_page_record("s3", {"url": "https://x.test/a", "status_code": 200, "title": "A", "depth": 1, "links": ["https://x.test/"], "response_time": 0.4})
    graph = db.get_graph_data("s3")
    assert {n["id"] for n in graph["nodes"]} == {"https://x.test/", "https://x.test/a"}
    assert all(isinstance(n["page_id"], int) for n in graph["nodes"])
    assert len(graph["edges"]) == 2  # edge to the never-crawled /zzz is omitted
    assert db.get_session_summary("s3")["avg_response_time"] == 0.3
    assert db.get_session("s3")["config"] == {"max_pages": 5}
    db.update_session_status("s3", "completed", 2, 0)
    assert db.get_session("s3")["finished_at"].endswith("Z")
    rows, total = db.list_sessions()
    assert total == 1 and rows[0]["session_id"] == "s3"
    assert db.delete_session("s3") and not db.delete_session("s3")
    assert db.get_session_pages("s3") == []


def test_stats_update_never_overwrites_final_status():
    db.save_session("s4", "https://x.test/")
    db.update_session_status("s4", "stopped", 3, 1)
    db.update_session_stats("s4", 99, 99, 99, 99)  # a late periodic write
    s = db.get_session("s4")
    assert s["status"] == "stopped" and s["pages_crawled"] == 3


def test_interrupted_sessions_are_flagged():
    db.save_session("s5", "https://x.test/")
    assert db.mark_interrupted_sessions() == 1
    assert db.get_session("s5")["status"] == "interrupted"
