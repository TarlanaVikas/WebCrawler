import { startAmbient } from "./ambient.js";
import { api } from "./api.js";
import { fmtNum, h, hostOf } from "./dom.js";
import { route, startRouter } from "./router.js";
import { app, startStatusPolling } from "./state.js";
import * as history from "./views/history.js";
import * as home from "./views/home.js";
import * as job from "./views/job.js";

route("home", /^\/?$/, home);
route("job", /^\/crawl\/([0-9a-f]{8})$/, job);
route("history", /^\/history$/, history);

// ---------- ambient background and the motion switch ----------
const ambient = startAmbient(document.getElementById("ambient"));
const motionToggle = document.getElementById("motion-toggle");
function paintMotion() {
  motionToggle.setAttribute("aria-pressed", String(ambient.motion));
  motionToggle.textContent = `Background motion: ${ambient.motion ? "on" : "off"}`;
}
motionToggle.addEventListener("click", () => { ambient.setMotion(!ambient.motion); paintMotion(); });
paintMotion();

// ---------- header status ----------
const slot = document.getElementById("status-slot");
function paintStatus({ server, status }) {
  let node;
  if (server === "down") {
    node = h("span", { class: "pulse-pill", role: "status" }, h("span", { class: "dot", dataset: { tone: "error" } }), "Server unreachable");
  } else if (status.is_running) {
    const limit = status.max_pages ? ` of ${fmtNum(status.max_pages)}` : "";
    node = h("a", { class: "pulse-pill", href: `#/crawl/${status.session_id}`, "aria-label": `Crawl in progress on ${hostOf(status.start_url)}, ${status.pages_crawled}${limit} pages. Open it.` },
      h("span", { class: "dot", dataset: { tone: "live" } }), "Crawling ", h("span", { class: "num" }, `${fmtNum(status.pages_crawled)}${limit}`));
  } else {
    node = h("span", { class: "pulse-pill" }, h("span", { class: "dot" }), server === "unknown" ? "Connecting" : "Idle");
  }
  slot.replaceChildren(node);
}
app.subscribe(paintStatus);
paintStatus(app.get());

// ---------- navigation state ----------
function paintNav(current) {
  for (const link of document.querySelectorAll("[data-nav]")) {
    if (link.dataset.nav === current.name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

startRouter(document.getElementById("main"), paintNav);
startStatusPolling();
api.health().then((health) => { document.getElementById("app-version").textContent = `v${health.version}`; }).catch(() => {});
