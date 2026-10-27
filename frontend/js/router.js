/** Minimal hash router. Each view exports mount(container, ctx) and returns { destroy() }. */
const routes = [];
let current = null;

export function route(name, pattern, view) {
  routes.push({ name, pattern, view });
}

export function navigate(path) {
  const target = `#${path}`;
  if (window.location.hash === target) resolve();
  else window.location.hash = target;
}

function match() {
  const path = window.location.hash.replace(/^#/, "") || "/";
  for (const r of routes) {
    const m = r.pattern.exec(path);
    if (m) return { ...r, params: m.slice(1), path };
  }
  return { name: "notfound", view: null, params: [], path };
}

let outlet;
let onChange = () => {};

function resolve() {
  const next = match();
  if (current?.instance?.destroy) current.instance.destroy();
  outlet.replaceChildren();
  document.body.dataset.view = next.name;
  const ctx = { params: next.params, path: next.path, setTitle };
  setTitle(null);
  let instance = null;
  if (next.view) {
    instance = next.view.mount(outlet, ctx) || null;
  } else {
    outlet.append(Object.assign(document.createElement("div"), { className: "page" }));
    import("./views/notfound.js").then((m) => m.mount(outlet.firstElementChild));
  }
  current = { ...next, instance };
  onChange(next);
  window.scrollTo(0, 0);
  const focusTarget = outlet.querySelector("[data-autofocus]") || outlet;
  focusTarget.setAttribute("tabindex", "-1");
  focusTarget.focus({ preventScroll: true });
}

function setTitle(part) {
  document.title = part ? `${part} · Web Crawler` : "Web Crawler";
}

export function startRouter(outletEl, changeHandler) {
  outlet = outletEl;
  onChange = changeHandler || onChange;
  window.addEventListener("hashchange", resolve);
  resolve();
}
