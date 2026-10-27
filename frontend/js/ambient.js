/**
 * Ambient background: a slowly drifting network. A few "crawlers" walk from node to node,
 * leaving a fading trail and warming the nodes they have visited.
 *
 * Purely decorative: it shows no labels or figures. Cheap by design (about 30 fps, a few
 * dozen nodes, adaptive node count), paused when motion is switched off or the user prefers
 * reduced motion, and the canvas has pointer-events: none so it can never block input.
 */

const STORAGE_KEY = "wc-motion";

function hexToRgb(hex) {
  const v = hex.trim().replace("#", "");
  return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16));
}

function readPalette() {
  const css = getComputedStyle(document.documentElement);
  const get = (name, fallback) => hexToRgb(css.getPropertyValue(name) || fallback);
  return {
    bone: get("--bone", "#e8e3d6"),
    dim: get("--bone-dim", "#b3b0a4"),
    verdigris: get("--verdigris", "#6dbba5"),
    brass: get("--brass", "#d0a85c"),
  };
}

const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));

export function startAmbient(canvas) {
  const ctx = canvas.getContext("2d", { alpha: true });
  const palette = readPalette();
  const reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

  let w = 0, h = 0, dpr = 1;
  let nodes = [];
  let walkers = [];
  let linkDist = 150;
  let targetCount = 40;
  let raf = 0;
  let last = 0;
  let lastDraw = 0;
  let cost = 0, costFrames = 0, shrinks = 0;
  const pointer = { x: -999, y: -999, active: false };

  const stored = localStorage.getItem(STORAGE_KEY);
  let motion = stored ? stored === "on" : !reducedQuery.matches;

  // ---------- setup ----------
  function computeCounts() {
    const mobile = w < 640;
    targetCount = Math.round(Math.min(mobile ? 34 : 84, Math.max(24, (w * h) / 21000)));
    linkDist = Math.max(105, Math.min(170, w / 9));
  }

  function makeNode(x, y) {
    const angle = Math.random() * Math.PI * 2;
    const speed = 3 + Math.random() * 7;
    return { x, y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed, r: 1.2 + Math.random() * 1.2, warm: 0, pulse: -10 };
  }

  function fillNodes() {
    while (nodes.length < targetCount) nodes.push(makeNode(Math.random() * w, Math.random() * h));
    if (nodes.length > targetCount) nodes.length = targetCount;
    const count = w < 640 ? 2 : 3;
    walkers = walkers.filter((k) => k.from < nodes.length && k.to < nodes.length);
    while (walkers.length < count && nodes.length > 3) walkers.push(newWalker());
  }

  function newWalker() {
    const from = Math.floor(Math.random() * nodes.length);
    return { from, to: pickNext({ from, trail: [from] }), t: 0, trail: [from], speed: 70 + Math.random() * 40 };
  }

  function neighbours(i) {
    const a = nodes[i];
    const out = [];
    for (let j = 0; j < nodes.length; j++) {
      if (j === i) continue;
      const dx = nodes[j].x - a.x, dy = nodes[j].y - a.y;
      if (dx * dx + dy * dy < linkDist * linkDist) out.push(j);
    }
    return out;
  }

  function pickNext(walker) {
    const options = neighbours(walker.from).filter((j) => !walker.trail.slice(-5).includes(j));
    if (options.length) return options[Math.floor(Math.random() * options.length)];
    const any = neighbours(walker.from);
    if (any.length) return any[Math.floor(Math.random() * any.length)];
    return Math.floor(Math.random() * nodes.length);
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const nw = Math.max(1, Math.round(rect.width));
    const nh = Math.max(1, Math.round(rect.height));
    dpr = Math.min(window.devicePixelRatio || 1, 1.75);
    const sx = w ? nw / w : 1, sy = h ? nh / h : 1;
    w = nw; h = nh;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    nodes.forEach((n) => { n.x *= sx; n.y *= sy; });
    computeCounts();
    fillNodes();
    if (!motion) drawStatic();
  }

  // ---------- simulation ----------
  function step(dt, now) {
    for (const n of nodes) {
      n.x += n.vx * dt;
      n.y += n.vy * dt;
      if (n.x < -40) n.vx = Math.abs(n.vx);
      if (n.x > w + 40) n.vx = -Math.abs(n.vx);
      if (n.y < -40) n.vy = Math.abs(n.vy);
      if (n.y > h + 40) n.vy = -Math.abs(n.vy);
      n.warm = Math.max(0, n.warm - dt / 28);
      if (pointer.active) {
        const dx = n.x - pointer.x, dy = n.y - pointer.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 120 * 120 && d2 > 1) {
          const d = Math.sqrt(d2);
          const push = (1 - d / 120) * 26 * dt;
          n.x += (dx / d) * push;
          n.y += (dy / d) * push;
        }
      }
    }
    for (const k of walkers) {
      const a = nodes[k.from], b = nodes[k.to];
      if (!a || !b) continue;
      const dist = Math.hypot(b.x - a.x, b.y - a.y) || 1;
      k.t += (k.speed * dt) / dist;
      if (k.t >= 1) {
        b.warm = 1;
        b.pulse = now;
        k.trail.push(k.to);
        if (k.trail.length > 7) k.trail.shift();
        k.from = k.to;
        k.to = pickNext(k);
        k.t = 0;
      }
    }
  }

  // ---------- drawing ----------
  function edgeBias(x) {
    return w > 900 ? 0.5 + 0.5 * (x / w) : 1; // quieter behind the hero copy on wide screens
  }

  function drawNetwork() {
    ctx.clearRect(0, 0, w, h);
    ctx.lineWidth = 1;
    const L2 = linkDist * linkDist;
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y;
        const d2 = dx * dx + dy * dy;
        if (d2 > L2) continue;
        const alpha = (1 - Math.sqrt(d2) / linkDist) * 0.17 * edgeBias((a.x + b.x) / 2);
        ctx.strokeStyle = rgba(palette.bone, alpha);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }
    for (const n of nodes) {
      const colour = mix(palette.dim, palette.verdigris, n.warm);
      ctx.fillStyle = rgba(colour, (0.35 + n.warm * 0.5) * edgeBias(n.x) + 0.1);
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r + n.warm * 1.2, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function drawWalkers(now) {
    for (const n of nodes) {
      const age = (now - n.pulse) / 1000;
      if (age >= 0 && age < 1.4) {
        ctx.strokeStyle = rgba(palette.verdigris, 0.5 * (1 - age / 1.4));
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(n.x, n.y, 3 + age * 9, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
    for (const k of walkers) {
      const a = nodes[k.from], b = nodes[k.to];
      if (!a || !b) continue;
      const x = a.x + (b.x - a.x) * k.t;
      const y = a.y + (b.y - a.y) * k.t;
      ctx.lineWidth = 1.3;
      const pts = [...k.trail.map((i) => nodes[i]).filter(Boolean), a];
      for (let i = 1; i < pts.length; i++) {
        const fade = i / pts.length;
        ctx.strokeStyle = rgba(palette.verdigris, 0.08 + fade * 0.42);
        ctx.beginPath();
        ctx.moveTo(pts[i - 1].x, pts[i - 1].y);
        ctx.lineTo(pts[i].x, pts[i].y);
        ctx.stroke();
      }
      ctx.strokeStyle = rgba(palette.verdigris, 0.75);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(x, y);
      ctx.stroke();
      ctx.fillStyle = rgba(palette.verdigris, 0.18);
      ctx.beginPath(); ctx.arc(x, y, 8, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = rgba(palette.verdigris, 1);
      ctx.beginPath(); ctx.arc(x, y, 2.8, 0, Math.PI * 2); ctx.fill();
    }
  }

  function drawPointer() {
    if (!pointer.active) return;
    const near = nodes
      .map((n) => ({ n, d: Math.hypot(n.x - pointer.x, n.y - pointer.y) }))
      .filter((o) => o.d < 170)
      .sort((p, q) => p.d - q.d)
      .slice(0, 3);
    ctx.lineWidth = 1;
    for (const { n, d } of near) {
      ctx.strokeStyle = rgba(palette.brass, (1 - d / 170) * 0.4);
      ctx.beginPath();
      ctx.moveTo(pointer.x, pointer.y);
      ctx.lineTo(n.x, n.y);
      ctx.stroke();
    }
  }

  function drawStatic() {
    drawNetwork();
  }

  function tick(t) {
    raf = requestAnimationFrame(tick);
    if (t - lastDraw < 32) return; // about 30 fps is plenty for slow drift
    const begin = performance.now();
    const dt = Math.min(0.06, (t - (last || t)) / 1000);
    last = t;
    lastDraw = t;
    step(dt, t);
    drawNetwork();
    drawWalkers(t);
    drawPointer();

    cost += performance.now() - begin;
    if (++costFrames === 90) {
      if (cost / costFrames > 9 && shrinks < 2) { // slow device: thin the network out
        shrinks++;
        targetCount = Math.max(20, Math.round(targetCount * 0.7));
        fillNodes();
      }
      cost = 0; costFrames = 0;
    }
  }

  function play() {
    if (raf) return;
    last = 0;
    raf = requestAnimationFrame(tick);
  }

  function pause() {
    cancelAnimationFrame(raf);
    raf = 0;
  }

  function apply() {
    if (motion) play();
    else { pause(); drawStatic(); }
  }

  // ---------- wiring ----------
  const onPointer = (e) => { pointer.x = e.clientX; pointer.y = e.clientY - canvas.getBoundingClientRect().top; pointer.active = true; };
  const onLeave = () => { pointer.active = false; };
  let resizeTimer;
  const onResize = () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(resize, 120); };
  const onVisibility = () => { last = 0; };
  const onReduced = () => {
    if (localStorage.getItem(STORAGE_KEY)) return; // an explicit user choice wins
    motion = !reducedQuery.matches;
    apply();
  };

  window.addEventListener("pointermove", onPointer, { passive: true });
  document.addEventListener("pointerleave", onLeave);
  window.addEventListener("blur", onLeave);
  window.addEventListener("resize", onResize);
  document.addEventListener("visibilitychange", onVisibility);
  reducedQuery.addEventListener?.("change", onReduced);

  resize();
  apply();

  return {
    get motion() { return motion; },
    setMotion(on) {
      motion = on;
      try { localStorage.setItem(STORAGE_KEY, on ? "on" : "off"); } catch { /* storage unavailable */ }
      apply();
    },
    destroy() {
      pause();
      window.removeEventListener("pointermove", onPointer);
      document.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("blur", onLeave);
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibility);
      reducedQuery.removeEventListener?.("change", onReduced);
    },
  };
}
