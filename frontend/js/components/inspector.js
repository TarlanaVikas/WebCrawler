import { api } from "../api.js";
import { append, clear, copyText, fmtBytes, fmtNum, fmtSeconds, h, icon, safeHref, statusBadge } from "../dom.js";
import { toast } from "./toast.js";

const LINK_LIMIT = 200;

function fact(label, value) {
  return [h("dt", null, label), h("dd", { class: "num" }, value)];
}

/**
 * Side panel with everything extracted from one page. All crawled text is inserted as text
 * nodes (never as HTML), so a hostile title or heading cannot inject markup.
 */
export function createInspector({ sessionId, onClose }) {
  const body = h("div", { class: "insp-body" });
  const closeBtn = h("button", { class: "btn btn-ghost btn-icon", type: "button", "aria-label": "Close page details", onclick: () => close() }, icon("x"));
  const heading = h("h2", { class: "insp-heading", tabindex: "-1" }, "Page details");
  const el = h("aside", { class: "inspector", hidden: true, "aria-label": "Page details" },
    h("div", { class: "insp-head" }, heading, closeBtn), body);
  let controller = null;
  let opener = null;

  function close(restore = true) {
    controller?.abort();
    el.hidden = true;
    el.dataset.open = "false";
    if (restore && opener?.isConnected) opener.focus();
    onClose?.();
  }

  el.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });

  function render(page) {
    const href = safeHref(page.url);
    const links = page.links || [];
    const headings = Object.entries(page.headings || {});
    const meta = Object.entries(page.meta_tags || {});

    clear(body);
    append(body, [
      h("div", { class: "insp-status" },
        statusBadge(page.status_code),
        h("span", { class: "faint" }, `depth ${page.depth}`)),
      h("h3", { class: "insp-title" }, page.title || h("span", { class: "faint" }, "No title")),
      h("div", { class: "insp-url" },
        href
          ? h("a", { href, target: "_blank", rel: "noopener noreferrer nofollow", class: "mono" }, page.url, icon("external-link"))
          : h("span", { class: "mono" }, page.url),
        h("button", {
          class: "btn btn-ghost btn-sm", type: "button",
          onclick: async () => toast((await copyText(page.url)) ? "URL copied" : "Could not copy the URL", "info"),
        }, icon("copy"), "Copy URL")),
      page.error
        ? h("div", { class: "banner", dataset: { tone: page.status_code ? "warn" : "error" } },
            icon("triangle-alert"), h("div", { class: "banner-body" }, h("strong", null, page.status_code ? "Note" : "Request failed"), h("span", null, page.error)))
        : null,
      page.redirect_url
        ? h("p", { class: "insp-redirect" }, h("span", { class: "dim" }, "Redirect target "), h("span", { class: "mono" }, page.redirect_url))
        : null,
      h("dl", { class: "facts" },
        fact("Response time", fmtSeconds(page.response_time)),
        fact("Size", fmtBytes(page.size_bytes)),
        fact("Words", fmtNum(page.word_count)),
        fact("Outgoing links", fmtNum(page.outlinks_count ?? links.length)),
        h("dt", null, "Content type"), h("dd", { class: "mono" }, (page.content_type || "none").split(";")[0])),
      page.description ? h("section", { class: "insp-section" }, h("h4", null, "Description"), h("p", null, page.description)) : null,
      headings.length
        ? h("section", { class: "insp-section" }, h("h4", null, "Headings"),
            h("ul", { class: "heading-list" },
              headings.flatMap(([level, texts]) => texts.map((t) => h("li", null, h("span", { class: "badge" }, level.toUpperCase()), h("span", null, t))))))
        : null,
      meta.length
        ? h("details", { class: "insp-section" }, h("summary", null, `Meta tags (${meta.length})`),
            h("dl", { class: "meta-list" }, meta.flatMap(([k, v]) => [h("dt", { class: "mono" }, k), h("dd", null, v)])))
        : null,
      links.length
        ? h("details", { class: "insp-section" }, h("summary", null, `Outgoing links (${links.length})`),
            h("ul", { class: "link-list" }, links.slice(0, LINK_LIMIT).map((l) => h("li", { class: "mono" }, l))),
            links.length > LINK_LIMIT ? h("p", { class: "hint" }, `Showing the first ${LINK_LIMIT}. The JSON export contains all of them.`) : null)
        : null,
    ]);
  }

  async function show(ref, { focus = false, from = null } = {}) {
    controller?.abort();
    controller = new AbortController();
    opener = from;
    el.hidden = false;
    el.dataset.open = "true";
    body.replaceChildren(h("div", { class: "insp-loading", role: "status" }, h("span", { class: "spinner" }), "Loading page details"));
    if (focus) heading.focus();
    try {
      render(await api.page(ref.id, sessionId, controller.signal));
    } catch (err) {
      if (err.name === "AbortError") return;
      body.replaceChildren(h("div", { class: "banner", dataset: { tone: "error" } }, icon("circle-alert"),
        h("div", { class: "banner-body" }, h("strong", null, "Could not load this page"), h("span", null, err.message))));
    }
  }

  return { el, show, close, destroy: () => controller?.abort(), get isOpen() { return !el.hidden; } };
}
