// TelegramMediaManager — single-page panel controller.
// Absolute paths so the index.html import map can pin these to the versioned
// URLs (e.g. /js/api.js?v=2.1.1) for cache-busting.
import { api } from "/js/api.js";
import { $, esc, human, toast, initTheme, toggleTheme } from "/js/ui.js";
import { t, getLang, setLang, LANGS } from "/js/i18n.js";

const app = $("#app");
const state = { view: "dashboard", tg: null, ws: null, live: {}, viewCtl: null };

// ===================================================================== //
// Language
// ===================================================================== //
function langSwitcher() {
  return `<div class="lang-switch">${LANGS.map((l) =>
    `<button data-lang="${l.id}" class="${l.id === getLang() ? "on" : ""}">${l.name}</button>`).join("")}</div>`;
}

function wireLang(onChange) {
  document.querySelectorAll(".lang-switch [data-lang]").forEach((b) =>
    b.addEventListener("click", () => {
      if (b.dataset.lang === getLang()) return;
      setLang(b.dataset.lang);
      api.setLanguage(b.dataset.lang).catch(() => {});  // sync backend messages
      onChange();
    }));
}

// ===================================================================== //
// Boot
// ===================================================================== //
async function boot() {
  initTheme();
  try {
    const st = await api.authStatus();
    if (st.setup_needed) return renderSetup();
    if (!st.authenticated) return renderLogin();
    return renderApp();
  } catch (e) {
    app.innerHTML = `<div class="center-wrap"><div class="card"><h1>${t("err_connect_title")}</h1>
      <p class="sub">${esc(e.message)}</p><button class="btn" onclick="location.reload()">${t("retry")}</button></div></div>`;
  }
}

// ===================================================================== //
// Setup wizard (first run)
// ===================================================================== //
function renderSetup() {
  app.innerHTML = `
  <div class="center-wrap"><div class="card">
    ${langSwitcher()}
    <div class="brand-logo">⬇</div>
    <h1>${t("setup_title")}</h1>
    <p class="sub">${t("setup_sub")}</p>
    <form id="setup-form">
      <div class="field"><label>${t("admin_username")}</label>
        <input class="input" name="username" autocomplete="username" minlength="3" required></div>
      <div class="field"><label>${t("password_min")}</label>
        <input class="input" name="password" type="password" autocomplete="new-password" minlength="8" required></div>
      <div class="field"><label>${t("confirm_password")}</label>
        <input class="input" name="confirm" type="password" minlength="8" required></div>
      <p class="error-text hidden" id="setup-err"></p>
      <button class="btn" type="submit">${t("create_enter")}</button>
      <p class="hint" style="margin-top:14px">${t("setup_warn")}</p>
    </form>
  </div></div>`;
  wireLang(renderSetup);
  $("#setup-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, err = $("#setup-err");
    if (f.password.value !== f.confirm.value) { err.textContent = t("pw_mismatch"); err.classList.remove("hidden"); return; }
    try {
      await api.setup({ username: f.username.value.trim(), password: f.password.value, lang: getLang() });
      toast(t("setup_done"), "ok");
      renderApp();
    } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
  });
}

// ===================================================================== //
// Login
// ===================================================================== //
function renderLogin() {
  app.innerHTML = `
  <div class="center-wrap"><div class="card">
    ${langSwitcher()}
    <div class="brand-logo">⬇</div>
    <h1>${t("login_title")}</h1>
    <p class="sub">${t("login_sub")}</p>
    <form id="login-form">
      <div class="field"><label>${t("username")}</label>
        <input class="input" name="username" autocomplete="username" required></div>
      <div class="field"><label>${t("password")}</label>
        <input class="input" name="password" type="password" autocomplete="current-password" required></div>
      <p class="error-text hidden" id="login-err"></p>
      <button class="btn" type="submit">${t("sign_in")}</button>
    </form>
  </div></div>`;
  wireLang(renderLogin);
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, err = $("#login-err");
    try {
      await api.login({ username: f.username.value.trim(), password: f.password.value });
      await api.setLanguage(getLang()).catch(() => {});
      renderApp();
    } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
  });
}

// ===================================================================== //
// App shell
// ===================================================================== //
function navItems() {
  return [
    { id: "dashboard", ico: "📥", label: t("nav_dashboard") },
    { id: "files", ico: "🗂", label: t("nav_files") },
    { id: "proxy", ico: "🌐", label: t("nav_proxy") },
    { id: "settings", ico: "⚙️", label: t("nav_settings") },
  ];
}

function renderApp() {
  const NAV = navItems();
  app.innerHTML = `
  <div class="app-shell">
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-head">
        <div class="logo">⬇</div>
        <div class="title">Media Manager<small>${t("app_subtitle")}</small></div>
      </div>
      <nav class="nav" id="nav">
        ${NAV.map((n) => `<button class="nav-item" data-view="${n.id}">
          <span class="ico">${n.ico}</span>${n.label}
          ${n.id === "dashboard" ? '<span class="badge hidden" id="fail-badge"></span>' : ""}
        </button>`).join("")}
      </nav>
      <div class="sidebar-foot">
        <button class="nav-item" id="theme-btn"><span class="ico">🌓</span>${t("toggle_theme")}</button>
        <button class="nav-item" id="logout-btn"><span class="ico">🚪</span>${t("logout")}</button>
      </div>
    </aside>
    <div class="scrim hidden" id="scrim"></div>
    <main class="main">
      <header class="topbar">
        <button class="menu-toggle" id="menu-toggle">☰</button>
        <span class="page-title" id="page-title"></span>
        <span class="spacer"></span>
        <span class="chip hidden" id="tg-chip"></span>
      </header>
      <div class="content"><div class="content-inner" id="view"></div></div>
    </main>
  </div>`;

  $("#nav").addEventListener("click", (e) => {
    const btn = e.target.closest(".nav-item");
    if (btn) showView(btn.dataset.view);
  });
  $("#theme-btn").addEventListener("click", toggleTheme);
  $("#logout-btn").addEventListener("click", async () => { await api.logout(); location.reload(); });
  $("#menu-toggle").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#scrim").addEventListener("click", () => $("#sidebar").classList.remove("open"));

  connectWs();
  showView(state.view in VIEWS ? state.view : "dashboard");
  refreshTgChip();
}

function setActiveNav(view) {
  document.querySelectorAll(".nav-item[data-view]").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  const nav = navItems().find((n) => n.id === view);
  $("#page-title").textContent = nav ? nav.label : "";
  $("#sidebar").classList.remove("open");
}

async function showView(view) {
  if (state.viewCtl && state.viewCtl.leave) state.viewCtl.leave();
  state.view = view;
  setActiveNav(view);
  const host = $("#view");
  host.innerHTML = `<div class="empty"><span class="spin"></span></div>`;
  state.viewCtl = await VIEWS[view](host);
}

// ===================================================================== //
// WebSocket live feed
// ===================================================================== //
function connectWs() {
  if (state.ws) return;  // avoid duplicate sockets across re-renders
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/ws`);
  state.ws = ws;
  ws.onmessage = (ev) => {
    const evt = JSON.parse(ev.data);
    if (evt.type === "ping") return;
    if (evt.type === "job_progress" && evt.job_id != null) state.live[evt.job_id] = evt;
    if (state.viewCtl && state.viewCtl.onEvent) state.viewCtl.onEvent(evt);
  };
  ws.onclose = () => { state.ws = null; setTimeout(connectWs, 3000); };
}

async function refreshTgChip() {
  try {
    const s = await api.tgStatus();
    state.tg = s;
    const chip = $("#tg-chip");
    if (!chip) return;
    chip.textContent = s.authorized && s.me ? "👤 " + s.me.name : t("tg_not_connected");
    chip.classList.remove("hidden");
  } catch (_) {}
}

// ===================================================================== //
// Views
// ===================================================================== //
const VIEWS = {
  dashboard: mountDashboard,
  files: mountFiles,
  proxy: mountProxy,
  settings: mountSettings,
};

// ---------- Dashboard ---------- //
async function mountDashboard(host) {
  const tg = await api.tgStatus().catch(() => null);
  state.tg = tg;
  const loggedIn = tg && tg.authorized;

  host.innerHTML = `
    <div id="tg-connect"></div>
    <div class="panel">
      <h2>${t("new_download")}</h2>
      <p class="panel-note">${t("composer_note")}</p>
      <div class="composer">
        <input class="input" id="link-input" placeholder="https://t.me/xxx/123">
        <button class="btn small" id="btn-link">${t("download")}</button>
        <button class="btn small secondary" id="btn-channel">${t("whole_channel")}</button>
      </div>
    </div>
    <div class="panel">
      <h2>${t("overview")} <span class="spacer"></span>
        <button class="btn tiny secondary" id="btn-resume">${t("resume")}</button>
        <button class="btn tiny secondary" id="btn-retry">${t("retry_failed")}</button>
        <button class="btn tiny danger" id="btn-cancel">${t("cancel_queue")}</button>
      </h2>
      <div class="stat-row" id="stats"></div>
    </div>
    <div class="panel">
      <h2>${t("tasks_h")} <span class="spacer"></span>
        <button class="btn tiny ghost" id="btn-clear">${t("clear_history")}</button></h2>
      <div id="task-list"><div class="empty"><span class="spin"></span></div></div>
    </div>`;

  if (!loggedIn) renderTgConnect($("#tg-connect"));

  $("#btn-link").addEventListener("click", submitLink);
  $("#btn-channel").addEventListener("click", submitChannel);
  $("#link-input").addEventListener("keydown", (e) => { if (e.key === "Enter") submitLink(); });
  $("#btn-resume").addEventListener("click", async () => {
    const r = await api.resume(); toast(t("requeued_n", { n: r.queued }), "ok"); loadTasks();
  });
  $("#btn-retry").addEventListener("click", async () => {
    const r = await api.retryFailed(); toast(t("requeued_failed_n", { n: r.requeued }), "ok"); loadTasks();
  });
  $("#btn-cancel").addEventListener("click", async () => {
    const r = await api.cancel(); toast(t("cleared_queue_n", { n: r.drained })); loadTasks();
  });
  $("#btn-clear").addEventListener("click", async () => {
    const r = await api.clearHistory(); toast(t("cleared_history_n", { n: r.cleared })); loadTasks();
  });

  async function submitLink() {
    const inp = $("#link-input"), link = inp.value.trim();
    if (!link) return;
    inp.disabled = true;
    try {
      const r = await api.dlLink({ link });
      if (r.ok) { toast(t("added_media_n", { n: r.count }), "ok"); inp.value = ""; }
      else toast(r.error, "err");
    } catch (e) { toast(e.message, "err"); }
    inp.disabled = false; loadTasks();
  }
  async function submitChannel() {
    const inp = $("#link-input"), link = inp.value.trim();
    if (!link) return;
    try {
      const r = await api.dlChannel({ link });
      if (r.ok) { toast(t("channel_started", { title: r.title }), "ok"); inp.value = ""; }
      else toast(r.error, "err");
    } catch (e) { toast(e.message, "err"); }
    loadTasks();
  }

  let timer = null;
  async function loadTasks() {
    let d;
    try { d = await api.tasks(); } catch (_) { return; }
    renderStats($("#stats"), d);
    renderTasks($("#task-list"), d);
    const badge = $("#fail-badge");
    const failed = (d.failed || []).length;
    if (badge) { badge.textContent = failed; badge.classList.toggle("hidden", !failed); }
  }
  loadTasks();
  timer = setInterval(loadTasks, 3000);

  return {
    leave() { clearInterval(timer); },
    onEvent(evt) {
      if (evt.type && (evt.type.startsWith("job_") || evt.type.startsWith("channel_")
        || evt.type === "resumed" || evt.type === "cancelled")) loadTasks();
    },
  };
}

function renderTgConnect(host) {
  host.innerHTML = `
    <div class="panel">
      <h2>${t("tg_connect_title")}</h2>
      <p class="panel-note">${t("tg_connect_note")}</p>
      <div id="tg-flow"></div>
    </div>`;
  const flow = $("#tg-flow");
  if (!state.tg || !state.tg.credentials_ready) {
    flow.innerHTML = `
      <div class="field"><label>${t("api_id")}</label><input class="input" id="api-id" placeholder="${t("api_id_ph")}"></div>
      <div class="field"><label>${t("api_hash")}</label><input class="input" id="api-hash"></div>
      <button class="btn" id="save-creds">${t("save_continue")}</button>
      <p class="hint">${t("api_hint")}</p>`;
    $("#save-creds").addEventListener("click", async () => {
      const api_id = parseInt($("#api-id").value.trim(), 10);
      const api_hash = $("#api-hash").value.trim();
      if (!api_id || !api_hash) return toast(t("fill_api"), "err");
      try {
        await api.tgCredentials({ api_id, api_hash });
        state.tg = await api.tgStatus();
        startQrFlow(flow);
      } catch (e) { toast(e.message, "err"); }
    });
  } else {
    startQrFlow(flow);
  }
}

async function startQrFlow(flow) {
  flow.innerHTML = `<div class="empty"><span class="spin"></span> ${t("generating_qr")}</div>`;
  try { await api.tgLoginStart(); } catch (e) { flow.innerHTML = `<p class="error-text">${esc(e.message)}</p>`; return; }
  let poll = null, phase = null, lastUrl = null;
  const draw = (st) => {
    if (st.state === "waiting") {
      // Render the QR frame once; only swap the image when the token changes,
      // so the code doesn't flicker/reload on every poll.
      if (phase !== "waiting") {
        phase = "waiting"; lastUrl = null;
        flow.innerHTML = `<div class="qr-wrap"><img id="qr-img" alt="QR">
          <div class="qr-steps">${t("qr_steps")}</div></div>`;
      }
      if (st.url && st.url !== lastUrl) {
        lastUrl = st.url;
        const img = document.querySelector("#qr-img");
        if (img) img.src = "/api/telegram/login/qr.png?t=" + Date.now();
      }
    } else if (st.state === "need_password") {
      // Render the field ONCE — re-rendering on each poll is what stole focus and
      // submitted an empty password. Subsequent polls only touch the error line.
      if (phase !== "need_password") {
        phase = "need_password";
        flow.innerHTML = `<div class="field"><label>${t("tfa_label")}</label>
          <input class="input" id="tfa" type="password" autocomplete="current-password"></div>
          <p class="error-text hidden" id="tfa-err"></p>
          <button class="btn" id="tfa-btn">${t("submit")}</button>`;
        const submit = async () => {
          const btn = $("#tfa-btn"), err = $("#tfa-err");
          if (!$("#tfa").value) return;  // don't submit an empty password
          err.classList.add("hidden");
          btn.disabled = true; btn.textContent = t("checking");
          try { await api.tgLoginPassword({ password: $("#tfa").value }); }
          catch (e) { toast(e.message, "err"); btn.disabled = false; btn.textContent = t("submit"); }
        };
        $("#tfa-btn").addEventListener("click", submit);
        $("#tfa").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
        setTimeout(() => { const i = $("#tfa"); if (i) i.focus(); }, 60);
      }
      // Show a wrong/empty-password message and re-enable submit, without
      // re-rendering (so the input keeps its value and focus).
      const err = $("#tfa-err"), btn = $("#tfa-btn");
      if (err && st.error) {
        err.textContent = st.error; err.classList.remove("hidden");
        if (btn) { btn.disabled = false; btn.textContent = t("submit"); }
      }
    } else if (st.state === "success") {
      clearInterval(poll);
      toast(t("tg_login_success"), "ok");
      refreshTgChip();
      showView("dashboard");
    } else if (st.state === "error") {
      clearInterval(poll); phase = "error";
      flow.innerHTML = `<p class="error-text">${esc(st.error || t("login_failed"))}</p>
        <button class="btn secondary" id="retry-qr">${t("retry")}</button>`;
      $("#retry-qr").addEventListener("click", () => startQrFlow(flow));
    }
  };
  const tick = async () => { try { draw(await api.tgLoginStatus()); } catch (_) {} };
  await tick();
  poll = setInterval(tick, 1500);
}

function renderStats(host, d) {
  const c = d.counts || {};
  host.innerHTML = `
    <div class="stat"><div class="n">${d.active}</div><div class="l">${t("stat_downloading")}</div></div>
    <div class="stat"><div class="n">${d.queued}</div><div class="l">${t("stat_queued")}</div></div>
    <div class="stat"><div class="n">${c.done || 0}</div><div class="l">${t("stat_done")}</div></div>
    <div class="stat"><div class="n">${(d.failed || []).length}</div><div class="l">${t("stat_failed")}</div></div>`;
}

function renderTasks(host, d) {
  const rows = d.recent || [];
  if (!rows.length) { host.innerHTML = `<div class="empty">${t("no_tasks")}</div>`; return; }
  host.innerHTML = rows.map((j) => {
    const live = state.live[j.id];
    const dl = live ? live.downloaded : j.downloaded;
    const total = live ? live.total : j.size;
    const pct = total ? Math.min(100, Math.round((dl / total) * 100)) : 0;
    const running = j.status === "running" || (live && j.status !== "done" && j.status !== "failed");
    const statusKey = running ? "running" : j.status;
    let meta = "";
    if (running && total) {
      meta = `${human(dl)} / ${human(total)}` + (live && live.speed ? ` · ${human(live.speed)}/s` : "");
    } else if (j.status === "failed") { meta = esc(j.error || ""); }
    else if (j.status === "done" || j.status === "skipped") { meta = j.size ? human(j.size) : ""; }
    return `<div class="task">
      <div class="task-top">
        <span class="task-name">${esc(j.title || j.filename || t("msg_n", { n: j.msg_id }))}</span>
        <span class="task-status st-${statusKey}">${t("st_" + statusKey)}</span>
      </div>
      ${running ? `<div class="progress"><span style="width:${pct}%"></span></div>` : ""}
      ${meta ? `<div class="task-meta"><span>${meta}</span></div>` : ""}
    </div>`;
  }).join("");
}

// ---------- Files ---------- //
function fileIcon(name) {
  const e = (name.split(".").pop() || "").toLowerCase();
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic"].includes(e)) return "🖼";
  if (["mp4", "mkv", "mov", "avi", "webm", "flv", "ts", "m4v"].includes(e)) return "🎬";
  if (["mp3", "flac", "wav", "ogg", "m4a", "aac"].includes(e)) return "🎵";
  if (["zip", "rar", "7z", "tar", "gz"].includes(e)) return "🗜";
  if (["pdf"].includes(e)) return "📕";
  if (["doc", "docx", "txt", "md", "rtf"].includes(e)) return "📄";
  return "📄";
}

async function mountFiles(host) {
  let cur = null;
  async function load(path) {
    host.innerHTML = `<div class="empty"><span class="spin"></span></div>`;
    try { cur = await api.files(path); } catch (e) { host.innerHTML = `<p class="error-text">${esc(e.message)}</p>`; return; }
    render();
  }

  async function del(path, name) {
    if (!confirm(t("confirm_delete", { name }))) return;
    try { await api.deleteFile({ path }); toast(t("deleted"), "ok"); load(cur.path); }
    catch (e) { toast(e.message, "err"); }
  }
  async function rename(path, oldName) {
    const name = prompt(t("rename_prompt"), oldName);
    if (!name || name === oldName) return;
    try { await api.renameFile({ path, name: name.trim() }); toast(t("renamed"), "ok"); load(cur.path); }
    catch (e) { toast(e.message, "err"); }
  }

  function render() {
    cur.folders = cur.folders || [];   // defensive: never hang on a shape mismatch
    cur.files = cur.files || [];
    const parts = cur.rel === "/" ? [] : cur.rel.replace(/^\//, "").split("/");
    let acc = cur.root;
    const crumbs = [`<span class="crumb" data-path="${esc(cur.root)}">${t("root_home")}</span>`];
    parts.forEach((p) => { acc += "/" + p; crumbs.push(`<span>/</span><span class="crumb" data-path="${esc(acc)}">${esc(p)}</span>`); });

    const folderRows = cur.folders.map((f) => `
      <div class="fm-row">
        <span class="ico open-dir" data-path="${esc(f.path)}" role="button">📁</span>
        <span class="fm-name open-dir" data-path="${esc(f.path)}" role="button">${esc(f.name)}</span>
        <button class="btn tiny secondary" data-set="${esc(f.path)}">${t("set_as_dir")}</button>
        <button class="icon-btn tiny" data-ren="${esc(f.path)}" data-name="${esc(f.name)}" title="${t("rename")}">✏️</button>
        <button class="icon-btn tiny danger" data-del="${esc(f.path)}" data-name="${esc(f.name)}" title="${t("delete")}">🗑</button>
      </div>`).join("");

    const fileRows = cur.files.map((f) => `
      <div class="fm-row">
        <span class="ico">${fileIcon(f.name)}</span>
        <a class="fm-name" href="${api.fileUrl(f.path, true)}" target="_blank" rel="noopener" title="${t("open_file")}">${esc(f.name)}</a>
        <span class="fm-size">${human(f.size)}</span>
        <a class="btn tiny secondary" href="${api.fileUrl(f.path, false)}">${t("download_file")}</a>
        <button class="icon-btn tiny" data-ren="${esc(f.path)}" data-name="${esc(f.name)}" title="${t("rename")}">✏️</button>
        <button class="icon-btn tiny danger" data-del="${esc(f.path)}" data-name="${esc(f.name)}" title="${t("delete")}">🗑</button>
      </div>`).join("");

    host.innerHTML = `
      <div class="panel">
        <h2>${t("files_title")} <span class="spacer"></span>
          <span class="chip">${t("current_dir", { p: esc(cur.current_rel) })}</span></h2>
        <div class="crumbs">${crumbs.join(" ")}</div>
        <div class="composer" style="margin-bottom:14px">
          <input class="input" id="new-folder" placeholder="${t("new_folder_ph")}">
          <button class="btn small" id="mk">${t("mk_set")}</button>
          ${cur.is_current ? "" : `<button class="btn small secondary" id="use">${t("set_as_dir")}</button>`}
        </div>
        ${cur.folders.length ? `<div class="fm-section">${t("folders_h")} · ${cur.folders.length}</div>${folderRows}` : ""}
        ${cur.files.length ? `<div class="fm-section">${t("files_h")} · ${cur.files.length}</div>${fileRows}` : ""}
        ${(!cur.folders.length && !cur.files.length) ? `<div class="empty">${t("empty_dir")}</div>` : ""}
      </div>`;

    host.querySelectorAll(".crumb").forEach((c) => c.addEventListener("click", () => load(c.dataset.path)));
    host.querySelectorAll(".open-dir").forEach((f) => f.addEventListener("click", () => load(f.dataset.path)));
    host.querySelectorAll("[data-set]").forEach((b) => b.addEventListener("click", async () => {
      try { await api.setCurrent({ path: b.dataset.set }); toast(t("set_dir_done"), "ok"); load(cur.path); }
      catch (e) { toast(e.message, "err"); }
    }));
    host.querySelectorAll("[data-ren]").forEach((b) => b.addEventListener("click", () => rename(b.dataset.ren, b.dataset.name)));
    host.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => del(b.dataset.del, b.dataset.name)));

    $("#mk").addEventListener("click", async () => {
      const name = $("#new-folder").value.trim();
      if (!name) return;
      try { cur = await api.mkdir({ parent: cur.path, name }); toast(t("created_switched"), "ok"); render(); }
      catch (e) { toast(e.message, "err"); }
    });
    const useBtn = $("#use");
    if (useBtn) useBtn.addEventListener("click", async () => {
      try { await api.setCurrent({ path: cur.path }); toast(t("set_dir_done"), "ok"); load(cur.path); }
      catch (e) { toast(e.message, "err"); }
    });
  }
  await load(null);
  return {};
}

// ---------- Proxy ---------- //
async function mountProxy(host) {
  // load() re-fetches settings and re-renders, so the status card (mode chip)
  // never goes stale after a save/toggle.
  async function load() {
    host.innerHTML = `<div class="empty"><span class="spin"></span></div>`;
    let s;
    try { s = await api.settings(); } catch (e) { host.innerHTML = `<p class="error-text">${esc(e.message)}</p>`; return; }
    render(s.proxy);
  }

  function render(p) {
    const modeName = { off: t("mode_off"), mihomo: t("mode_mihomo"), external: t("mode_external") }[p.mode] || p.mode;
    const viaProxy = p.mode === "mihomo";
    host.innerHTML = `
      <div class="panel">
        <h2>${t("proxy_status")}</h2>
        <div class="setting-row">
          <span class="dot ${p.mihomo_reachable ? "on" : "off"}"></span>
          <div class="label">${t("mihomo_sidecar")}
            <small>${p.mihomo_enabled ? (p.mihomo_reachable ? t("mihomo_connected") : t("mihomo_unreachable")) : t("mihomo_disabled")}</small></div>
          <span class="chip">${t("current_mode", { m: modeName })}</span>
        </div>
        <div class="setting-row">
          <div class="label">${t("traffic_label")}</div>
          <div class="lang-switch">
            <button data-mode="off" class="${!viaProxy ? "on" : ""}">${t("mode_direct_btn")}</button>
            <button data-mode="mihomo" class="${viaProxy ? "on" : ""}">${t("mode_proxy_btn")}</button>
          </div>
        </div>
      </div>
      <div class="panel">
        <h2>${t("subscription_h")}</h2>
        <p class="panel-note">${t("subscription_note")}</p>
        <div class="composer">
          <input class="input" id="sub-url" placeholder="https://.../subscribe  ·  vless://…">
          <button class="btn small" id="sub-save">${t("save_enable")}</button>
          <button class="btn small secondary" id="sub-refresh">${t("refresh")}</button>
        </div>
        ${p.subscription_set ? `<p class="hint">${t("subscription_set")}</p>` : ""}
        <div id="nodes" style="margin-top:14px"></div>
      </div>
      <div class="panel">
        <h2>${t("ext_proxy_h")}</h2>
        <p class="panel-note">${t("ext_proxy_note")}</p>
        <div class="setting-row"><div class="label">${t("mode_label")}</div>
          <select class="input" id="ext-mode" style="max-width:180px">
            <option value="off">${t("mode_direct_opt")}</option>
            <option value="external">${t("mode_external_opt")}</option>
          </select></div>
        <div class="row"><div class="field"><label>${t("type_label")}</label>
          <select class="input" id="ext-type"><option value="socks5">SOCKS5</option><option value="http">HTTP</option></select></div>
          <div class="field"><label>${t("address")}</label><input class="input" id="ext-host" placeholder="127.0.0.1"></div>
          <div class="field"><label>${t("port")}</label><input class="input" id="ext-port" placeholder="7890"></div></div>
        <button class="btn small" id="ext-save">${t("save")}</button>
      </div>`;

    // Traffic direction toggle (direct ⇄ via the mihomo sidecar).
    host.querySelectorAll(".lang-switch [data-mode]").forEach((b) => b.addEventListener("click", async () => {
      const wantProxy = b.dataset.mode === "mihomo";
      if (wantProxy === viaProxy) return;
      try {
        const r = await api.proxyMode(b.dataset.mode);
        toast(r.note || (wantProxy ? t("switched_proxy") : t("switched_direct")), "ok");
      } catch (e) { toast(e.message, "err"); }
      load();
    }));

    $("#ext-mode").value = p.mode === "external" ? "external" : "off";
    $("#ext-type").value = p.type || "socks5";
    if (p.host) $("#ext-host").value = p.host;
    if (p.port) $("#ext-port").value = p.port;

    $("#sub-save").addEventListener("click", async () => {
      const url = $("#sub-url").value.trim();
      if (!url) return toast(t("fill_sub"), "err");
      toast(t("applying_sub"));
      try {
        const r = await api.setSubscription({ url });
        if (r.ok) { toast(r.note || t("saved"), "ok"); load(); }
        else toast(r.error, "err");
      } catch (e) { toast(e.message, "err"); }
    });
    $("#sub-refresh").addEventListener("click", async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true;  // avoid spamming the airport into a rate-limit
      try {
        const r = await api.proxyRefresh();
        toast(r.ok ? t("refreshed_sub") : (r.error || t("refresh_failed")), r.ok ? "ok" : "err");
        loadNodes();
      } finally {
        setTimeout(() => { btn.disabled = false; }, 3000);
      }
    });
    $("#ext-save").addEventListener("click", async () => {
      const body = { mode: $("#ext-mode").value, type: $("#ext-type").value,
        host: $("#ext-host").value.trim(), port: parseInt($("#ext-port").value.trim(), 10) || null };
      try { const r = await api.setProxy(body); toast(r.note || t("saved"), "ok"); load(); }
      catch (e) { toast(e.message, "err"); }
    });

    async function loadNodes() {
      const box = $("#nodes");
      box.innerHTML = `<div class="empty"><span class="spin"></span></div>`;
      const r = await api.proxyNodes();
      if (!r.ok) { box.innerHTML = `<p class="hint">${esc(r.error || t("cannot_get_nodes"))}</p>`; return; }
      if (!r.nodes.length) { box.innerHTML = `<p class="hint">${t("no_nodes")}</p>`; return; }
      box.innerHTML = r.nodes.map((n) => `
        <div class="node ${n === r.selected ? "selected" : ""}" data-name="${esc(n)}">
          <span class="name">${esc(n)}</span>
          <span class="delay" data-delay></span>
          <button class="btn tiny secondary" data-test>${t("test")}</button>
          <button class="btn tiny" data-select ${n === r.selected ? "disabled" : ""}>${n === r.selected ? t("in_use") : t("select")}</button>
        </div>`).join("");
      box.querySelectorAll(".node").forEach((el) => {
        const name = el.dataset.name;
        el.querySelector("[data-select]").addEventListener("click", async () => {
          const r2 = await api.proxySelect({ name });
          toast(r2.ok ? t("switched_node") : (r2.error || t("op_failed")), r2.ok ? "ok" : "err");
          if (r2.ok) loadNodes();
        });
        el.querySelector("[data-test]").addEventListener("click", async (ev) => {
          ev.target.textContent = "…";
          const r2 = await api.proxyTest({ name });
          el.querySelector("[data-delay]").textContent = r2.ok ? r2.delay + " ms" : t("timeout");
          ev.target.textContent = t("test");
        });
      });
    }
    if (p.subscription_set) loadNodes();
  }

  await load();
  return {};
}

// ---------- Settings ---------- //
async function mountSettings(host) {
  const s = await api.settings();
  host.innerHTML = `
    <div class="panel">
      <h2>${t("language_h")}</h2>
      <div class="setting-row">
        <div class="label">${t("language_label")}</div>
        ${langSwitcher()}
      </div>
    </div>
    <div class="panel">
      <h2>${t("concurrency_h")}</h2>
      <div class="setting-row">
        <div class="label">${t("concurrency_label")}<small>${t("concurrency_sub")}</small></div>
        <div class="stepper"><button id="c-dec">−</button><span class="val" id="c-val">${s.concurrency}</span><button id="c-inc">+</button></div>
      </div>
    </div>
    <div class="panel">
      <h2>${t("bot_h")}</h2>
      <p class="panel-note">${t("bot_note")}</p>
      <div class="setting-row"><div class="label">${t("bot_enable")}</div>
        <label class="toggle"><input type="checkbox" id="bot-enabled" ${s.bot.enabled ? "checked" : ""}><span class="slider"></span></label></div>
      <div class="field"><label>Bot Token${s.bot.token_set ? " " + t("bot_token_set") : ""}</label>
        <input class="input" id="bot-token" placeholder="123456:ABC-..."></div>
      <div class="field"><label>${t("bot_admin")}</label>
        <input class="input" id="bot-admin" value="${esc(s.bot.admin_id || "")}" placeholder="${t("bot_admin_ph")}"></div>
      <button class="btn small" id="bot-save">${t("save_bot")}</button>
      <p class="hint">${t("status_label", { s: s.bot.running ? t("status_running") : t("status_stopped") })}</p>
    </div>
    <div class="panel">
      <h2>${t("root_h")}</h2>
      <div class="composer"><input class="input" id="root-path" value="${esc(s.root_path)}">
        <button class="btn small" id="root-save">${t("save")}</button></div>
      <p class="hint">${t("root_note")}</p>
    </div>
    <div class="panel">
      <h2>${t("pw_h")}</h2>
      <div class="field"><label>${t("current_pw")}</label><input class="input" id="pw-old" type="password"></div>
      <div class="field"><label>${t("new_pw")}</label><input class="input" id="pw-new" type="password"></div>
      <button class="btn small" id="pw-save">${t("update_pw")}</button>
    </div>`;

  wireLang(() => renderApp());

  const setConc = async (v) => { const r = await api.concurrency(v); $("#c-val").textContent = r.value; };
  $("#c-inc").addEventListener("click", () => setConc(parseInt($("#c-val").textContent) + 1));
  $("#c-dec").addEventListener("click", () => setConc(parseInt($("#c-val").textContent) - 1));

  $("#bot-save").addEventListener("click", async () => {
    const body = { enabled: $("#bot-enabled").checked, admin_id: parseInt($("#bot-admin").value.trim(), 10) || null };
    const tok = $("#bot-token").value.trim();
    if (tok) body.token = tok;
    try { const r = await api.setBot(body); toast(r.note || t("saved"), "ok"); }
    catch (e) { toast(e.message, "err"); }
  });
  $("#root-save").addEventListener("click", async () => {
    try { const r = await api.setRoot({ path: $("#root-path").value.trim() });
      toast(r.pending ? t("root_saved_pending", { n: r.pending }) : t("saved"), "ok"); }
    catch (e) { toast(e.message, "err"); }
  });
  $("#pw-save").addEventListener("click", async () => {
    try {
      await api.changePassword({ old_password: $("#pw-old").value, new_password: $("#pw-new").value });
      toast(t("pw_updated"), "ok");
      setTimeout(() => location.reload(), 1200);
    } catch (e) { toast(e.message, "err"); }
  });
  return {};
}

boot();
