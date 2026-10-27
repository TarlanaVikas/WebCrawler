<p align="center">
  <img src="docs/banner.svg" alt="Web Crawler" width="100%">
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-1b2124?style=flat-square&labelColor=1b2124&color=6dbba5">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-aiohttp-1b2124?style=flat-square&labelColor=1b2124&color=6dbba5">
  <img alt="SQLite" src="https://img.shields.io/badge/storage-SQLite-1b2124?style=flat-square&labelColor=1b2124&color=d0a85c">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-1b2124?style=flat-square&labelColor=1b2124&color=d0a85c">
</p>

# Web Crawler

**Follow every link. Keep what each page says.**

## Overview

Web Crawler starts from one URL, follows links page by page within limits you set, and records what each page returns: HTTP status, title, description, headings, metadata, outgoing links, size, word count and response time. A dark, instrument-style interface shows the crawl as it happens (progress, a link graph, a sortable page table and a live log) and keeps every crawl in a local database so the results can be reopened, filtered and exported.

It is built for people who need an honest, inspectable picture of a site's structure: which pages exist, how they link together, what responds with errors, and what each page declares about itself. It is a link-and-metadata crawler. It does not execute JavaScript, so pages that only exist after client-side rendering are recorded as the server sent them.

## Features

**Crawling**

- Breadth-first crawl from a start URL with a depth limit, a page limit, concurrent requests and a minimum delay between requests to the same host.
- Scope control: stay on the starting site (`www.` counts as the same site, other subdomains do not) or follow links anywhere.
- `robots.txt` support, on by default: allow and disallow rules, agent-specific groups and `Crawl-delay`, with RFC 9309 behaviour for missing, erroring and unreachable files.
- Duplicate protection: URLs are normalised (case, default ports, fragments, empty paths) and each is fetched once, so link cycles cannot cause loops.
- Redirects are followed inside the crawl scope and recorded (the final URL is stored on the page). A redirect that leaves the scope is recorded but not followed.
- Non-HTML responses are detected from the headers and never downloaded; common binary file types are not requested at all.
- A running crawl can be stopped. Requests already in flight finish, then the crawl ends and is saved as stopped.

**Interface**

- **Crawl**: URL input, depth, page limit, concurrency, delay, scope and robots switches. The slider ranges come from the limits the server actually enforces.
- **Live crawl view**: progress against the page limit, queue and in-flight counts, pages per second, elapsed time and a summary ledger (2xx, 3xx, HTTP errors, failed requests, skipped, discovered URLs, average response, bytes).
- **Link graph**: a force-directed graph of the crawled pages, with rings for crawl depth. Drag, pan, zoom, hover for details, select to inspect, and navigate by keyboard.
- **Pages**: a sortable, filterable, paginated table with a page inspector (headings, meta tags, outgoing links, redirect target, error reason).
- **Log**: the live crawl log over Server-Sent Events.
- **History**: every crawl is stored; reopen, export or delete it.
- Empty, loading, error and not-found states throughout; responsive from phone to desktop; keyboard operable with visible focus; the background animation respects `prefers-reduced-motion` and has a visible on/off switch.

**Data**

- SQLite storage of crawls, pages and links. Databases created by earlier versions are migrated in place the first time the server starts.
- CSV and JSON export, streamed page by page so large crawls are not held in memory. Spreadsheet formula injection from crawled titles is neutralised in CSV.

## Technologies

| Layer | Technology |
| --- | --- |
| Backend | Python, FastAPI, Uvicorn, aiohttp, Beautiful Soup 4 (`html.parser`), Pydantic v2 |
| Storage | SQLite (WAL mode) |
| Frontend | Vanilla JavaScript (ES modules, no build step), HTML5 Canvas, CSS custom properties |
| Type and icons | Bricolage Grotesque and IBM Plex Mono (bundled, SIL OFL 1.1), Lucide icons (bundled, ISC) |
| Tests | pytest, pytest-asyncio, httpx |

All fonts and icons are served from the project itself; the interface makes no requests to third-party hosts.

## Architecture

```
 Browser (static ES modules)                       Server (FastAPI)
 ┌──────────────────────────────┐   JSON / SSE   ┌─────────────────────────────────────────────┐
 │ views: crawl, job, history   │ ─────────────▶ │ server.py   routes, host/origin guard, CSP  │
 │ graph.js   link graph canvas │ ◀───────────── │ manager.py  one active crawl at a time      │
 │ ambient.js decorative canvas │                │ crawler.py  queue, workers, budget, stop    │
 └──────────────────────────────┘                │   ├─ fetcher.py  manual redirects, size cap │
                                                 │   ├─ security.py URL + IP validation, DNS   │
                                                 │   ├─ robots.py   RFC 9309 rules and delay   │
                                                 │   └─ parser.py   title, meta, headings, links│
                                                 │ database.py SQLite     events.py log broker │
                                                 └─────────────────────────────────────────────┘
                                                                    │ HTTP(S), public addresses only
                                                                    ▼
                                                              target websites
```

**Workflow.** `POST /api/crawl/start` validates the request and the start URL (including DNS), writes a session row and starts the engine. Workers take URLs from a queue, check `robots.txt`, wait for the per-host delay, fetch the page through the safe fetcher, parse it off the event loop, store it, and queue its in-scope links. The crawl ends when the queue is empty, the page budget is spent, the time limit is reached or it is stopped. The interface polls status for counts, reads pages and the graph from the database, and receives log lines over SSE.

## Security and reliability

These are the protections the code enforces. They reduce risk; they are not a substitute for network-level egress controls if the server is ever exposed beyond a trusted machine.

**Untrusted URLs and SSRF**

- Only `http` and `https` URLs are accepted, without embedded credentials, up to 2048 characters.
- Loopback, private, link-local, CGNAT, multicast, reserved and cloud-metadata addresses are refused. Addresses that embed an IPv4 address (IPv4-mapped, 6to4, Teredo, NAT64) are judged by the embedded address. Alternative IPv4 spellings (decimal, hex, octal) and single-label or internal host names (`localhost`, `*.local`, `*.internal`, and similar) are refused.
- Every hostname is resolved by a validating resolver at connection time, and the connection goes to the validated address, so a DNS answer cannot change between the check and the request. If any returned address is non-public, the host is refused.
- Redirects are never followed automatically. Each hop is re-validated and limited to five hops.
- Proxy environment variables and cookies are ignored, so requests cannot be routed around the resolver check.
- Discovered links to refused addresses and internal host names are not queued. A host name that resolves to a private address is refused when it is fetched and shows up as a failed request.
- Private-network crawling exists only as an explicit server setting meant for development and tests. It is off by default, cannot be set by an API request, and the interface shows a warning when it is on.

**Resource limits**

- Server-side hard caps on depth, pages, concurrency, delay and total runtime. Requests above them are rejected rather than silently reduced.
- Connect and total timeouts, a response size cap (cut off while streaming), a cap on links stored per page and a cap on discovered URLs.
- Requests to the same host are spaced apart regardless of how many workers run. `Crawl-delay` from `robots.txt` is honoured up to a ceiling.
- Only one crawl runs at a time.

**The local server**

- The `Host` header is checked against an allow-list (defends against DNS rebinding of a local server). State-changing requests with a foreign or `null` `Origin` are rejected. No CORS headers are sent unless origins are configured.
- A strict Content-Security-Policy (no inline scripts or styles, same-origin only), `X-Frame-Options`, `nosniff` and `no-referrer` are sent on every response.
- Session identifiers are validated against a fixed pattern; sort and filter parameters are whitelisted; SQL is parameterised.
- Unexpected errors return a generic JSON message and are logged on the server.

**Crawled content is data, not code**

- Pages are parsed with `html.parser` and are never rendered or executed by the server.
- The interface builds every crawled value with text nodes, never as HTML, and only `http(s)` links are made clickable.
- Spreadsheet formula prefixes in exported CSV cells are neutralised.

**Not covered.** There is no authentication: the server is meant for local use and warns when bound to a non-local interface. The FastAPI documentation page at `/api/docs` loads Swagger UI from a public CDN, under a relaxed policy limited to that page.

## Configuration

Settings are read from environment variables and from an optional `.env` file in the project root (real environment variables take precedence). `.env.example` lists every variable with its default. There are no API keys or secrets in this project.

| Variable | Default | Purpose |
| --- | --- | --- |
| `WEBCRAWLER_HOST` / `WEBCRAWLER_PORT` | `127.0.0.1` / `8000` | Interface and port to listen on |
| `WEBCRAWLER_DB_PATH` | `crawler.db` in the project root | SQLite file |
| `WEBCRAWLER_ALLOWED_HOSTS` | empty | Extra accepted `Host` values |
| `WEBCRAWLER_CORS_ORIGINS` | empty | Cross-origin callers allowed to use the API |
| `WEBCRAWLER_ALLOW_PRIVATE_NETWORKS` | `false` | Allow crawling loopback and private addresses (development and tests only) |
| `WEBCRAWLER_MAX_DEPTH` | `8` | Cap on link depth |
| `WEBCRAWLER_MAX_PAGES` | `1000` | Cap on pages per crawl |
| `WEBCRAWLER_MAX_CONCURRENCY` | `16` | Cap on concurrent requests |
| `WEBCRAWLER_MIN_DELAY` | `0.1` | Minimum delay between requests to one host (seconds) |
| `WEBCRAWLER_MAX_RUNTIME` | `1800` | Maximum crawl duration (seconds) |
| `WEBCRAWLER_MAX_RESPONSE_BYTES` | `2097152` | Response size cap |
| `WEBCRAWLER_USER_AGENT` | `WebCrawler/1.0.0 (+local crawler; respects robots.txt)` | User-Agent header |

## API

All endpoints are under `/api`. Errors are JSON of the form `{"detail": "...", "code": "..."}`; validation errors also include a `fields` object. Interactive documentation is served at `/api/docs` and the schema at `/api/openapi.json`.

| Method and path | Description |
| --- | --- |
| `GET /api/health` | Name and version |
| `GET /api/limits` | Limits and defaults enforced by the server |
| `POST /api/crawl/start` | Start a crawl. Body: `start_url`, `max_depth`, `max_pages`, `concurrency`, `crawl_delay`, `stay_on_domain`, `respect_robots`, optional `user_agent`. Responds `409` if a crawl is already running, `400` for a refused URL, `422` for invalid or out-of-limit values |
| `POST /api/crawl/stop` | Ask the running crawl to stop |
| `GET /api/crawl/status` | Live counters of the running or most recent crawl |
| `GET /api/sessions` | Saved crawls, newest first (`limit`, `offset`) |
| `GET /api/sessions/{id}` | One crawl with its settings, aggregates and live counters while running |
| `DELETE /api/sessions/{id}` | Delete a finished crawl and its pages |
| `GET /api/results` | Pages of a crawl: `session_id`, `limit`, `offset`, `search`, `status` (`all`, `ok`, `redirect`, `error`), `sort`, `order`, `full` |
| `GET /api/pages/{page_id}` | Full record of one page, with `session_id` |
| `GET /api/graph` | Nodes and edges between crawled pages |
| `GET /api/export` | Download a crawl as `csv` or `json` |
| `GET /api/logs/stream` | Server-Sent Events with crawl log lines, optionally for one `session_id` |

## Project structure

```
web-crawler/
├── main.py                  Entry point
├── backend/
│   ├── server.py            Routes, host/origin guard, security headers, SSE, static files
│   ├── manager.py           Owns the single active crawl
│   ├── crawler.py           Crawl engine: queue, workers, budget, throttling, termination
│   ├── fetcher.py           Safe HTTP fetching: manual redirects, bounded reads
│   ├── security.py          URL validation, IP classification, validating DNS resolver
│   ├── robots.py            robots.txt cache with RFC 9309 semantics
│   ├── parser.py            Title, metadata, headings, links, word count
│   ├── urls.py              Normalisation and scope helpers
│   ├── database.py          SQLite access, migrations, queries
│   ├── events.py            Log broker for the SSE stream
│   ├── exporter.py          Streaming CSV and JSON export
│   ├── models.py            API response models
│   └── config.py            Settings, hard limits, request model
├── frontend/
│   ├── index.html
│   ├── css/                 Tokens, base, components, views
│   ├── js/
│   │   ├── main.js, router.js, state.js, store.js, api.js, dom.js, icons.js
│   │   ├── ambient.js       Decorative background canvas
│   │   ├── graph.js         Link graph canvas
│   │   ├── components/      Inspector, controls, toasts, confirm dialog
│   │   └── views/           Crawl, job, history, not found
│   └── assets/              Fonts and icon license
├── tests/                   pytest suite
├── docs/banner.svg
├── .env.example
└── requirements.txt, requirements-dev.txt
```

## Tests

The `tests/` directory holds an automated suite covering:

- URL validation and IP classification, including encoded and embedded addresses, and the validating resolver (including a simulated DNS-rebinding answer).
- Redirect handling, shown not to issue a request to an internal address.
- The parser, URL normalisation and scope rules.
- The crawl engine against a local fixture website: link following, cycles, redirects (in and out of scope), 404 pages, non-HTML, robots rules, page and depth limits, stop, timeouts, size truncation and redirect loops.
- `robots.txt` semantics, SQLite migration of a database created with the original schema, filtering, sorting and search.
- The HTTP API end to end: validation, refusals, one-crawl-at-a-time, stop, results, graph, exports (including CSV formula neutralisation), host and origin guards, and security headers.

The interface was also exercised in a real browser against a running server for the main flows, error states and keyboard paths. That browser run was a manual verification and is not part of the committed suite.

## Limitations

- No JavaScript rendering, no form submission, no authenticated crawling and no cookies.
- HTML pages only. Sitemaps are not read.
- One crawl at a time. Subdomains other than `www.` count as other sites.
- The live log is kept in server memory: it shows crawls from the current server run only.
- The graph shows up to 1500 pages, and only links between pages that were crawled.
- Verified against local fixture sites and one real HTTPS site; behaviour on the wider web will vary.
- Crawl only sites you own or have permission to crawl, and follow their terms of use.

