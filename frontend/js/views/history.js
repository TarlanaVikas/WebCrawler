import { api } from "../api.js";
import { confirmDialog } from "../components/confirm.js";
import { toast } from "../components/toast.js";
import { append, clear, fmtDateTime, fmtDuration, fmtNum, h, hostOf, icon, relTime, sessionBadge, urlText } from "../dom.js";

const PAGE_SIZE = 20;

export function mount(root, ctx) {
  ctx.setTitle("Crawl history");
  let offset = 0;
  let timer = null;
  let controller = null;
  let destroyed = false;

  const title = h("h1", { "data-autofocus": true }, "Crawl history");
  const count = h("p", { class: "dim", "aria-live": "polite" });
  const content = h("div", { class: "history-content", "aria-busy": "true" },
    h("span", { class: "skeleton tall" }), h("span", { class: "skeleton tall" }), h("span", { class: "skeleton tall" }));

  root.append(h("div", { class: "page" },
    h("header", { class: "page-head" },
      h("div", null, title, count),
      h("a", { class: "btn btn-primary", href: "#/" }, icon("play"), "New crawl")),
    content));

  function duration(s) {
    if (s.status === "running") return "running";
    if (!s.finished_at || !s.created_at) return "n/a";
    return fmtDuration((new Date(s.finished_at) - new Date(s.created_at)) / 1000);
  }

  function emptyState() {
    return h("div", { class: "empty" },
      h("div", { class: "empty-icon" }, icon("history")),
      h("h3", null, "No crawls yet"),
      h("p", null, "When you run a crawl it is saved here with its pages, links and settings, so you can come back to the results."),
      h("a", { class: "btn btn-secondary", href: "#/" }, "Start a crawl"));
  }

  async function remove(session) {
    const ok = await confirmDialog({
      title: "Delete this crawl?",
      message: `The crawl of ${hostOf(session.start_url)} and its ${fmtNum(session.pages_crawled)} recorded pages will be removed permanently.`,
      confirmLabel: "Delete crawl",
    });
    if (!ok) return;
    try {
      await api.deleteSession(session.session_id);
      toast("Crawl deleted", "ok");
      if (offset > 0 && content.querySelectorAll("tbody tr").length === 1) offset = Math.max(0, offset - PAGE_SIZE);
      load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function table(sessions, total) {
    const rows = sessions.map((s) => h("tr", null,
      h("td", null, sessionBadge(s.status)),
      h("td", { class: "cell-url" },
        h("a", { class: "row-link", href: `#/crawl/${s.session_id}`, title: s.start_url }, h("span", { class: "ellipsis" }, urlText(s.start_url)))),
      h("td", { class: "num" }, fmtNum(s.pages_crawled)),
      h("td", { class: "num" }, s.error_count ? h("span", { class: "err-count" }, fmtNum(s.error_count)) : h("span", { class: "faint" }, "0")),
      h("td", { class: "num" }, duration(s)),
      h("td", null, h("time", { datetime: s.created_at, title: fmtDateTime(s.created_at) }, relTime(s.created_at))),
      h("td", { class: "actions" },
        h("a", { class: "btn btn-secondary btn-sm", href: `#/crawl/${s.session_id}`, "aria-label": `Open crawl of ${hostOf(s.start_url)}` }, "Open"),
        h("button", {
          class: "btn btn-ghost btn-sm btn-icon", type: "button", "aria-label": `Delete crawl of ${hostOf(s.start_url)}`,
          disabled: s.status === "running", title: s.status === "running" ? "Stop the crawl before deleting it" : "Delete crawl",
          onclick: () => remove(s),
        }, icon("trash-2")))));
    const from = offset + 1, to = offset + sessions.length;
    return [
      h("div", { class: "table-wrap" },
        h("table", null,
          h("caption", { class: "sr-only" }, "Saved crawls, newest first"),
          h("thead", null, h("tr", null,
            ["Status", "Start URL"].map((t) => h("th", { scope: "col" }, t)),
            ["Pages", "Errors", "Duration"].map((t) => h("th", { scope: "col", class: "num" }, t)),
            h("th", { scope: "col" }, "Started"),
            h("th", { scope: "col" }, h("span", { class: "sr-only" }, "Actions")))),
          h("tbody", null, rows))),
      h("div", { class: "pager" },
        h("span", { class: "num" }, `${fmtNum(from)} to ${fmtNum(to)} of ${fmtNum(total)}`),
        h("div", { class: "pager-buttons" },
          h("button", { class: "btn btn-secondary btn-sm", type: "button", disabled: offset === 0, onclick: () => { offset = Math.max(0, offset - PAGE_SIZE); load(); } }, icon("chevron-left"), "Previous"),
          h("button", { class: "btn btn-secondary btn-sm", type: "button", disabled: to >= total, onclick: () => { offset += PAGE_SIZE; load(); } }, "Next", icon("chevron-right")))),
    ];
  }

  async function load() {
    controller?.abort();
    controller = new AbortController();
    clearTimeout(timer);
    try {
      const { sessions, total } = await api.sessions({ limit: PAGE_SIZE, offset }, controller.signal);
      if (destroyed) return;
      content.removeAttribute("aria-busy");
      clear(content);
      count.textContent = total ? `${fmtNum(total)} saved ${total === 1 ? "crawl" : "crawls"}` : "";
      if (!sessions.length && offset > 0) { offset = 0; return load(); }
      append(content, sessions.length ? table(sessions, total) : emptyState());
      if (sessions.some((s) => s.status === "running")) timer = setTimeout(load, 3000);
    } catch (err) {
      if (err.name === "AbortError" || destroyed) return;
      content.removeAttribute("aria-busy");
      clear(content);
      append(content, h("div", { class: "banner", dataset: { tone: "error" }, role: "alert" }, icon("circle-alert"),
        h("div", { class: "banner-body" }, h("strong", null, "Could not load the history"), h("span", null, err.message)),
        h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: load }, icon("rotate-cw"), "Retry")));
    }
  }

  load();
  return { destroy() { destroyed = true; clearTimeout(timer); controller?.abort(); } };
}
