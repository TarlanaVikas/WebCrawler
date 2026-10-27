"""Shared fixtures: an isolated database and a local fixture website to crawl."""
import asyncio
import dataclasses

import pytest
import pytest_asyncio
from aiohttp import web

from backend import database
from backend.config import settings as base_settings


@pytest.fixture
def dev_settings():
    """Settings that allow the loopback fixture server and keep tests fast."""
    return dataclasses.replace(
        base_settings,
        allow_private_networks=True,
        min_crawl_delay=0.0,
        request_timeout=3.0,
        connect_timeout=2.0,
        max_runtime_seconds=30,
    )


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    previous = database.DB_FILE
    database.configure(str(tmp_path / "test.db"))
    database.init_db()
    yield
    database.configure(previous)


def _page(title, body=""):
    return f"<!doctype html><html><head><title>{title}</title></head><body>{body}</body></html>"


HITS = web.AppKey("hits", dict)


def build_site() -> web.Application:
    hits = {}

    async def index(request):
        hits["/"] = hits.get("/", 0) + 1
        return web.Response(content_type="text/html", text=_page(
            "Home",
            '<h1>Home</h1><a href="/a">A</a> <a href="b">B</a> <a href="/redirect-in">in</a> '
            '<a href="/redirect-out">out</a> <a href="/missing">missing</a> <a href="/file.pdf">pdf</a> '
            '<a href="/nothtml">text</a> <a href="/private">private</a> <a href="http://other.invalid/x">ext</a> '
            '<a href="javascript:alert(1)">js</a> <a href="mailto:x@example.com">mail</a>'))

    async def a(request):
        return web.Response(content_type="text/html", text=_page(
            "&lt;img src=x onerror=alert(1)&gt; A page",
            '<h1>A</h1><script>var secret = "one two three";</script><a href="/a/deep">deep</a> <a href="/">home</a> <a href="/a#frag">self</a>'))

    async def deep(request):
        return web.Response(content_type="text/html", text=_page("Deep", '<a href="/a/deeper">more</a>'))

    async def deeper(request):
        return web.Response(content_type="text/html", text=_page("Deeper"))

    async def b(request):
        return web.Response(content_type="text/html", text=_page("=HYPERLINK(\"http://evil\",\"x\")", "<h2>B</h2>"))

    async def redirect_in(request):
        raise web.HTTPFound("/a")

    async def redirect_out(request):
        raise web.HTTPFound("http://example.org/")

    async def missing(request):
        return web.Response(status=404, content_type="text/html", text=_page("Not here", '<a href="/never-followed">x</a>'))

    async def nothtml(request):
        return web.Response(text="plain text", content_type="text/plain")

    async def private(request):
        return web.Response(content_type="text/html", text=_page("Private"))

    async def robots(request):
        return web.Response(text="User-agent: *\nDisallow: /private\n", content_type="text/plain")

    async def slow(request):
        await asyncio.sleep(5)
        return web.Response(text="late")

    async def big(request):
        return web.Response(content_type="text/html", text=_page("Big", "<p>" + "word " * 50000 + "</p>"))

    async def loop_a(request):
        raise web.HTTPFound("/loop-b")

    async def loop_b(request):
        raise web.HTTPFound("/loop-a")

    app = web.Application()
    app[HITS] = hits
    app.router.add_get("/", index)
    app.router.add_get("/a", a)
    app.router.add_get("/a/deep", deep)
    app.router.add_get("/a/deeper", deeper)
    app.router.add_get("/b", b)
    app.router.add_get("/redirect-in", redirect_in)
    app.router.add_get("/redirect-out", redirect_out)
    app.router.add_get("/missing", missing)
    app.router.add_get("/nothtml", nothtml)
    app.router.add_get("/private", private)
    app.router.add_get("/robots.txt", robots)
    app.router.add_get("/slow", slow)
    app.router.add_get("/big", big)
    app.router.add_get("/loop-a", loop_a)
    app.router.add_get("/loop-b", loop_b)
    return app


@pytest_asyncio.fixture
async def site():
    app = build_site()
    runner = web.AppRunner(app, handler_cancellation=True)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    port = tcp._server.sockets[0].getsockname()[1]
    yield {"base": f"http://127.0.0.1:{port}", "app": app}
    await runner.cleanup()


@pytest.fixture
def site_url():
    """Run the fixture site on a background thread so synchronous TestClient tests can use it."""
    import threading

    loop = asyncio.new_event_loop()
    state = {}
    ready = threading.Event()

    async def start():
        runner = web.AppRunner(build_site(), handler_cancellation=True)
        await runner.setup()
        tcp = web.TCPSite(runner, "127.0.0.1", 0)
        await tcp.start()
        state["runner"] = runner
        state["port"] = tcp._server.sockets[0].getsockname()[1]
        ready.set()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    ready.wait(5)
    yield f"http://127.0.0.1:{state['port']}"
    asyncio.run_coroutine_threadsafe(state["runner"].cleanup(), loop).result(10)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
