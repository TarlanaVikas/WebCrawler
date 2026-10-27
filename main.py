"""Web Crawler entry point."""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from backend.config import APP_NAME, APP_VERSION, settings


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="web-crawler", description=f"{APP_NAME} server")
    parser.add_argument("--host", default=settings.host, help="interface to bind (default: %(default)s)")
    parser.add_argument("--port", type=int, default=settings.port, help="port to listen on (default: %(default)s)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    parser.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    parser.add_argument("--test", action="store_true", help="verify imports and the database, then exit")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print(f"{APP_NAME} {APP_VERSION}")

    if args.test:
        from backend import database
        from backend.server import app  # noqa: F401  (import check)

        database.init_db()
        print(f"[test] Imports and database OK ({database.DB_FILE})")
        return 0

    import uvicorn

    display_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0", "::") else args.host
    url = f"http://{display_host}:{args.port}"
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("Warning: the server has no authentication. Only bind to a non-local interface on a trusted network.")
    print(f"Listening on {url}")

    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run("backend.server:app", host=args.host, port=args.port, reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
