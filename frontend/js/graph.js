/**
 * Link graph of the pages found by a crawl.
 *
 * Nodes are crawled pages (colour = response class), edges are links between two crawled
 * pages. The layout is force-directed with a weak pull towards a ring per crawl depth, so the
 * start page sits in the middle and each further click moves one ring outwards.
 *
 * Interaction: drag nodes, drag the background to pan, wheel or +/- to zoom, click to select.
 * Keyboard: arrow keys move between pages, Enter selects, 0 fits, +/- zoom.
 */
import { h } from "./dom.js";

const RING = 96;
const CELL = 70;

function hexToRgb(hex) {
  const v = hex.trim().replace("#", "");
  return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16));
}
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

export function createGraph(container, { onSelect = () => {}, onFocus = () => {} } = {}) {
  const css = getComputedStyle(document.documentElement);
  const colour = (name, fallback) => hexToRgb(css.getPropertyValue(name) || fallback);
  const palette = {
    bone: colour("--bone", "#e8e3d6"),
    dim: colour("--bone-dim", "#b3b0a4"),
    ok: colour("--verdigris", "#6dbba5"),
    redirect: colour("--steel", "#8da6c2"),
    error: colour("--rose", "#e58a7b"),
    brass: colour("--brass", "#d0a85c"),
    ink: colour("--ink", "#14181a"),
  };
  const reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

  const canvas = h("canvas", {
    class: "graph-canvas", tabindex: "0", role: "application", "aria-roledescription": "link graph",
    "aria-label": "Link graph of crawled pages. Use the arrow keys to move between pages and Enter to inspect one.",
  });
  const tip = h("div", { class: "graph-tip", hidden: true, role: "presentation" });
  container.append(canvas, tip);
  const ctx = canvas.getContext("2d");

  let W = 0, H = 0, dpr = 1;
  let list = [];
  let byId = new Map();
  let edges = [];
  let adjacency = new Map();
  let alpha = 0;
  let raf = 0;
  let dirty = true;
  let autoFit = true;
  let view = { k: 1, tx: 0, ty: 0 };
  let hover = null, selected = null, focusIdx = -1;
  let drag = null, pan = null;

  // ---------- data ----------
  function radius(n) { return Math.min(9, 3.2 + Math.sqrt(n.outlinks || 0) * 0.55); }
  function tone(n) {
    if (n.status >= 200 && n.status < 300) return palette.ok;
    if (n.status >= 300 && n.status < 400) return palette.redirect;
    return palette.error;
  }

  function setData(data) {
    const previous = byId;
    const next = new Map();
    const incoming = new Map();
    for (const e of data.edges) if (!incoming.has(e.target)) incoming.set(e.target, e.source);

    list = data.nodes.map((raw, idx) => {
      let node = previous.get(raw.id);
      if (!node) {
        const parent = previous.get(incoming.get(raw.id)) || next.get(incoming.get(raw.id));
        const a = Math.random() * Math.PI * 2;
        node = {
          x: (parent ? parent.x : 0) + Math.cos(a) * 24, y: (parent ? parent.y : 0) + Math.sin(a) * 24,
          vx: 0, vy: 0,
        };
      }
      Object.assign(node, { id: raw.id, pageId: raw.page_id, label: raw.label, status: raw.status, depth: raw.depth, outlinks: raw.outlinks, idx });
      next.set(raw.id, node);
      return node;
    });
    byId = next;
    edges = [];
    adjacency = new Map();
    for (const e of data.edges) {
      const a = next.get(e.source), b = next.get(e.target);
      if (!a || !b) continue;
      edges.push([a, b]);
      if (!adjacency.has(a.id)) adjacency.set(a.id, new Set());
      if (!adjacency.has(b.id)) adjacency.set(b.id, new Set());
      adjacency.get(a.id).add(b.id);
      adjacency.get(b.id).add(a.id);
    }
    if (hover && !byId.has(hover.id)) hover = null;
    canvas.setAttribute("aria-label",
      `Link graph of ${list.length} crawled pages and ${edges.length} links. Use the arrow keys to move between pages and Enter to inspect one.`);

    if (reducedQuery.matches) {
      for (let i = 0; i < 260; i++) simulate(0.6);
      if (autoFit) fit(true);
      alpha = 0;
    } else {
      alpha = Math.max(alpha, list.length < 3 ? 0.4 : 0.7);
    }
    requestDraw();
  }

  // ---------- simulation ----------
  function simulate(strength) {
    const n = list.length;
    if (!n) return;
    const grid = new Map();
    for (const node of list) {
      const key = `${Math.floor(node.x / CELL)},${Math.floor(node.y / CELL)}`;
      (grid.get(key) || grid.set(key, []).get(key)).push(node);
    }
    for (const a of list) {
      const cx = Math.floor(a.x / CELL), cy = Math.floor(a.y / CELL);
      for (let gx = cx - 1; gx <= cx + 1; gx++) {
        for (let gy = cy - 1; gy <= cy + 1; gy++) {
          const bucket = grid.get(`${gx},${gy}`);
          if (!bucket) continue;
          for (const b of bucket) {
            if (a === b) continue;
            let dx = a.x - b.x, dy = a.y - b.y;
            let d2 = dx * dx + dy * dy;
            if (d2 > CELL * CELL) continue;
            if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.5; }
            const d = Math.sqrt(d2);
            const f = ((CELL - d) / CELL) * 2.2 * strength;
            a.vx += (dx / d) * f;
            a.vy += (dy / d) * f;
          }
        }
      }
    }
    for (const [a, b] of edges) {
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.hypot(dx, dy) || 1;
      const f = (d - 52) * 0.012 * strength;
      a.vx += (dx / d) * f; a.vy += (dy / d) * f;
      b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    }
    for (const node of list) {
      const r = Math.hypot(node.x, node.y) || 1;
      const f = (node.depth * RING - r) * 0.014 * strength;
      node.vx += (node.x / r) * f;
      node.vy += (node.y / r) * f;
    }
    for (const node of list) {
      if (node === drag?.node) continue;
      if (node.idx === 0 && node.depth === 0) { node.x = 0; node.y = 0; node.vx = 0; node.vy = 0; continue; }
      node.vx *= 0.8; node.vy *= 0.8;
      node.x += node.vx; node.y += node.vy;
    }
  }

  // ---------- view ----------
  function bounds() {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of list) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    return list.length ? { x0, y0, x1, y1 } : { x0: -1, y0: -1, x1: 1, y1: 1 };
  }

  function fitTarget() {
    const b = bounds();
    const pad = 44;
    const bw = Math.max(b.x1 - b.x0, 80), bh = Math.max(b.y1 - b.y0, 80);
    const k = Math.max(0.12, Math.min(2.2, Math.min((W - pad * 2) / bw, (H - pad * 2) / bh)));
    return { k, tx: W / 2 - ((b.x0 + b.x1) / 2) * k, ty: H / 2 - ((b.y0 + b.y1) / 2) * k };
  }

  function fit(immediate = false) {
    autoFit = true;
    const t = fitTarget();
    if (immediate || reducedQuery.matches) view = t;
    else { view = { k: view.k + (t.k - view.k) * 0.25, tx: view.tx + (t.tx - view.tx) * 0.25, ty: view.ty + (t.ty - view.ty) * 0.25 }; alpha = Math.max(alpha, 0.05); }
    requestDraw();
  }

  function zoomAt(factor, sx = W / 2, sy = H / 2) {
    autoFit = false;
    const k = Math.max(0.1, Math.min(4, view.k * factor));
    const ratio = k / view.k;
    view = { k, tx: sx - (sx - view.tx) * ratio, ty: sy - (sy - view.ty) * ratio };
    requestDraw();
  }

  const toWorld = (sx, sy) => ({ x: (sx - view.tx) / view.k, y: (sy - view.ty) / view.k });

  function hit(sx, sy) {
    const p = toWorld(sx, sy);
    let best = null, bestD = Infinity;
    for (const n of list) {
      const d = Math.hypot(n.x - p.x, n.y - p.y);
      const limit = (radius(n) + 5) / view.k;
      if (d < limit && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }

  // ---------- drawing ----------
  function requestDraw() {
    dirty = true;
    if (!raf) raf = requestAnimationFrame(frame);
  }

  function frame() {
    raf = 0;
    if (alpha > 0.012) {
      simulate(alpha);
      alpha *= 0.985;
      if (autoFit) fit();
      dirty = true;
    }
    if (dirty) { draw(); dirty = false; }
    // fit() and requestDraw() may already have queued the next frame; never queue a second one
    if (alpha > 0.012 && !raf) raf = requestAnimationFrame(frame);
  }

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(view.tx, view.ty);
    ctx.scale(view.k, view.k);

    const focusNode = selected || hover;
    const related = focusNode ? adjacency.get(focusNode.id) || new Set() : null;

    // depth rings (quiet guides)
    const maxDepth = list.reduce((m, n) => Math.max(m, n.depth), 0);
    ctx.lineWidth = 1 / view.k;
    ctx.strokeStyle = rgba(palette.bone, 0.045);
    for (let d = 1; d <= maxDepth; d++) { ctx.beginPath(); ctx.arc(0, 0, d * RING, 0, Math.PI * 2); ctx.stroke(); }

    // edges
    ctx.strokeStyle = rgba(palette.bone, focusNode ? 0.07 : 0.15);
    ctx.beginPath();
    for (const [a, b] of edges) { ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); }
    ctx.stroke();
    if (focusNode) {
      ctx.strokeStyle = rgba(palette.brass, 0.85);
      ctx.lineWidth = 1.4 / view.k;
      ctx.beginPath();
      for (const [a, b] of edges) if (a === focusNode || b === focusNode) { ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); }
      ctx.stroke();
    }

    // nodes
    for (const n of list) {
      const dimmed = focusNode && n !== focusNode && !related.has(n.id);
      const r = radius(n);
      ctx.fillStyle = rgba(tone(n), dimmed ? 0.3 : 0.95);
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill();
      if (n.idx === 0 && n.depth === 0) {
        ctx.strokeStyle = rgba(palette.brass, 1);
        ctx.lineWidth = 1.6 / view.k;
        ctx.beginPath(); ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2); ctx.stroke();
      }
    }
    for (const n of [selected, hover].filter(Boolean)) {
      ctx.strokeStyle = rgba(palette.bone, 1);
      ctx.lineWidth = 1.6 / view.k;
      ctx.beginPath(); ctx.arc(n.x, n.y, radius(n) + 3.5, 0, Math.PI * 2); ctx.stroke();
    }
    if (focusIdx >= 0 && list[focusIdx] && document.activeElement === canvas) {
      const n = list[focusIdx];
      ctx.setLineDash([3 / view.k, 3 / view.k]);
      ctx.strokeStyle = rgba(palette.brass, 1);
      ctx.lineWidth = 1.6 / view.k;
      ctx.beginPath(); ctx.arc(n.x, n.y, radius(n) + 7, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.restore();
  }

  // ---------- pointer ----------
  function local(e) {
    const rect = canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function showTip(node, p) {
    if (!node) { tip.hidden = true; return; }
    tip.replaceChildren(
      h("strong", null, node.label.length > 80 ? `${node.label.slice(0, 80)}…` : node.label),
      h("span", null, node.id.length > 90 ? `${node.id.slice(0, 90)}…` : node.id),
      h("span", null, `${node.status || "failed"} · depth ${node.depth} · ${node.outlinks} links`));
    tip.hidden = false;
    const x = Math.min(p.x + 14, Math.max(8, W - tip.offsetWidth - 8));
    const y = Math.min(p.y + 14, Math.max(8, H - tip.offsetHeight - 8));
    tip.style.transform = `translate(${x}px, ${y}px)`;
  }

  canvas.addEventListener("pointerdown", (e) => {
    canvas.focus({ preventScroll: true });
    const p = local(e);
    const node = hit(p.x, p.y);
    canvas.setPointerCapture(e.pointerId);
    if (node) drag = { node, moved: false, sx: p.x, sy: p.y };
    else pan = { moved: false, sx: p.x, sy: p.y, tx: view.tx, ty: view.ty };
  });

  canvas.addEventListener("pointermove", (e) => {
    const p = local(e);
    if (drag) {
      if (Math.hypot(p.x - drag.sx, p.y - drag.sy) > 3) drag.moved = true;
      if (drag.moved) {
        const w = toWorld(p.x, p.y);
        drag.node.x = w.x; drag.node.y = w.y; drag.node.vx = 0; drag.node.vy = 0;
        alpha = Math.max(alpha, reducedQuery.matches ? 0 : 0.25);
        autoFit = false;
        requestDraw();
      }
      return;
    }
    if (pan) {
      if (Math.hypot(p.x - pan.sx, p.y - pan.sy) > 3) pan.moved = true;
      if (pan.moved) { autoFit = false; view = { ...view, tx: pan.tx + (p.x - pan.sx), ty: pan.ty + (p.y - pan.sy) }; requestDraw(); }
      return;
    }
    const node = hit(p.x, p.y);
    if (node !== hover) {
      hover = node;
      canvas.style.cursor = node ? "pointer" : "grab";
      requestDraw();
    }
    showTip(node, p);
  });

  function release(e) {
    if (drag && !drag.moved) { selected = drag.node; focusIdx = drag.node.idx; onSelect(drag.node); requestDraw(); }
    else if (pan && !pan.moved) { if (selected) { selected = null; onSelect(null); requestDraw(); } }
    drag = null; pan = null;
    if (e.pointerId !== undefined && canvas.hasPointerCapture?.(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
  }
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  canvas.addEventListener("pointerleave", () => { if (!drag && !pan) { hover = null; tip.hidden = true; requestDraw(); } });

  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const p = local(e);
    zoomAt(Math.exp(-e.deltaY * 0.0014), p.x, p.y);
  }, { passive: false });

  canvas.addEventListener("keydown", (e) => {
    if (!list.length) return;
    const move = (to) => {
      focusIdx = Math.max(0, Math.min(list.length - 1, to));
      onFocus(list[focusIdx]);
      requestDraw();
    };
    switch (e.key) {
      case "ArrowRight": case "ArrowDown": e.preventDefault(); move(focusIdx < 0 ? 0 : focusIdx + 1); break;
      case "ArrowLeft": case "ArrowUp": e.preventDefault(); move(focusIdx < 0 ? 0 : focusIdx - 1); break;
      case "Home": e.preventDefault(); move(0); break;
      case "End": e.preventDefault(); move(list.length - 1); break;
      case "Enter": case " ":
        if (focusIdx >= 0) { e.preventDefault(); selected = list[focusIdx]; onSelect(selected); requestDraw(); }
        break;
      case "+": case "=": zoomAt(1.25); break;
      case "-": case "_": zoomAt(0.8); break;
      case "0": fit(true); break;
      case "Escape": if (selected) { selected = null; onSelect(null); requestDraw(); } break;
      default: break;
    }
  });
  canvas.addEventListener("focus", requestDraw);
  canvas.addEventListener("blur", requestDraw);

  // ---------- sizing ----------
  function resize() {
    const rect = container.getBoundingClientRect();
    W = Math.max(1, Math.round(rect.width));
    H = Math.max(1, Math.round(rect.height));
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
    if (autoFit) view = fitTarget();
    requestDraw();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  view = { k: 1, tx: container.clientWidth / 2, ty: container.clientHeight / 2 };
  resize();
  canvas.style.cursor = "grab";

  return {
    setData,
    fit: () => fit(true),
    zoomBy: (f) => zoomAt(f),
    select(id) { selected = id ? byId.get(id) || null : null; if (selected) focusIdx = selected.idx; requestDraw(); },
    get size() { return list.length; },
    destroy() { cancelAnimationFrame(raf); observer.disconnect(); canvas.remove(); tip.remove(); },
  };
}
