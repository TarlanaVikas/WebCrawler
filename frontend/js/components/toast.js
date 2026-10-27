import { h, icon } from "../dom.js";

const ICONS = { ok: "circle-check", error: "circle-alert", info: "info" };

export function toast(message, tone = "info") {
  const region = document.getElementById("toasts");
  if (!region) return;
  let timer;
  const close = () => { clearTimeout(timer); node.remove(); };
  const node = h("div", { class: "toast", dataset: { tone } },
    icon(ICONS[tone] || "info"),
    h("p", null, message),
    h("button", { class: "btn btn-ghost btn-icon btn-sm", type: "button", "aria-label": "Dismiss notification", onclick: close }, icon("x")));
  const start = () => { timer = setTimeout(close, tone === "error" ? 9000 : 5000); };
  node.addEventListener("mouseenter", () => clearTimeout(timer));
  node.addEventListener("mouseleave", start);
  region.append(node);
  while (region.children.length > 4) region.firstElementChild.remove();
  start();
}
