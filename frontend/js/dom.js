import { ICONS } from "./icons.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const PROP_ATTRS = new Set(["value", "checked", "disabled", "hidden", "selected", "open", "indeterminate"]);

/** Hyperscript-style element builder. Strings become text nodes, so content is never parsed as HTML. */
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (PROP_ATTRS.has(key)) el[key] = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  append(el, children);
  return el;
}

export function append(parent, children) {
  const list = Array.isArray(children) ? children : [children];
  for (const child of list.flat(Infinity)) {
    if (child == null || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

export function icon(name, extraClass = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", `icon ${extraClass}`.trim());
  svg.innerHTML = ICONS[name] || ""; // ICONS is a static, trusted table (see icons.js)
  return svg;
}

let uid = 0;
export const nextId = (prefix = "id") => `${prefix}-${++uid}`;

// ---------- formatting ----------
const nf = new Intl.NumberFormat();
export const fmtNum = (n) => nf.format(Number.isFinite(n) ? n : 0);

export function fmtBytes(bytes) {
  const b = Number(bytes) || 0;
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(b < 10240 ? 1 : 0)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtSeconds(sec) {
  const s = Number(sec) || 0;
  if (s <= 0) return "0 ms";
  return s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(2)} s`;
}

export function fmtDuration(totalSeconds) {
  const t = Math.max(0, Math.round(Number(totalSeconds) || 0));
  const hh = Math.floor(t / 3600);
  const mm = Math.floor((t % 3600) / 60);
  const ss = t % 60;
  const pad = (v) => String(v).padStart(2, "0");
  return hh ? `${hh}:${pad(mm)}:${pad(ss)}` : `${mm}:${pad(ss)}`;
}

const dtf = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const tf = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export const fmtDateTime = (iso) => (iso ? dtf.format(new Date(iso)) : "");
export const fmtClock = (epochSeconds) => tf.format(new Date(epochSeconds * 1000));

export function relTime(iso) {
  if (!iso) return "";
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
  return fmtDateTime(iso);
}

export function statusTone(code) {
  if (code >= 200 && code < 300) return "ok";
  if (code >= 300 && code < 400) return "redirect";
  return "error";
}

/** Status code badge; 0 means the request itself failed (no HTTP response). */
export function statusBadge(code) {
  const failed = !code;
  return h("span", { class: "badge", dataset: { tone: statusTone(code) }, title: failed ? "Request failed, no HTTP response" : `HTTP ${code}` },
    failed ? "failed" : String(code));
}

const SESSION_LABEL = { running: "Running", completed: "Completed", stopped: "Stopped", failed: "Failed", interrupted: "Interrupted" };
const SESSION_TONE = { running: "ok", completed: "ok", stopped: "warn", failed: "error", interrupted: "warn" };

export function sessionBadge(status) {
  const live = status === "running";
  return h("span", { class: "badge badge-plain", dataset: { tone: SESSION_TONE[status] || "" } },
    live ? h("span", { class: "dot", dataset: { tone: "live" } }) : null,
    SESSION_LABEL[status] || status);
}

/** URL with the scheme muted and host emphasised. Built from text nodes only. */
export function urlText(url, { compact = false } = {}) {
  const match = /^(https?:\/\/)([^/?#]+)(.*)$/i.exec(url || "");
  if (!match) return h("span", null, url || "");
  return h("span", null, compact ? null : h("span", { class: "faint" }, match[1]), match[2], h("span", { class: "dim" }, match[3]));
}

export function hostOf(url) {
  try { return new URL(url).host; } catch { return url || ""; }
}

/** Only http(s) URLs are ever used as link targets. */
export const safeHref = (url) => (/^https?:\/\//i.test(url || "") ? url : null);

export function debounce(fn, ms) {
  let timer;
  const wrapped = (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
