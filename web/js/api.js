// Thin fetch wrapper. All calls are same-origin and rely on the session cookie.

async function req(method, path, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* no body */ }
  if (!res.ok) {
    const detail = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return data;
}

export const api = {
  get: (p) => req("GET", p),
  post: (p, b) => req("POST", p, b),

  authStatus: () => api.get("/api/auth/status"),
  setup: (b) => api.post("/api/auth/setup", b),
  login: (b) => api.post("/api/auth/login", b),
  logout: () => api.post("/api/auth/logout"),
  me: () => api.get("/api/auth/me"),
  changePassword: (b) => api.post("/api/auth/password", b),

  tgStatus: () => api.get("/api/telegram/status"),
  tgCredentials: (b) => api.post("/api/telegram/credentials", b),
  tgLoginStart: () => api.post("/api/telegram/login/start"),
  tgLoginStatus: () => api.get("/api/telegram/login/status"),
  tgLoginPassword: (b) => api.post("/api/telegram/login/password", b),
  tgLogout: () => api.post("/api/telegram/logout"),

  tasks: () => api.get("/api/downloads/tasks"),
  dlLink: (b) => api.post("/api/downloads/link", b),
  dlChannel: (b) => api.post("/api/downloads/channel", b),
  cancel: () => api.post("/api/downloads/cancel"),
  resume: () => api.post("/api/downloads/resume"),
  retryFailed: () => api.post("/api/downloads/retry-failed"),
  clearHistory: () => api.post("/api/downloads/clear-history"),
  concurrency: (v) => api.post("/api/downloads/concurrency", { value: v }),

  files: (path) => api.get("/api/files" + (path ? "?path=" + encodeURIComponent(path) : "")),
  fileUrl: (path, inline) => "/api/files/download?path=" + encodeURIComponent(path) + (inline ? "&inline=1" : ""),
  mkdir: (b) => api.post("/api/files/mkdir", b),
  renameFile: (b) => api.post("/api/files/rename", b),
  deleteFile: (b) => api.post("/api/files/delete", b),
  setCurrent: (b) => api.post("/api/files/set-current", b),
  setRoot: (b) => api.post("/api/files/set-root", b),

  setLanguage: (lang) => api.post("/api/settings/language", { lang }),

  settings: () => api.get("/api/settings"),
  setProxy: (b) => api.post("/api/settings/proxy", b),
  setSubscription: (b) => api.post("/api/settings/subscription", b),
  proxyMode: (mode) => api.post("/api/settings/proxy/mode", { mode }),
  proxyNodes: () => api.get("/api/settings/proxy/nodes"),
  proxySelect: (b) => api.post("/api/settings/proxy/select", b),
  proxyTest: (b) => api.post("/api/settings/proxy/test", b),
  proxyRefresh: () => api.post("/api/settings/proxy/refresh"),
  setBot: (b) => api.post("/api/settings/bot", b),
};
