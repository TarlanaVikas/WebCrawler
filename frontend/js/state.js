import { api } from "./api.js";
import { createStore } from "./store.js";

/** Global application state: server reachability, the active crawl and server limits. */
export const app = createStore({
  server: "unknown", // unknown | up | down
  status: { status: "idle", is_running: false },
  limits: null,
  health: null,
});

let timer = null;
let inFlight = false;

export async function refreshStatus() {
  if (inFlight) return;
  inFlight = true;
  try {
    const status = await api.status();
    app.set({ server: "up", status });
  } catch {
    app.set({ server: "down" });
  } finally {
    inFlight = false;
  }
}

function schedule() {
  clearTimeout(timer);
  const { status, server } = app.get();
  const delay = document.hidden ? 8000 : status.is_running ? 1000 : server === "down" ? 3000 : 5000;
  timer = setTimeout(async () => {
    await refreshStatus();
    schedule();
  }, delay);
}

export async function startStatusPolling() {
  await refreshStatus();
  schedule();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { refreshStatus(); schedule(); }
  });
}

/** Call after starting/stopping a crawl so the header reacts immediately. */
export async function pokeStatus() {
  await refreshStatus();
  schedule();
}

export async function loadLimits() {
  if (app.get().limits) return app.get().limits;
  const [limits, health] = await Promise.all([api.limits(), api.health()]);
  app.set({ limits, health, server: "up" });
  return limits;
}
