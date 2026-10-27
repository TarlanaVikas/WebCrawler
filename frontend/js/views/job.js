import { api } from "../api.js";
import { createInspector } from "../components/inspector.js";
import { toast } from "../components/toast.js";
import {
  append, clear, debounce, fmtBytes, fmtClock, fmtDateTime, fmtDuration, fmtNum, fmtSeconds, h, hostOf, icon,
  sessionBadge, statusBadge, urlText,
} from "../dom.js";
import { createGraph } from "../graph.js";
import { pokeStatus } from "../state.js";

const PAGE_SIZE = 25;
const LOG_LIMIT = 600;

const COLUMNS = [
  { key: "status_code", label: "Status", numeric: false, first: "asc" },
  { key: "url", label: "URL", first: "asc" },
  { key: "title", label: "Title", first: "asc" },
  { key: "depth", label: "Depth", numeric: true, first: "asc" },
  { key: "response_time", label: "Time", numeric: true, first: "desc" },
  { key: "size_bytes", label: "Size", numeric: true, first: "desc" },
  { key: "word_count", label: "Words", numeric: true, first: "desc" },
  { key: "outlinks_count", label: "Links", numeric: true, first: "desc" },
];

export function mount(root, ctx) {
  const sessionId = ctx.params[0];
  let detail = null;
  let lastCount = -1;
  let lastStatus = null;
  let tab = "graph";
  let destroyed = false;
  let pollTimer = null;
  let pollController = new AbortController();
  let resultsController = null;
  let graph = null;
  let eventSource = null;
  let logLines = 0;
  let follow = true;
  let selected = null; // { id, url }
  const query = { offset: 0, sort: "id", order: "desc", search: "", status: "all" };

  // ---------------------------------------------------------------- skeleton
  const title = h("h1", { "data-autofocus": true, class: "job-title" }, h("span", { class: "skeleton wide" }));
  const startUrl = h("p", { class: "job-url mono dim" });
  const badgeSlot = h("span", { class: "job-badge" });
  const actions = h("div", { class: "job-actions" });
  const bannerSlot = h("div", { class: "job-banner" });
  const trail = buildTrail();
  const ledger = buildLedger();
  const configBox = h("details", { class: "config-box", hidden: true });

  const inspector = createInspector({
    sessionId,
    onClose: () => { selected = null; side.dataset.mode = "summary"; graph?.select(null); markSelectedRow(); },
  });
  const scrim = h("div", { class: "sheet-scrim", onclick: () => inspector.close(false) });
  const side = h("div", { class: "job-side", dataset: { mode: "summary" } },
    scrim,
    h("section", { class: "summary", "aria-labelledby": "summary-h" }, h("h2", { id: "summary-h" }, "Summary"), ledger.el, configBox),
    inspector.el);

  // tabs
  const tabs = [
    { id: "graph", label: "Link graph" },
    { id: "pages", label: "Pages" },
    { id: "log", label: "Log" },
  ];
  const tabButtons = {};
  const tablist = h("div", { class: "tabs", role: "tablist", "aria-label": "Crawl views" },
    tabs.map((t) => {
      const count = t.id === "pages" ? h("span", { class: "tab-count num" }) : null;
      const btn = h("button", {
        class: "tab", role: "tab", type: "button", id: `tab-${t.id}`, "aria-controls": `panel-${t.id}`,
        "aria-selected": t.id === tab ? "true" : "false", tabindex: t.id === tab ? "0" : "-1",
        onclick: () => selectTab(t.id),
      }, t.label, count);
      tabButtons[t.id] = { btn, count };
      return btn;
    }));
  tablist.addEventListener("keydown", (e) => {
    const index = tabs.findIndex((t) => t.id === tab);
    let next = null;
    if (e.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    if (next !== null) { e.preventDefault(); selectTab(tabs[next].id); tabButtons[tabs[next].id].btn.focus(); }
  });

  const graphPanel = buildGraphPanel();
  const pagesPanel = buildPagesPanel();
  const logPanel = buildLogPanel();
  const panels = { graph: graphPanel.el, pages: pagesPanel.el, log: logPanel.el };
  for (const [id, el] of Object.entries(panels)) {
    el.setAttribute("role", "tabpanel");
    el.id = `panel-${id}`;
    el.setAttribute("aria-labelledby", `tab-${id}`);
    el.hidden = id !== tab;
  }

  const page = h("div", { class: "page job" },
    h("a", { class: "back-link", href: "#/history" }, icon("arrow-left"), "History"),
    h("header", { class: "job-head" },
      h("div", { class: "job-head-main" }, h("div", { class: "job-title-row" }, title, badgeSlot), startUrl),
      actions),
    bannerSlot,
    trail.el,
    h("div", { class: "job-grid" },
      h("div", { class: "job-main" }, tablist, graphPanel.el, pagesPanel.el, logPanel.el),
      side));
  root.append(page);

  // ---------------------------------------------------------------- builders
  function buildTrail() {
    const meta = {
      count: h("span", { class: "trail-count num" }),
      queue: h("span", { class: "trail-queue num dim" }),
      time: h("span", { class: "trail-time num dim" }),
    };
    const bar = h("div", { class: "trail-track", role: "progressbar", "aria-label": "Pages recorded", "aria-valuemin": "0" },
      h("div", { class: "trail-line" }), h("div", { class: "trail-fill" }), h("div", { class: "trail-head" }), h("div", { class: "trail-end" }));
    const el = h("section", { class: "trail", "aria-label": "Crawl progress" }, bar, h("div", { class: "trail-meta" }, meta.count, meta.queue, meta.time));
    return { el, bar, meta };
  }

  function buildLedger() {
    const rows = [
      { key: "pages", label: "Pages recorded", get: (d, l) => fmtNum(d.pages_crawled) + (l.limit ? ` of ${fmtNum(l.limit)}` : "") },
      { key: "ok", label: "Responded 2xx", get: (d) => fmtNum(d.summary?.ok) },
      { key: "redirects", label: "Redirects 3xx", get: (d) => fmtNum(d.summary?.redirects) },
      { key: "http", label: "HTTP errors 4xx and 5xx", get: (d) => fmtNum(d.summary?.http_errors), tone: (d) => (d.summary?.http_errors ? "error" : "") },
      { key: "failed", label: "Failed requests", get: (d) => fmtNum(d.summary?.failed), tone: (d) => (d.summary?.failed ? "error" : ""), hint: "No HTTP response: timeouts, refused connections, DNS failures, blocked targets, or robots.txt that could not be read." },
      { key: "skipped", label: "Skipped", get: (d) => fmtNum(d.skipped_count), hint: "Not fetched or not recorded: blocked by robots.txt, not HTML, or a link to a refused address." },
      { key: "discovered", label: "URLs discovered", get: (d) => fmtNum(d.discovered_count) },
      { key: "queued", label: "Waiting in queue", get: (d) => fmtNum(d.live?.queued), live: true },
      { key: "avg", label: "Average response", get: (d) => fmtSeconds(d.summary?.avg_response_time) },
      { key: "bytes", label: "Downloaded", get: (d) => fmtBytes(d.summary?.total_bytes) },
      { key: "depth", label: "Deepest page", get: (d) => `${fmtNum(d.summary?.max_depth)} clicks` },
      { key: "rate", label: "Pages per second", get: (d) => String(d.live?.pages_per_second ?? 0), live: true },
      { key: "time", label: "Elapsed", get: (d) => elapsed(d) },
    ];
    const cells = {};
    const el = h("dl", { class: "ledger" }, rows.flatMap((r) => {
      const dt = h("dt", { title: r.hint || null }, r.label);
      const dd = h("dd", { class: "num" });
      cells[r.key] = { dt, dd, row: r };
      return [dt, dd];
    }));
    return {
      el,
      update(d) {
        const limit = d.config?.max_pages || d.live?.max_pages || 0;
        const running = d.status === "running";
        for (const { dt, dd, row } of Object.values(cells)) {
          const hide = row.live && !running;
          dt.hidden = hide; dd.hidden = hide;
          dd.textContent = row.get(d, { limit });
          dd.dataset.tone = row.tone ? row.tone(d) : "";
        }
        cells.time.dt.textContent = running ? "Elapsed" : "Duration";
      },
    };
  }

  function elapsed(d) {
    if (d.status === "running" && d.live) return fmtDuration(d.live.elapsed);
    if (d.finished_at && d.created_at) return fmtDuration((new Date(d.finished_at) - new Date(d.created_at)) / 1000);
    return d.live ? fmtDuration(d.live.elapsed) : "n/a";
  }

  function buildGraphPanel() {
    const view = h("div", { class: "graph-view" });
    const empty = h("div", { class: "graph-empty" }, h("div", { class: "spinner" }), h("p", null, "The graph appears as pages are crawled."));
    const note = h("p", { class: "hint graph-note" });
    const announce = h("div", { class: "sr-only", "aria-live": "polite" });
    const tools = h("div", { class: "graph-tools" },
      h("button", { class: "btn btn-secondary btn-icon btn-sm", type: "button", "aria-label": "Zoom in", onclick: () => graph?.zoomBy(1.3) }, icon("chevron-up")),
      h("button", { class: "btn btn-secondary btn-icon btn-sm", type: "button", "aria-label": "Zoom out", onclick: () => graph?.zoomBy(0.77) }, icon("chevron-down")),
      h("button", { class: "btn btn-secondary btn-icon btn-sm", type: "button", "aria-label": "Fit all pages in view", title: "Fit to view", onclick: () => graph?.fit() }, icon("maximize")));
    const legend = h("ul", { class: "legend", "aria-label": "Legend" },
      h("li", null, h("span", { class: "swatch", dataset: { tone: "ok" } }), "2xx"),
      h("li", null, h("span", { class: "swatch", dataset: { tone: "redirect" } }), "3xx"),
      h("li", null, h("span", { class: "swatch", dataset: { tone: "error" } }), "4xx, 5xx, failed"),
      h("li", null, h("span", { class: "swatch ring" }), "Start page"),
      h("li", { class: "faint" }, "Rings mark clicks from the start page"));
    const el = h("div", { class: "graph-stage" },
      h("div", { class: "graph-frame" }, view, tools, empty), legend, note,
      h("p", { class: "hint" }, "Drag pages to move them, scroll to zoom, select a page to inspect it. Edges are links between pages that were crawled. The Pages tab lists the same data as a table."),
      announce);
    return { el, view, empty, note, announce };
  }

  function buildPagesPanel() {
    const search = h("input", { class: "input input-sm", type: "search", placeholder: "Filter by URL or title", "aria-label": "Filter pages by URL or title", autocomplete: "off", maxlength: "200" });
    const status = h("select", { class: "select input-sm", "aria-label": "Filter by response" },
      h("option", { value: "all" }, "All responses"), h("option", { value: "ok" }, "Success (2xx)"),
      h("option", { value: "redirect" }, "Redirects (3xx)"), h("option", { value: "error" }, "Errors and failures"));
    const onSearch = debounce(() => { query.search = search.value.trim(); query.offset = 0; loadResults(); }, 250);
    search.addEventListener("input", onSearch);
    status.addEventListener("change", () => { query.status = status.value; query.offset = 0; loadResults(); });

    const headRow = h("tr", null, COLUMNS.map((c) => {
      const btn = h("button", { class: "th-sort", type: "button", onclick: () => sortBy(c) }, c.label, icon("arrow-up-down"));
      return h("th", { scope: "col", class: c.numeric ? "num" : "", dataset: { key: c.key } }, btn);
    }));
    const tbody = h("tbody");
    const table = h("table", null, h("caption", { class: "sr-only" }, "Pages found by this crawl"), h("thead", null, headRow), tbody);
    const wrap = h("div", { class: "table-wrap pages-table" }, table);
    const info = h("span", { class: "num", "aria-live": "polite" });
    const prev = h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => { query.offset = Math.max(0, query.offset - PAGE_SIZE); loadResults(); } }, icon("chevron-left"), "Previous");
    const next = h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => { query.offset += PAGE_SIZE; loadResults(); } }, "Next", icon("chevron-right"));
    const pager = h("div", { class: "pager" }, info, h("div", { class: "pager-buttons" }, prev, next));
    const message = h("div", { class: "pages-message" });
    const el = h("div", { class: "pages-panel" },
      h("div", { class: "toolbar" }, h("div", { class: "grow" }, search), status), wrap, message, pager);
    return { el, tbody, info, prev, next, wrap, message, pager, headRow, search, status };
  }

  function buildLogPanel() {
    const body = h("div", { class: "console", role: "log", "aria-live": "off", tabindex: "0", "aria-label": "Crawl log" });
    const hint = h("p", { class: "console-empty" }, "Waiting for log lines");
    body.append(hint);
    const followBox = h("input", { type: "checkbox", role: "switch", checked: true });
    followBox.addEventListener("change", () => { follow = followBox.checked; if (follow) body.scrollTop = body.scrollHeight; });
    body.addEventListener("scroll", () => {
      const atEnd = body.scrollTop + body.clientHeight >= body.scrollHeight - 24;
      if (!atEnd && follow) { follow = false; followBox.checked = false; }
    });
    const clearBtn = h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => { clear(body); logLines = 0; body.append(hint); hint.textContent = "Log cleared"; } }, "Clear");
    const el = h("div", { class: "log-panel" },
      h("div", { class: "toolbar" },
        h("label", { class: "switch switch-inline" }, followBox, h("span", { class: "switch-track" }), h("span", { class: "switch-copy" }, h("strong", null, "Follow new lines"))),
        clearBtn),
      body,
      h("p", { class: "hint" }, "The live log is kept in server memory. It shows lines for crawls run since the server started."));
    return { el, body, hint, followBox };
  }

  // ---------------------------------------------------------------- rendering
  function renderHead() {
    const d = detail;
    const host = hostOf(d.start_url);
    clear(title); title.append(host);
    startUrl.replaceChildren(urlText(d.start_url));
    ctx.setTitle(`Crawl of ${host}`);
    badgeSlot.replaceChildren(sessionBadge(d.status));

    const running = d.status === "running";
    const hasPages = d.pages_crawled > 0;
    const items = [];
    if (running) {
      items.push(h("button", { class: "btn btn-danger", type: "button", onclick: stopCrawl }, icon("square"), "Stop crawl"));
    }
    if (hasPages) {
      items.push(
        h("a", { class: "btn btn-secondary", href: api.exportUrl(sessionId, "csv"), download: "" }, icon("download"), "CSV"),
        h("a", { class: "btn btn-secondary", href: api.exportUrl(sessionId, "json"), download: "" }, icon("download"), "JSON"));
    }
    actions.replaceChildren(...items);
  }

  function renderTrail() {
    const d = detail;
    const limit = d.config?.max_pages || d.live?.max_pages || 0;
    const running = d.status === "running";
    const p = limit ? Math.min(1, d.pages_crawled / limit) : 0;
    trail.el.style.setProperty("--p", String(p));
    trail.el.dataset.state = running ? "running" : d.status;
    trail.bar.setAttribute("aria-valuenow", String(d.pages_crawled));
    if (limit) trail.bar.setAttribute("aria-valuemax", String(limit));
    trail.meta.count.textContent = limit
      ? `${fmtNum(d.pages_crawled)} of ${fmtNum(limit)} pages`
      : `${fmtNum(d.pages_crawled)} pages`;
    trail.meta.queue.textContent = running && d.live ? `${fmtNum(d.live.queued)} waiting · ${fmtNum(d.live.active)} in flight` : "";
    trail.meta.time.textContent = elapsed(d);
  }

  function renderBanner() {
    const d = detail;
    clear(bannerSlot);
    if (d.status === "running") return;
    const limit = d.config?.max_pages;
    let tone = "ok", heading = "Crawl completed", text;
    if (d.status === "completed") {
      if (limit && d.pages_crawled >= limit) text = `Stopped at the page limit of ${fmtNum(limit)}. There may be more pages on the site.`;
      else if (d.config) text = `No further links to follow within a depth of ${d.config.max_depth}${d.config.stay_on_domain ? " on this site" : ""}.`;
      else text = "The crawl finished.";
    } else if (d.status === "stopped") {
      tone = "warn"; heading = "Crawl stopped";
      text = d.live?.stop_reason ? `Ended early: ${d.live.stop_reason}.` : "The crawl ended before it ran out of links. Pages recorded so far are kept.";
    } else if (d.status === "failed") {
      tone = "error"; heading = "Crawl failed"; text = d.error_message || "The crawl stopped because of an internal error.";
    } else if (d.status === "interrupted") {
      tone = "warn"; heading = "Crawl interrupted"; text = "The server stopped while this crawl was running. Pages recorded before that are kept.";
    }
    const good = (d.summary?.ok || 0) + (d.summary?.redirects || 0);
    if (d.status === "completed" && d.pages_crawled > 0 && good === 0) {
      tone = "warn"; heading = "Crawl finished without a successful response";
      text = "Every request failed or returned an error. Open the Pages tab and select a row to see the reason.";
    }
    if (!d.pages_crawled) {
      text += d.skipped_count ? " Nothing was recorded; the start page may be disallowed by robots.txt." : " Nothing was recorded. Check the Log tab for the reason.";
    }
    bannerSlot.append(h("div", { class: "banner", dataset: { tone }, role: "status" },
      icon(tone === "ok" ? "circle-check" : tone === "error" ? "circle-alert" : "triangle-alert"),
      h("div", { class: "banner-body" }, h("strong", null, heading), h("span", null, text))));
  }

  function renderConfig() {
    const c = detail.config;
    if (!c) { configBox.hidden = true; return; }
    configBox.hidden = false;
    const yes = (v) => (v ? "Yes" : "No");
    configBox.replaceChildren(
      h("summary", null, "Crawl settings"),
      h("dl", { class: "ledger small" },
        h("dt", null, "Started"), h("dd", null, fmtDateTime(detail.created_at)),
        h("dt", null, "Link depth"), h("dd", { class: "num" }, String(c.max_depth)),
        h("dt", null, "Page limit"), h("dd", { class: "num" }, fmtNum(c.max_pages)),
        h("dt", null, "Concurrent requests"), h("dd", { class: "num" }, String(c.concurrency)),
        h("dt", null, "Delay per host"), h("dd", { class: "num" }, `${Number(c.crawl_delay).toFixed(1)} s`),
        h("dt", null, "Stay on site"), h("dd", null, yes(c.stay_on_domain)),
        h("dt", null, "Respect robots.txt"), h("dd", null, yes(c.respect_robots))));
  }

  function renderAll() {
    renderHead(); renderTrail(); renderBanner(); renderConfig();
    ledger.update(detail);
    tabButtons.pages.count.textContent = fmtNum(detail.pages_crawled);
  }

  // ---------------------------------------------------------------- data
  async function poll() {
    clearTimeout(pollTimer);
    try {
      const d = await api.session(sessionId, pollController.signal);
      if (destroyed) return;
      const first = detail === null;
      detail = d;
      renderAll();
      if (first || d.pages_crawled !== lastCount || d.status !== lastStatus) refreshPanel();
      if (lastStatus === "running" && d.status !== "running") {
        toast(`Crawl ${d.status}: ${fmtNum(d.pages_crawled)} pages recorded`, d.status === "failed" ? "error" : "ok");
        pokeStatus();
        if (tab !== "log") setTimeout(closeLog, 1500);
      }
      lastCount = d.pages_crawled; lastStatus = d.status;
      if (first && d.status === "running") connectLog();
      if (d.status === "running") pollTimer = setTimeout(poll, 1200);
    } catch (err) {
      if (err.name === "AbortError" || destroyed) return;
      if (err.status === 404) return renderMissing();
      if (!detail) {
        clear(title); title.append("Crawl");
        bannerSlot.replaceChildren(h("div", { class: "banner", dataset: { tone: "error" }, role: "alert" }, icon("circle-alert"),
          h("div", { class: "banner-body" }, h("strong", null, "Could not load this crawl"), h("span", null, err.message)),
          h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: poll }, icon("rotate-cw"), "Retry")));
      } else {
        pollTimer = setTimeout(poll, 3000); // transient failure while a crawl is running
      }
    }
  }

  function renderMissing() {
    clear(page);
    page.append(h("div", { class: "empty" },
      h("div", { class: "empty-icon" }, icon("network")),
      h("h1", { class: "empty-title", "data-autofocus": true }, "This crawl does not exist"),
      h("p", null, "It may have been deleted, or the link may be wrong."),
      h("a", { class: "btn btn-secondary", href: "#/history" }, "Open the history")));
    ctx.setTitle("Crawl not found");
  }

  async function stopCrawl(event) {
    const btn = event.currentTarget;
    btn.setAttribute("aria-busy", "true");
    try {
      await api.stop();
      toast("Stopping. Requests already in flight will finish first.", "info");
      poll();
    } catch (err) {
      btn.removeAttribute("aria-busy");
      toast(err.message, "error");
    }
  }

  function refreshPanel() {
    if (tab === "graph") loadGraph();
    else if (tab === "pages") loadResults();
  }

  async function loadGraph() {
    try {
      const data = await api.graph(sessionId);
      if (destroyed) return;
      if (!graph) {
        graph = createGraph(graphPanel.view, {
          onSelect: (node) => (node ? selectPage({ id: node.pageId, url: node.id }, { focus: false }) : inspector.close(false)),
          onFocus: (node) => { graphPanel.announce.textContent = `${node.label}, ${node.status || "failed"}, ${node.depth} clicks from the start page`; },
        });
        if (selected) graph.select(selected.url);
      }
      graph.setData(data);
      graphPanel.empty.hidden = data.nodes.length > 0;
      graphPanel.note.textContent = data.truncated ? `Showing the first ${fmtNum(data.nodes.length)} of ${fmtNum(data.total_nodes)} pages.` : "";
    } catch (err) {
      if (!destroyed) toast(`Could not load the graph: ${err.message}`, "error");
    }
  }

  function sortBy(column) {
    if (query.sort === column.key) query.order = query.order === "asc" ? "desc" : "asc";
    else { query.sort = column.key; query.order = column.first; }
    query.offset = 0;
    loadResults();
  }

  function paintSortHeaders() {
    for (const th of pagesPanel.headRow.children) {
      const active = th.dataset.key === query.sort;
      if (active) th.setAttribute("aria-sort", query.order === "asc" ? "ascending" : "descending");
      else th.removeAttribute("aria-sort");
      const svg = th.querySelector("svg");
      const name = active ? (query.order === "asc" ? "arrow-up" : "arrow-down") : "arrow-up-down";
      th.querySelector("button").replaceChild(icon(name), svg);
    }
  }

  async function loadResults() {
    resultsController?.abort();
    resultsController = new AbortController();
    paintSortHeaders();
    try {
      const data = await api.results({
        session_id: sessionId, limit: PAGE_SIZE, offset: query.offset, search: query.search,
        status: query.status, sort: query.sort, order: query.order,
      }, resultsController.signal);
      if (destroyed) return;
      renderRows(data);
    } catch (err) {
      if (err.name === "AbortError" || destroyed) return;
      pagesPanel.message.replaceChildren(h("div", { class: "banner", dataset: { tone: "error" }, role: "alert" }, icon("circle-alert"),
        h("div", { class: "banner-body" }, h("strong", null, "Could not load pages"), h("span", null, err.message)),
        h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: loadResults }, icon("rotate-cw"), "Retry")));
    }
  }

  function renderRows({ pages, total }) {
    const p = pagesPanel;
    p.message.replaceChildren();
    clear(p.tbody);
    const filtered = Boolean(query.search) || query.status !== "all";
    p.wrap.hidden = total === 0;
    p.pager.hidden = total === 0;
    if (total === 0) {
      if (filtered) {
        p.message.append(h("div", { class: "empty compact" }, h("p", null, "No pages match these filters."),
          h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => { p.search.value = ""; p.status.value = "all"; query.search = ""; query.status = "all"; loadResults(); } }, "Clear filters")));
      } else if (detail?.status === "running") {
        p.message.append(h("div", { class: "empty compact" }, h("div", { class: "spinner" }), h("p", null, "Waiting for the first response.")));
      } else {
        p.message.append(h("div", { class: "empty compact" }, h("p", null, "No pages were recorded for this crawl.")));
      }
      return;
    }
    for (const pg of pages) {
      const tr = h("tr", { dataset: { id: pg.id } },
        h("td", null, statusBadge(pg.status_code)),
        h("td", { class: "cell-url" },
          h("button", { class: "row-link", type: "button", title: pg.url, onclick: (e) => selectPage({ id: pg.id, url: pg.url }, { focus: true, from: e.currentTarget }) },
            h("span", { class: "ellipsis" }, urlText(pg.url, { compact: true })))),
        h("td", { class: "cell-title" }, h("span", { class: "ellipsis", title: pg.title || pg.error || null }, pg.title || h("span", { class: pg.status_code ? "faint" : "err-text" }, pg.status_code ? "none" : (pg.error || "failed")))),
        h("td", { class: "num" }, String(pg.depth)),
        h("td", { class: "num" }, pg.status_code ? fmtSeconds(pg.response_time) : h("span", { class: "faint" }, "n/a")),
        h("td", { class: "num" }, pg.status_code ? fmtBytes(pg.size_bytes) : h("span", { class: "faint" }, "n/a")),
        h("td", { class: "num" }, fmtNum(pg.word_count)),
        h("td", { class: "num" }, fmtNum(pg.outlinks_count)));
      p.tbody.append(tr);
    }
    const from = query.offset + 1, to = query.offset + pages.length;
    p.info.textContent = `${fmtNum(from)} to ${fmtNum(to)} of ${fmtNum(total)}`;
    p.prev.disabled = query.offset === 0;
    p.next.disabled = to >= total;
    markSelectedRow();
  }

  function markSelectedRow() {
    for (const tr of pagesPanel.tbody.children) {
      const on = selected && Number(tr.dataset.id) === selected.id;
      if (on) tr.setAttribute("aria-selected", "true"); else tr.removeAttribute("aria-selected");
    }
  }

  function selectPage(ref, { focus = false, from = null } = {}) {
    selected = ref;
    side.dataset.mode = "inspect";
    inspector.show(ref, { focus, from });
    graph?.select(ref.url);
    markSelectedRow();
  }

  // ---------------------------------------------------------------- log
  function connectLog() {
    if (eventSource || destroyed) return;
    eventSource = new EventSource(api.logStreamUrl(sessionId));
    eventSource.addEventListener("log", (e) => addLog(JSON.parse(e.data)));
    if (detail && detail.status !== "running") {
      setTimeout(() => { if (!logLines && logPanel.hint.isConnected) logPanel.hint.textContent = "No log lines in memory for this crawl. Logs are only kept while the server runs."; }, 1200);
    }
  }

  function closeLog() {
    eventSource?.close();
    eventSource = null;
  }

  function addLog(entry) {
    const body = logPanel.body;
    if (!logLines) logPanel.hint.remove();
    logLines += 1;
    body.append(h("div", { class: "log-line", dataset: { level: entry.level } },
      h("time", { class: "faint" }, fmtClock(entry.ts)),
      h("span", { class: "log-level" }, entry.level),
      h("span", { class: "log-msg" }, entry.message)));
    while (body.children.length > LOG_LIMIT) body.firstElementChild.remove();
    if (follow) body.scrollTop = body.scrollHeight;
  }

  // ---------------------------------------------------------------- tabs
  function selectTab(id) {
    tab = id;
    for (const t of tabs) {
      const { btn } = tabButtons[t.id];
      btn.setAttribute("aria-selected", String(t.id === id));
      btn.tabIndex = t.id === id ? 0 : -1;
      panels[t.id].hidden = t.id !== id;
    }
    if (id === "log") { connectLog(); logPanel.body.scrollTop = logPanel.body.scrollHeight; }
    else refreshPanel();
  }

  poll();

  return {
    destroy() {
      destroyed = true;
      clearTimeout(pollTimer);
      pollController.abort();
      resultsController?.abort();
      inspector.destroy();
      closeLog();
      graph?.destroy();
    },
  };
}
