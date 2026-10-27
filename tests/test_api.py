import csv
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend import database as db
from backend.server import app

LOCAL = "http://localhost"


@pytest.fixture
def client():
    with TestClient(app, base_url=LOCAL) as c:
        yield c


def test_health_limits_and_security_headers(client):
    r = client.get("/api/health")
    assert r.json() == {"status": "ok", "name": "Web Crawler", "version": "1.0.0"}
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    limits = client.get("/api/limits").json()
    assert limits["max_pages"]["max"] >= 1 and limits["allow_private_networks"] is False
    assert "secret" not in json.dumps(limits).lower()


def test_frontend_is_served_and_branded(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>Web Crawler</title>" in r.text
    assert client.get("/api/openapi.json").json()["info"]["title"] == "Web Crawler API"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost:8000/", "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/", "file:///etc/passwd", "ftp://example.com", "http://user:pw@example.com/", "not a url",
])
def test_start_rejects_unsafe_urls_with_reason(client, url):
    r = client.post("/api/crawl/start", json={"start_url": url})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "url_blocked" and body["detail"] and "start_url" in body["fields"]
    assert db.list_sessions()[1] == 0  # nothing was recorded


def test_start_rejects_unresolvable_hosts(client):
    r = client.post("/api/crawl/start", json={"start_url": "https://no-such-host.invalid/"})
    assert r.status_code == 400 and "resolve" in r.json()["detail"].lower()


@pytest.mark.parametrize("payload,field", [
    ({"start_url": "https://example.com", "max_pages": 0}, "max_pages"),
    ({"start_url": "https://example.com", "max_pages": "many"}, "max_pages"),
    ({"start_url": "https://example.com", "concurrency": -1}, "concurrency"),
    ({"start_url": "https://example.com", "max_depth": -1}, "max_depth"),
    ({"start_url": "https://example.com", "user_agent": "bad\r\nHeader: x"}, "user_agent"),
])
def test_validation_errors_are_readable(client, payload, field):
    r = client.post("/api/crawl/start", json=payload)
    assert r.status_code == 422
    assert field in r.json()["fields"] and r.json()["code"] == "invalid_request"


def test_limits_above_server_caps_are_refused(client):
    for payload in ({"max_pages": 10**6}, {"concurrency": 500}, {"max_depth": 99}, {"crawl_delay": 0.0}):
        r = client.post("/api/crawl/start", json={"start_url": "https://example.com", **payload})
        assert r.status_code == 422 and r.json()["code"] == "limit_exceeded", payload


def test_results_and_sessions_for_unknown_ids(client):
    assert client.get("/api/sessions/deadbeef").status_code == 404
    assert client.get("/api/results", params={"session_id": "deadbeef"}).status_code == 404
    assert client.get("/api/graph", params={"session_id": "deadbeef"}).status_code == 404
    assert client.get("/api/export", params={"session_id": "deadbeef"}).status_code == 404
    assert client.delete("/api/sessions/deadbeef").status_code == 404


@pytest.mark.parametrize("sid", ["../etc/passwd", "x" * 30, "ABCDEFGH", "a b c d "])
def test_session_id_format_is_enforced(client, sid):
    assert client.get("/api/results", params={"session_id": sid}).status_code == 422
    assert client.get("/api/export", params={"session_id": sid}).status_code == 422


def test_status_when_idle(client):
    s = client.get("/api/crawl/status").json()
    assert s["is_running"] is False and s["status"] == "idle"
    assert client.post("/api/crawl/stop").json() == {"status": "not_running"}


# ---------------- guards ----------------

def test_foreign_host_header_is_rejected(client):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"host": "localhost:8000"}).status_code == 200
    assert client.get("/api/health", headers={"host": "[::1]:8000"}).status_code == 200


def test_cross_origin_writes_are_rejected(client):
    r = client.post("/api/crawl/stop", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.post("/api/crawl/stop", headers={"origin": "null"}).status_code == 403
    assert client.post("/api/crawl/stop", headers={"origin": "http://localhost"}).status_code == 200
    assert client.delete("/api/sessions/deadbeef", headers={"origin": "https://evil.example"}).status_code == 403


def test_no_permissive_cors_headers(client):
    r = client.get("/api/health", headers={"origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


# ---------------- end to end against the fixture site ----------------

@pytest.fixture
def private_client(monkeypatch, dev_settings):
    """Client whose crawl manager may reach the loopback fixture server."""
    with TestClient(app, base_url=LOCAL) as c:
        c.app.state.manager.cfg = dev_settings
        yield c


def wait_until_done(client, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        status = client.get("/api/crawl/status").json()
        if not status["is_running"]:
            return status
        time.sleep(0.1)
    raise AssertionError("crawl did not finish")


def test_crawl_end_to_end_via_api(private_client, site_url):
    c = private_client
    r = c.post("/api/crawl/start", json={"start_url": site_url + "/", "max_depth": 2, "max_pages": 20,
                                         "concurrency": 2, "crawl_delay": 0.0})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    status = wait_until_done(c)
    assert status["status"] == "completed" and status["pages_crawled"] > 3

    detail = c.get(f"/api/sessions/{sid}").json()
    assert detail["status"] == "completed" and detail["config"]["max_pages"] == 20
    assert detail["summary"]["pages"] == status["pages_crawled"]

    res = c.get("/api/results", params={"session_id": sid, "limit": 100}).json()
    assert res["total"] == res["count"] == status["pages_crawled"]
    assert "links" not in res["pages"][0] and "outlinks_count" in res["pages"][0]
    full = c.get("/api/results", params={"session_id": sid, "full": "true"}).json()["pages"][0]
    assert isinstance(full["links"], list)

    errors = c.get("/api/results", params={"session_id": sid, "status": "error"}).json()
    assert errors["total"] >= 1 and all(p["status_code"] >= 400 or p["status_code"] == 0 for p in errors["pages"])
    found = c.get("/api/results", params={"session_id": sid, "search": "deep"}).json()
    assert found["total"] >= 1

    page = c.get(f"/api/pages/{res['pages'][0]['id']}", params={"session_id": sid}).json()
    assert page["url"] == res["pages"][0]["url"] and "headings" in page
    assert c.get("/api/pages/999999", params={"session_id": sid}).status_code == 404

    graph = c.get("/api/graph", params={"session_id": sid}).json()
    assert len(graph["nodes"]) == res["total"] and graph["edges"] and not graph["truncated"]

    listing = c.get("/api/sessions").json()
    assert listing["total"] == 1 and listing["sessions"][0]["session_id"] == sid

    # exports
    csv_text = c.get("/api/export", params={"session_id": sid, "format": "csv"})
    assert csv_text.headers["content-disposition"].endswith(f'web-crawler-{sid}.csv"')
    rows = list(csv.reader(io.StringIO(csv_text.text)))
    assert rows[0][:3] == ["ID", "URL", "Status Code"] and len(rows) == res["total"] + 1
    titles = [r[3] for r in rows[1:]]
    assert "'=HYPERLINK(\"http://evil\",\"x\")" in titles          # formula injection neutralized
    assert not any(t.startswith("=") for t in titles)
    data = c.get("/api/export", params={"session_id": sid, "format": "json"}).json()
    assert len(data) == res["total"] and "links" in data[0]
    assert c.get("/api/export", params={"session_id": sid, "format": "xml"}).status_code == 422

    # delete
    assert c.delete(f"/api/sessions/{sid}").json()["status"] == "deleted"
    assert c.get(f"/api/sessions/{sid}").status_code == 404


def test_only_one_crawl_at_a_time_and_stop_works(private_client, site_url):
    c = private_client
    r = c.post("/api/crawl/start", json={"start_url": site_url + "/slow", "max_depth": 1, "crawl_delay": 0.0})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    second = c.post("/api/crawl/start", json={"start_url": site_url + "/"})
    assert second.status_code == 409
    assert second.json()["code"] == "crawl_running" and second.json()["session_id"] == sid
    assert c.delete(f"/api/sessions/{sid}").status_code == 409
    assert c.post("/api/crawl/stop").json() == {"status": "stopping"}
    final = wait_until_done(c)
    assert final["status"] == "stopped"
    assert c.get(f"/api/sessions/{sid}").json()["status"] == "stopped"


def test_log_stream_delivers_events_for_a_session(private_client, site_url):
    c = private_client
    sid = c.post("/api/crawl/start", json={"start_url": site_url + "/", "max_depth": 1, "crawl_delay": 0.0}).json()["session_id"]
    wait_until_done(c)
    broker = c.app.state.broker
    events = broker.backlog(sid)
    assert any(e["level"] == "system" and "started" in e["message"] for e in events)
    assert any(e["message"].startswith("200 ") for e in events)
    assert all(e["session_id"] == sid for e in events)
    assert broker.backlog("ffffffff") == []


def test_unexpected_errors_return_generic_json_without_internals(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("secret internal detail /srv/db.sqlite")
    monkeypatch.setattr(db, "list_sessions", boom)
    with TestClient(app, base_url=LOCAL, raise_server_exceptions=False) as c:
        r = c.get("/api/sessions")
    assert r.status_code == 500
    assert r.json()["code"] == "internal_error"
    assert "secret" not in r.text and "sqlite" not in r.text
