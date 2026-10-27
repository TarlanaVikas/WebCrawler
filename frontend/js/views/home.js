import { api } from "../api.js";
import { rangeField, switchField } from "../components/controls.js";
import { append, clear, fmtBytes, fmtNum, h, hostOf, icon, nextId, relTime, sessionBadge, urlText } from "../dom.js";
import { navigate } from "../router.js";
import { app, loadLimits, pokeStatus } from "../state.js";

const RECORD_FIELDS = [
  ["Response", "HTTP status, response time, size and content type. Redirects record where they led."],
  ["Content", "Title, meta description, word count and the first five headings of each level."],
  ["Metadata", "Every named meta tag and Open Graph property found in the head."],
  ["Links", "Outgoing links, resolved, normalised and de-duplicated, which also feed the link graph."],
  ["Position", "How many clicks the page is from the start URL."],
];

export function mount(root, ctx) {
  ctx.setTitle(null);
  const disposers = [];
  let limits = null;

  const page = h("div", { class: "page home" });
  root.append(page);

  // ---------- hero + launcher ----------
  const notice = h("div", { class: "launcher-notice" });
  const urlId = nextId("url");
  const urlInput = h("input", {
    id: urlId, class: "input input-lg", name: "start_url", type: "text", inputmode: "url", autocomplete: "off",
    autocapitalize: "off", spellcheck: "false", placeholder: "example.com or https://example.com/docs",
    "aria-describedby": `${urlId}-hint`, required: true,
  });
  const urlError = h("div", { class: "field-error", hidden: true, id: `${urlId}-error`, role: "alert" });
  const submit = h("button", { class: "btn btn-primary btn-lg", type: "submit" }, icon("play"), "Start crawl");
  const controls = h("div", { class: "launcher-controls", "aria-busy": "true" }, h("span", { class: "skeleton" }), h("span", { class: "skeleton" }));
  const formError = h("div", { class: "launcher-error" });
  let fields = null;

  const form = h("form", { class: "launcher", novalidate: true, "aria-label": "Start a crawl" },
    notice,
    h("div", { class: "launcher-row" },
      h("div", { class: "field grow" },
        h("label", { for: urlId }, "Start URL"),
        h("div", { class: "input-wrap" }, icon("globe"), urlInput),
        urlError,
        h("span", { class: "hint", id: `${urlId}-hint` }, "https is used when you leave out the scheme.")),
      h("div", { class: "launcher-submit" }, submit)),
    formError,
    controls,
    h("p", { class: "launcher-foot" }, icon("shield-check"),
      "Only crawl sites you are allowed to crawl. Private, loopback and cloud-metadata addresses are refused by the server."));

  page.append(
    h("section", { class: "hero" },
      h("div", { class: "hero-copy" },
        h("h1", { "data-autofocus": true }, h("span", null, "Follow every link."), h("span", { class: "hero-second" }, "Keep what each page says.")),
        h("p", { class: "lede" },
          "Web Crawler starts from one URL, follows links page by page within the limits you set, and records each page's status, title, headings, metadata and outgoing links. Watch the link graph grow while it works, then export the results.")),
      form));

  function setUrlError(message) {
    urlError.hidden = !message;
    clear(urlError);
    if (message) append(urlError, [icon("circle-alert"), h("span", null, message)]);
    urlInput.setAttribute("aria-invalid", message ? "true" : "false");
    if (message) urlInput.setAttribute("aria-describedby", `${urlId}-error ${urlId}-hint`);
    else urlInput.setAttribute("aria-describedby", `${urlId}-hint`);
  }

  function showBanner(target, tone, title, text, action) {
    clear(target);
    append(target, h("div", { class: "banner", dataset: { tone }, role: tone === "error" ? "alert" : "status" },
      icon(tone === "error" ? "circle-alert" : tone === "warn" ? "triangle-alert" : "info"),
      h("div", { class: "banner-body" }, h("strong", null, title), text ? h("span", null, text) : null), action));
  }

  function buildControls(l) {
    const depth = rangeField({ label: "Link depth", hint: "Clicks away from the start page", min: l.max_depth.min, max: l.max_depth.max, value: l.max_depth.default, name: "max_depth" });
    const pageStep = l.max_pages.max > 200 ? 5 : 1; // keep the slider's min on its step grid so defaults are exact
    const pages = rangeField({ label: "Page limit", hint: "Stop after this many pages", min: pageStep > 1 ? pageStep : l.max_pages.min, max: l.max_pages.max, step: pageStep, value: l.max_pages.default, name: "max_pages", format: fmtNum });
    const workers = rangeField({ label: "Concurrent requests", hint: "Requests in flight at once", min: l.concurrency.min, max: l.concurrency.max, value: l.concurrency.default, name: "concurrency" });
    const delay = rangeField({ label: "Delay per host", hint: "Minimum gap between requests to one host", min: l.crawl_delay.min, max: l.crawl_delay.max, step: 0.1, value: l.crawl_delay.default, name: "crawl_delay", format: (v) => `${v.toFixed(1)} s` });
    const scope = switchField({ label: "Stay on the starting site", hint: "Do not follow links to other hosts. www. counts as the same site.", checked: true, name: "stay_on_domain" });
    const robots = switchField({ label: "Respect robots.txt", hint: "Skip pages the site asks crawlers to avoid.", checked: true, name: "respect_robots" });
    fields = { depth, pages, workers, delay, scope, robots };
    controls.removeAttribute("aria-busy");
    controls.replaceChildren(
      h("div", { class: "launcher-grid" }, depth.el, pages.el, workers.el, delay.el),
      h("div", { class: "launcher-switches" }, scope.el, robots.el));
  }

  async function init() {
    try {
      limits = await loadLimits();
      buildControls(limits);
      if (limits.allow_private_networks) {
        showBanner(formError, "warn", "Private-network crawling is enabled on this server",
          "Loopback and private addresses can be crawled. This is meant for development and testing only.");
      }
    } catch (err) {
      controls.removeAttribute("aria-busy");
      controls.replaceChildren(h("div", { class: "banner", dataset: { tone: "error" }, role: "alert" }, icon("circle-alert"),
        h("div", { class: "banner-body" }, h("strong", null, "Cannot reach the Web Crawler server"), h("span", null, err.message)),
        h("button", { class: "btn btn-secondary btn-sm", type: "button", onclick: () => { controls.replaceChildren(h("span", { class: "skeleton" })); controls.setAttribute("aria-busy", "true"); init(); } }, icon("rotate-cw"), "Retry")));
    }
  }

  function renderRunning(status) {
    const running = status.is_running;
    submit.disabled = running || !fields;
    clear(notice);
    if (running) {
      append(notice, h("div", { class: "banner", dataset: { tone: "info" }, role: "status" }, icon("info"),
        h("div", { class: "banner-body" },
          h("strong", null, "A crawl is already running"),
          h("span", null, `${hostOf(status.start_url)}: ${fmtNum(status.pages_crawled)} of ${fmtNum(status.max_pages)} pages. Only one crawl runs at a time.`)),
        h("a", { class: "btn btn-secondary btn-sm", href: `#/crawl/${status.session_id}` }, "View live crawl")));
    }
  }
  disposers.push(app.subscribe((s) => renderRunning(s.status)));
  renderRunning(app.get().status);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setUrlError(null);
    if (!limits?.allow_private_networks) clear(formError);
    const raw = urlInput.value.trim();
    if (!raw) { setUrlError("Enter the address of the site to crawl."); urlInput.focus(); return; }
    const startUrl = /^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`;
    const payload = {
      start_url: startUrl,
      max_depth: Number(fields.depth.input.value),
      max_pages: Number(fields.pages.input.value),
      concurrency: Number(fields.workers.input.value),
      crawl_delay: Number(fields.delay.input.value),
      stay_on_domain: fields.scope.input.checked,
      respect_robots: fields.robots.input.checked,
    };
    submit.setAttribute("aria-busy", "true");
    submit.disabled = true;
    try {
      const result = await api.start(payload);
      pokeStatus();
      navigate(`/crawl/${result.session_id}`);
    } catch (err) {
      submit.removeAttribute("aria-busy");
      submit.disabled = app.get().status.is_running;
      if (err.code === "crawl_running") {
        showBanner(formError, "warn", "A crawl is already running", "Wait for it to finish or stop it first.",
          h("a", { class: "btn btn-secondary btn-sm", href: `#/crawl/${err.data.session_id}` }, "View it"));
        pokeStatus();
      } else if (err.fields?.start_url) {
        setUrlError(err.fields.start_url);
        urlInput.focus();
      } else {
        showBanner(formError, "error", "The crawl could not be started", err.message);
      }
    }
  });
  urlInput.addEventListener("input", () => { if (!urlError.hidden) setUrlError(null); });

  // ---------- below the fold ----------
  const recent = h("div", { class: "recent-list", "aria-busy": "true" },
    h("span", { class: "skeleton" }), h("span", { class: "skeleton" }), h("span", { class: "skeleton" }));
  const safety = h("ul", { class: "plain-list" });

  page.append(h("div", { class: "below" },
    h("section", { class: "below-col", "aria-labelledby": "recent-h" },
      h("div", { class: "section-head" },
        h("h2", { id: "recent-h" }, "Recent crawls"),
        h("a", { href: "#/history", class: "text-link" }, "All history")),
      recent),
    h("section", { class: "below-col", "aria-labelledby": "record-h" },
      h("h2", { id: "record-h" }, "What a crawl records"),
      h("dl", { class: "record-list" }, RECORD_FIELDS.flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)]))),
    h("section", { class: "below-col", "aria-labelledby": "safe-h" },
      h("h2", { id: "safe-h" }, "How requests are kept in check"),
      safety)));

  loadLimits().then((l) => {
    clear(safety);
    append(safety, [
      "Only http and https URLs, and none that contain credentials.",
      "Loopback, private, link-local and cloud-metadata addresses are refused, re-checked after DNS and on every redirect.",
      `Redirects are followed manually, at most ${l.max_redirects} per request, and out-of-scope redirects are not followed.`,
      "robots.txt is honoured by default, including Crawl-delay, and requests to one host are spaced apart.",
      `Responses over ${fmtBytes(l.max_response_bytes)} are cut off and requests time out after ${l.request_timeout} s.`,
      `A crawl ends after ${Math.round(l.max_runtime_seconds / 60)} minutes at most.`,
    ].map((t) => h("li", null, icon("check"), t)));
  }).catch(() => { clear(safety); append(safety, h("li", { class: "faint" }, "Unavailable while the server cannot be reached.")); });

  async function loadRecent() {
    try {
      const { sessions } = await api.sessions({ limit: 5 });
      recent.removeAttribute("aria-busy");
      clear(recent);
      if (!sessions.length) {
        append(recent, h("div", { class: "empty compact" },
          h("p", null, "No crawls yet. Finished crawls are kept here so you can reopen their results.")));
        return;
      }
      append(recent, h("ul", { class: "recent" }, sessions.map((s) =>
        h("li", null, h("a", { class: "recent-item", href: `#/crawl/${s.session_id}` },
          h("span", { class: "recent-url mono" }, urlText(s.start_url)),
          h("span", { class: "recent-meta" }, sessionBadge(s.status),
            h("span", { class: "num dim" }, `${fmtNum(s.pages_crawled)} pages`),
            h("span", { class: "faint" }, relTime(s.created_at))))))));
    } catch (err) {
      recent.removeAttribute("aria-busy");
      clear(recent);
      append(recent, h("div", { class: "banner", dataset: { tone: "error" } }, icon("circle-alert"),
        h("div", { class: "banner-body" }, h("strong", null, "Could not load recent crawls"), h("span", null, err.message))));
    }
  }

  init();
  loadRecent();

  return { destroy() { disposers.forEach((d) => d()); } };
}
