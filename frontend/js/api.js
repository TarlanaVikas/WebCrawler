export class ApiError extends Error {
  constructor(message, { status = 0, code = "error", fields = {}, data = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
    this.data = data;
  }
}

function describeDetail(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || String(d)).join("; ");
  return "";
}

async function request(method, path, { body, params, signal } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  let response;
  try {
    response = await fetch(url, {
      method,
      signal,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Cannot reach the Web Crawler server. Check that it is still running.", { code: "network" });
  }
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* non-JSON body */ }
  if (!response.ok) {
    throw new ApiError(describeDetail(data?.detail) || `The server answered with ${response.status}.`, {
      status: response.status,
      code: data?.code || "error",
      fields: data?.fields || {},
      data,
    });
  }
  return data;
}

export const api = {
  health: () => request("GET", "/api/health"),
  limits: () => request("GET", "/api/limits"),
  status: (signal) => request("GET", "/api/crawl/status", { signal }),
  start: (config) => request("POST", "/api/crawl/start", { body: config }),
  stop: () => request("POST", "/api/crawl/stop"),
  sessions: (params, signal) => request("GET", "/api/sessions", { params, signal }),
  session: (id, signal) => request("GET", `/api/sessions/${id}`, { signal }),
  deleteSession: (id) => request("DELETE", `/api/sessions/${id}`),
  results: (params, signal) => request("GET", "/api/results", { params, signal }),
  page: (id, sessionId, signal) => request("GET", `/api/pages/${id}`, { params: { session_id: sessionId }, signal }),
  graph: (sessionId, signal) => request("GET", "/api/graph", { params: { session_id: sessionId }, signal }),
  exportUrl: (sessionId, format) => `/api/export?session_id=${encodeURIComponent(sessionId)}&format=${format}`,
  logStreamUrl: (sessionId) => `/api/logs/stream?session_id=${encodeURIComponent(sessionId)}`,
};
