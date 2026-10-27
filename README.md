 <p align="center">
  <img src="docs/banner.svg" alt="Web Crawler" width="100%">
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-242424?style=flat-square&labelColor=242424&color=C6A77D">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-async-242424?style=flat-square&labelColor=242424&color=C6A77D">
  <img alt="SQLite" src="https://img.shields.io/badge/Storage-SQLite-242424?style=flat-square&labelColor=242424&color=B7A99A">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-242424?style=flat-square&labelColor=242424&color=B7A99A">
</p>

<h1 align="center">Web Crawler</h1>

<p align="center">
  <strong>Every link has a story. Follow the structure.</strong>
</p>

<p align="center">
  A web exploration tool for discovering pages, tracing connections, inspecting metadata, and understanding how websites are built.
</p>

---

## Overview

Web Crawler transforms a starting URL into an inspectable map of a website's structure. It follows links within configurable boundaries, collects page-level information, and presents the results through a live crawling interface, an interactive link graph, and a searchable results explorer.

From response codes and page metadata to outgoing links and request timings, every crawl produces a structured record that can be revisited, filtered, and exported.

The interface is designed around clarity and control: a restrained dark workspace, live activity indicators, carefully organised data, and subtle ambient motion that never competes with the information.

## What it can do

### 01 — Explore

- Crawl websites using breadth-first traversal.
- Configure crawl depth, page limits, concurrency, and request delays.
- Choose between same-site crawling and unrestricted external-link discovery.
- Respect `robots.txt` rules and supported crawl delays.
- Normalise URLs and prevent duplicate requests and link cycles.
- Track redirects while enforcing crawl boundaries.
- Identify non-HTML responses without downloading their contents.
- Stop an active crawl and retain its collected results.

### 02 — Observe

- Monitor crawl progress, elapsed time, and pages processed per second.
- Inspect queued URLs and in-flight requests.
- Track successful responses, redirects, errors, skipped pages, and discovered URLs.
- Review average response time and transferred data.
- Follow live events through a server-sent event stream.
- Explore a force-directed link graph with depth-based rings.
- Inspect individual pages, metadata, headings, and outgoing links.

### 03 — Investigate

- Search, sort, filter, and paginate crawl results.
- Filter pages by successful responses, redirects, and errors.
- Open detailed page records with metadata and redirect information.
- Explore relationships between discovered pages.
- Reopen previously saved crawl sessions.
- Delete completed sessions when they are no longer needed.
- Export crawl data in CSV or JSON format.

### 04 — Understand

Each recorded page can include:

- Requested and final URLs
- HTTP status and redirect information
- Page title and meta description
- Headings and metadata
- Outgoing links
- Response size and word count
- Request duration and failure details

**Scope note:** Web Crawler analyses the HTML returned by the server. It does not execute JavaScript, submit forms, or access authenticated pages.

## Interface philosophy

The workspace is built to make complex crawl activity easier to understand without turning the interface into a wall of charts.

- **Focused visual hierarchy** — crawl controls, live activity, and detailed results have distinct roles.
- **Live visual feedback** — progress, logs, and graph updates make crawling activity observable.
- **Interactive exploration** — select graph nodes and inspect page-level details.
- **Persistent history** — saved sessions make previous crawls accessible.
- **Responsive layout** — designed for desktop and smaller screens.
- **Accessible interaction** — keyboard navigation, visible focus states, and reduced-motion support.
- **Self-contained assets** — bundled fonts and icons avoid third-party asset requests in the main interface.

## Technology

| Layer | Stack |
|---|---|
| Language | Python 3.12 |
| API | FastAPI, Uvicorn |
| HTTP requests | aiohttp |
| HTML parsing | Beautiful Soup 4, `html.parser` |
| Validation | Pydantic v2 |
| Database | SQLite with WAL mode |
| Frontend | HTML5, CSS, vanilla JavaScript ES modules |
| Visualisation | HTML5 Canvas |
| Live updates | Server-Sent Events |
| Typography | Bricolage Grotesque, IBM Plex Mono |
| Icons | Lucide |
| Testing | pytest, pytest-asyncio, httpx |

## Architecture

The application separates the browser interface, crawl orchestration, safe request handling, parsing, persistence, and event delivery into focused modules.

```text
Web Crawler
│
├── Frontend
│   ├── Crawl workspace
│   ├── Live session view
│   ├── Interactive link graph
│   ├── Results explorer
│   ├── Session history
│   └── Live event log
│
├── FastAPI backend
│   ├── server.py       API routes and security headers
│   ├── manager.py      Active crawl coordination
│   ├── crawler.py      Queue, workers, limits and stopping
│   ├── fetcher.py      Safe HTTP requests and redirects
│   ├── security.py     URL validation and DNS checks
│   ├── robots.py       robots.txt rules and delays
│   ├── parser.py       Page metadata and link extraction
│   ├── urls.py         URL normalisation and scope
│   ├── database.py     Persistence and migrations
│   ├── events.py       Live event distribution
│   ├── exporter.py     CSV and JSON exports
│   ├── models.py       API response models
│   └── config.py       Settings and request limits
│
└── SQLite
    ├── Crawl sessions
    ├── Page records
    └── Link relationships
```

### Crawl lifecycle

1. Validate the starting URL and resolve its hostname.
2. Create a persistent crawl session.
3. Process discovered URLs through a bounded queue.
4. Apply scope, `robots.txt`, delay, and resource-limit rules.
5. Fetch eligible pages through the validated request layer.
6. Parse HTML and collect metadata and links.
7. Persist page records and publish crawl events.
8. Finish when the queue is exhausted, a limit is reached, or the crawl is stopped.

## Security and reliability

Web Crawler includes protections intended to reduce the risks associated with requesting URLs supplied by users or discovered on external pages.

- Restricts accepted URL schemes to HTTP and HTTPS.
- Rejects embedded URL credentials and malformed or excessively long URLs.
- Blocks loopback, private, link-local, reserved, and cloud-metadata addresses by default.
- Validates DNS results at connection time and connects to validated addresses.
- Revalidates redirect destinations and limits redirect hops.
- Ignores proxy environment variables and cookies.
- Applies configurable hard limits for depth, pages, concurrency, request duration, response size, and total runtime.
- Enforces per-host request delays and supported `robots.txt` crawl delays.
- Uses parameterised database queries and validates API inputs.
- Applies security headers and request-origin checks.
- Treats crawled content as untrusted text rather than executable HTML.
- Neutralises spreadsheet formula prefixes in CSV exports.

These protections reduce risk but do not replace network-level controls or a security review.

**Deployment boundary:** The application has no authentication and is intended for local use. Exposing it to an untrusted network requires additional access controls and infrastructure-level protections. The API documentation at `/api/docs` loads Swagger UI from a public CDN.

## Data and configuration

Crawl sessions and page relationships are stored locally in SQLite. Earlier database schemas are migrated when the server starts.

Configuration can be supplied through environment variables or an optional `.env` file. Environment variables take precedence.

| Setting | Default | Purpose |
|---|---|---|
| `WEBCRAWLER_HOST` | `127.0.0.1` | Server interface |
| `WEBCRAWLER_PORT` | `8000` | Server port |
| `WEBCRAWLER_DB_PATH` | `crawler.db` | Database location |
| `WEBCRAWLER_ALLOWED_HOSTS` | Empty | Additional accepted host values |
| `WEBCRAWLER_CORS_ORIGINS` | Empty | Permitted cross-origin callers |
| `WEBCRAWLER_ALLOW_PRIVATE_NETWORKS` | `false` | Development and test network access |
| `WEBCRAWLER_MAX_DEPTH` | `8` | Maximum crawl depth |
| `WEBCRAWLER_MAX_PAGES` | `1000` | Maximum pages per crawl |
| `WEBCRAWLER_MAX_CONCURRENCY` | `16` | Maximum concurrent requests |
| `WEBCRAWLER_MIN_DELAY` | `0.1` | Minimum per-host delay in seconds |
| `WEBCRAWLER_MAX_RUNTIME` | `1800` | Maximum runtime in seconds |
| `WEBCRAWLER_MAX_RESPONSE_BYTES` | `2097152` | Maximum response size |
| `WEBCRAWLER_USER_AGENT` | `WebCrawler/1.0.0 (+local crawler; respects robots.txt)` | Request identification |

No external AI service or API key is required.

## API reference

All API endpoints are under `/api`.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Service name and version |
| `GET` | `/limits` | Server-enforced limits |
| `POST` | `/crawl/start` | Start a crawl |
| `POST` | `/crawl/stop` | Request a crawl stop |
| `GET` | `/crawl/status` | Current or most recent crawl status |
| `GET` | `/sessions` | List saved sessions |
| `GET` | `/sessions/{id}` | Retrieve session details |
| `DELETE` | `/sessions/{id}` | Delete a completed session |
| `GET` | `/results` | Search and paginate page records |
| `GET` | `/pages/{page_id}` | Retrieve a page record |
| `GET` | `/graph` | Retrieve graph nodes and edges |
| `GET` | `/export` | Export results as CSV or JSON |
| `GET` | `/logs/stream` | Receive live crawl events |

Interactive API documentation is available at `/api/docs`, with the OpenAPI schema at `/api/openapi.json`.

## Testing

The automated test suite covers URL validation, IP classification, DNS rebinding scenarios, redirects, URL normalisation, scope enforcement, parsing, crawl limits, stopping behaviour, timeouts, response truncation, `robots.txt` semantics, database migrations, filtering, exports, API validation, security headers, and origin checks.

The interface has also been manually exercised in a browser for primary workflows, error states, and keyboard interactions. Manual browser verification is separate from the automated test suite.

## Known limitations

- JavaScript-rendered content is not executed.
- Forms, authenticated pages, and cookie-dependent content are not supported.
- Sitemaps are not read.
- Only one crawl runs at a time.
- Subdomains are treated as separate sites, except for the `www` equivalence rule.
- Live log events are held in server memory and do not survive server restarts.
- The graph displays up to 1,500 pages and only relationships between crawled pages.
- Results may vary across websites and network conditions.

## Responsible use

Crawl only websites you own or have permission to inspect. Respect each website's terms, access policies, and `robots.txt` rules. Configure crawl limits responsibly to avoid placing unnecessary load on external services.

---

<p align="center">
  <strong>Web Crawler</strong>
  <br>
  <sub>Discover the pages. Trace the connections. Understand the structure.</sub>
</p>
