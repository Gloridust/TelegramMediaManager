// TelegramMediaManager — single-page panel controller.
import { api } from "./api.js";
import { $, esc, human, dur, toast, initTheme, toggleTheme, frag } from "./ui.js";

const app = $("#app");
const state = { view: "dashboard", tg: null, ws: null, live: {}, viewCtl: null };

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
    app.innerHTML = `<div class="center-wrap"><div class="card"><h1>无法连接服务</h1>
      <p class="sub">${esc(e.message)}</p><button class="btn" onclick="location.reload()">重试</button></div></div>`;
  }
}

// ===================================================================== //
// Setup wizard (first run)
// ===================================================================== //
function renderSetup() {
  app.innerHTML = `
  <div class="center-wrap"><div class="card">
    <div class="brand-logo">⬇</div>
    <h1>欢迎使用</h1>
    <p class="sub">首次使用，请创建管理员账号。之后所有配置都在面板中完成，无需手动编辑任何文件。</p>
    <form id="setup-form">
      <div class="field"><label>管理员用户名</label>
        <input class="input" name="username" autocomplete="username" minlength="3" required></div>
      <div class="field"><label>密码（至少 8 位）</label>
        <input class="input" name="password" type="password" autocomplete="new-password" minlength="8" required></div>
      <div class="field"><label>确认密码</label>
        <input class="input" name="confirm" type="password" minlength="8" required></div>
      <p class="error-text hidden" id="setup-err"></p>
      <button class="btn" type="submit">创建并进入</button>
      <p class="hint" style="margin-top:14px">⚠️ 面板可完全控制你的 Telegram 账号，请仅在内网使用，切勿直接暴露到公网。</p>
    </form>
  </div></div>`;
  $("#setup-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, err = $("#setup-err");
    if (f.password.value !== f.confirm.value) { err.textContent = "两次密码不一致"; err.classList.remove("hidden"); return; }
    try {
      await api.setup({ username: f.username.value.trim(), password: f.password.value });
      toast("初始化完成", "ok");
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
    <div class="brand-logo">⬇</div>
    <h1>登录</h1>
    <p class="sub">Telegram Media Manager 控制面板</p>
    <form id="login-form">
      <div class="field"><label>用户名</label>
        <input class="input" name="username" autocomplete="username" required></div>
      <div class="field"><label>密码</label>
        <input class="input" name="password" type="password" autocomplete="current-password" required></div>
      <p class="error-text hidden" id="login-err"></p>
      <button class="btn" type="submit">登录</button>
    </form>
  </div></div>`;
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, err = $("#login-err");
    try {
      await api.login({ username: f.username.value.trim(), password: f.password.value });
      renderApp();
    } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
  });
}

// ===================================================================== //
// App shell
// ===================================================================== //
const NAV = [
  { id: "dashboard", ico: "📥", label: "下载任务" },
  { id: "files", ico: "🗂", label: "文件夹" },
  { id: "proxy", ico: "🌐", label: "代理" },
  { id: "settings", ico: "⚙️", label: "设置" },
];

function renderApp() {
  app.innerHTML = `
  <div class="app-shell">
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-head">
        <div class="logo">⬇</div>
        <div class="title">Media Manager<small>Telegram 媒体下载</small></div>
      </div>
      <nav class="nav" id="nav">
        ${NAV.map((n) => `<button class="nav-item" data-view="${n.id}">
          <span class="ico">${n.ico}</span>${n.label}
          ${n.id === "dashboard" ? '<span class="badge hidden" id="fail-badge"></span>' : ""}
        </button>`).join("")}
      </nav>
      <div class="sidebar-foot">
        <button class="nav-item" id="theme-btn"><span class="ico">🌓</span>切换主题</button>
        <button class="nav-item" id="logout-btn"><span class="ico">🚪</span>退出登录</button>
      </div>
    </aside>
    <div class="scrim hidden" id="scrim"></div>
    <main class="main">
      <header class="topbar">
        <button class="menu-toggle" id="menu-toggle">☰</button>
        <span class="page-title" id="page-title">下载任务</span>
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
  showView("dashboard");
  refreshTgChip();
}

function setActiveNav(view) {
  document.querySelectorAll(".nav-item[data-view]").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  const nav = NAV.find((n) => n.id === view);
  $("#page-title").textContent = nav ? nav.label : "";
  $("#sidebar").classList.remove("open");
}

async function showView(view) {
  if (state.viewCtl && state.viewCtl.leave) state.viewCtl.leave();
  state.view = view;
  setActiveNav(view);
  const host = $("#view");
  host.innerHTML = `<div class="empty"><span class="spin"></span></div>`;
  const ctl = VIEWS[view];
  state.viewCtl = await ctl(host);
}

// ===================================================================== //
// WebSocket live feed
// ===================================================================== //
function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/ws`);
  state.ws = ws;
  ws.onmessage = (ev) => {
    const evt = JSON.parse(ev.data);
    if (evt.type === "ping") return;
    if (evt.type === "job_progress" && evt.job_id != null) {
      state.live[evt.job_id] = evt;
    }
    if (state.viewCtl && state.viewCtl.onEvent) state.viewCtl.onEvent(evt);
  };
  ws.onclose = () => setTimeout(connectWs, 3000); // auto-reconnect
}

async function refreshTgChip() {
  try {
    const s = await api.tgStatus();
    state.tg = s;
    const chip = $("#tg-chip");
    if (!chip) return;
    if (s.authorized && s.me) {
      chip.textContent = "👤 " + s.me.name;
      chip.classList.remove("hidden");
    } else {
      chip.textContent = "未登录 Telegram";
      chip.classList.remove("hidden");
    }
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
      <h2>➕ 新建下载</h2>
      <p class="panel-note">粘贴 t.me 消息链接下载受限媒体（含相册），或粘贴频道链接批量下载。</p>
      <div class="composer">
        <input class="input" id="link-input" placeholder="https://t.me/xxx/123">
        <button class="btn small" id="btn-link">下载</button>
        <button class="btn small secondary" id="btn-channel">整个频道</button>
      </div>
    </div>
    <div class="panel">
      <h2>📊 概览 <span class="spacer"></span>
        <button class="btn tiny secondary" id="btn-resume">继续</button>
        <button class="btn tiny secondary" id="btn-retry">重试失败</button>
        <button class="btn tiny danger" id="btn-cancel">取消队列</button>
      </h2>
      <div class="stat-row" id="stats"></div>
    </div>
    <div class="panel">
      <h2>📥 任务 <span class="spacer"></span>
        <button class="btn tiny ghost" id="btn-clear">清空历史</button></h2>
      <div id="task-list"><div class="empty"><span class="spin"></span></div></div>
    </div>`;

  if (!loggedIn) renderTgConnect($("#tg-connect"));

  $("#btn-link").addEventListener("click", submitLink);
  $("#btn-channel").addEventListener("click", submitChannel);
  $("#link-input").addEventListener("keydown", (e) => { if (e.key === "Enter") submitLink(); });
  $("#btn-resume").addEventListener("click", async () => {
    const r = await api.resume(); toast(`已重新入队 ${r.queued} 个任务`, "ok"); loadTasks();
  });
  $("#btn-retry").addEventListener("click", async () => {
    const r = await api.retryFailed(); toast(`已重排 ${r.requeued} 个失败任务`, "ok"); loadTasks();
  });
  $("#btn-cancel").addEventListener("click", async () => {
    const r = await api.cancel(); toast(`已清除队列 ${r.drained} 项`); loadTasks();
  });
  $("#btn-clear").addEventListener("click", async () => {
    const r = await api.clearHistory(); toast(`已清除 ${r.cleared} 条历史`); loadTasks();
  });

  async function submitLink() {
    const inp = $("#link-input"), link = inp.value.trim();
    if (!link) return;
    inp.disabled = true;
    try {
      const r = await api.dlLink({ link });
      if (r.ok) { toast(`已加入 ${r.count} 个媒体`, "ok"); inp.value = ""; }
      else toast(r.error, "err");
    } catch (e) { toast(e.message, "err"); }
    inp.disabled = false; loadTasks();
  }
  async function submitChannel() {
    const inp = $("#link-input"), link = inp.value.trim();
    if (!link) return;
    try {
      const r = await api.dlChannel({ link });
      if (r.ok) { toast(`开始下载频道：${r.title}`, "ok"); inp.value = ""; }
      else toast(r.error, "err");
    } catch (e) { toast(e.message, "err"); }
    loadTasks();
  }

  let timer = null;
  async function loadTasks() {
    let t;
    try { t = await api.tasks(); } catch (_) { return; }
    renderStats($("#stats"), t);
    renderTasks($("#task-list"), t);
    const badge = $("#fail-badge");
    const failed = (t.failed || []).length;
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
      <h2>🔗 连接 Telegram 账号</h2>
      <p class="panel-note">需要一次登录以访问受限内容。凭据仅保存在本机数据卷中。</p>
      <div id="tg-flow"></div>
    </div>`;
  const flow = $("#tg-flow");
  if (!state.tg || !state.tg.credentials_ready) {
    flow.innerHTML = `
      <div class="field"><label>API ID</label><input class="input" id="api-id" placeholder="从 my.telegram.org 获取"></div>
      <div class="field"><label>API Hash</label><input class="input" id="api-hash"></div>
      <button class="btn" id="save-creds">保存并继续</button>
      <p class="hint">前往 <a href="https://my.telegram.org" target="_blank" rel="noopener">my.telegram.org</a> → API development tools 创建应用获取。</p>`;
    $("#save-creds").addEventListener("click", async () => {
      const api_id = parseInt($("#api-id").value.trim(), 10);
      const api_hash = $("#api-hash").value.trim();
      if (!api_id || !api_hash) return toast("请填写 API ID 与 API Hash", "err");
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
  flow.innerHTML = `<div class="empty"><span class="spin"></span> 正在生成二维码…</div>`;
  try { await api.tgLoginStart(); } catch (e) { flow.innerHTML = `<p class="error-text">${esc(e.message)}</p>`; return; }
  let poll = null;
  const draw = (st) => {
    if (st.state === "waiting") {
      flow.innerHTML = `<div class="qr-wrap">
        <img src="/api/telegram/login/qr.png?t=${Date.now()}" alt="QR">
        <div class="qr-steps">用手机 Telegram：设置 → 设备 → 关联桌面设备，扫描上方二维码。二维码会自动刷新。</div>
      </div>`;
    } else if (st.state === "need_password") {
      flow.innerHTML = `<div class="field"><label>两步验证密码</label>
        <input class="input" id="tfa" type="password"></div>
        <button class="btn" id="tfa-btn">提交</button>`;
      $("#tfa-btn").addEventListener("click", async () => {
        try { await api.tgLoginPassword({ password: $("#tfa").value }); toast("已提交", "ok"); }
        catch (e) { toast(e.message, "err"); }
      });
    } else if (st.state === "success") {
      clearInterval(poll);
      toast("Telegram 登录成功", "ok");
      refreshTgChip();
      showView("dashboard");
    } else if (st.state === "error") {
      flow.innerHTML = `<p class="error-text">${esc(st.error || "登录失败")}</p>
        <button class="btn secondary" id="retry-qr">重试</button>`;
      clearInterval(poll);
      $("#retry-qr").addEventListener("click", () => startQrFlow(flow));
    }
  };
  const tick = async () => { try { draw(await api.tgLoginStatus()); } catch (_) {} };
  await tick();
  poll = setInterval(tick, 2000);
}

function renderStats(host, t) {
  const c = t.counts || {};
  host.innerHTML = `
    <div class="stat"><div class="n">${t.active}</div><div class="l">下载中</div></div>
    <div class="stat"><div class="n">${t.queued}</div><div class="l">排队</div></div>
    <div class="stat"><div class="n">${c.done || 0}</div><div class="l">已完成</div></div>
    <div class="stat"><div class="n">${(t.failed || []).length}</div><div class="l">失败</div></div>`;
}

function renderTasks(host, t) {
  const rows = t.recent || [];
  if (!rows.length) { host.innerHTML = `<div class="empty">还没有任务。粘贴一个链接开始吧。</div>`; return; }
  const labels = { done: "已完成", skipped: "已跳过", failed: "失败", running: "下载中", pending: "排队", cancelled: "已取消" };
  host.innerHTML = rows.map((j) => {
    const live = state.live[j.id];
    const dl = live ? live.downloaded : j.downloaded;
    const total = live ? live.total : j.size;
    const pct = total ? Math.min(100, Math.round((dl / total) * 100)) : 0;
    const running = j.status === "running" || (live && j.status !== "done" && j.status !== "failed");
    let meta = "";
    if (running && total) {
      meta = `${human(dl)} / ${human(total)}` + (live && live.speed ? ` · ${human(live.speed)}/s` : "");
    } else if (j.status === "failed") {
      meta = esc(j.error || "");
    } else if (j.status === "done" || j.status === "skipped") {
      meta = j.size ? human(j.size) : "";
    }
    return `<div class="task">
      <div class="task-top">
        <span class="task-name">${esc(j.title || j.filename || ("消息 " + j.msg_id))}</span>
        <span class="task-status st-${running ? "running" : j.status}">${labels[running ? "running" : j.status] || j.status}</span>
      </div>
      ${running ? `<div class="progress"><span style="width:${pct}%"></span></div>` : ""}
      ${meta ? `<div class="task-meta"><span>${meta}</span></div>` : ""}
    </div>`;
  }).join("");
}

// ---------- Files ---------- //
async function mountFiles(host) {
  let cur = null;
  async function load(path) {
    host.innerHTML = `<div class="empty"><span class="spin"></span></div>`;
    cur = await api.files(path);
    render();
  }
  function render() {
    const parts = cur.rel === "/" ? [] : cur.rel.replace(/^\//, "").split("/");
    let acc = cur.root;
    const crumbs = [`<span class="crumb" data-path="${esc(cur.root)}">🏠 根目录</span>`];
    parts.forEach((p) => { acc += "/" + p; crumbs.push(`<span>/</span><span class="crumb" data-path="${esc(acc)}">${esc(p)}</span>`); });

    host.innerHTML = `
      <div class="panel">
        <h2>🗂 文件夹管理 <span class="spacer"></span>
          <span class="chip">当前下载目录：${esc(cur.current_rel)}</span></h2>
        <div class="crumbs">${crumbs.join(" ")}</div>
        <div class="composer" style="margin-bottom:14px">
          <input class="input" id="new-folder" placeholder="新建子文件夹名称">
          <button class="btn small" id="mk">新建并设为下载目录</button>
          ${cur.is_current ? "" : `<button class="btn small secondary" id="use">设为下载目录</button>`}
        </div>
        ${cur.entries.length
          ? `<div class="folder-grid">${cur.entries.map((e) =>
              `<div class="folder" data-path="${esc(e.path)}"><span class="ico">📁</span>${esc(e.name)}</div>`).join("")}</div>`
          : `<div class="empty">（空文件夹）</div>`}
      </div>`;

    host.querySelectorAll(".crumb").forEach((c) => c.addEventListener("click", () => load(c.dataset.path)));
    host.querySelectorAll(".folder").forEach((f) => f.addEventListener("click", () => load(f.dataset.path)));
    $("#mk").addEventListener("click", async () => {
      const name = $("#new-folder").value.trim();
      if (!name) return;
      try { cur = await api.mkdir({ parent: cur.path, name }); toast("已创建并切换", "ok"); render(); }
      catch (e) { toast(e.message, "err"); }
    });
    const useBtn = $("#use");
    if (useBtn) useBtn.addEventListener("click", async () => {
      try { await api.setCurrent({ path: cur.path }); toast("已设为下载目录", "ok"); load(cur.path); }
      catch (e) { toast(e.message, "err"); }
    });
  }
  await load(null);
  return {};
}

// ---------- Proxy ---------- //
async function mountProxy(host) {
  const s = await api.settings();
  const p = s.proxy;
  host.innerHTML = `
    <div class="panel">
      <h2>🌐 代理状态</h2>
      <div class="setting-row">
        <span class="dot ${p.mihomo_reachable ? "on" : "off"}"></span>
        <div class="label">mihomo 边车
          <small>${p.mihomo_enabled ? (p.mihomo_reachable ? "已连接，可用" : "已启用但未连接——请确认 compose 中的 mihomo 服务在运行") : "未启用"}</small></div>
        <span class="chip">当前模式：${({ off: "直连", mihomo: "订阅代理", external: "外部代理" })[p.mode] || p.mode}</span>
      </div>
    </div>
    <div class="panel">
      <h2>📡 订阅（vless 等）</h2>
      <p class="panel-note">粘贴机场订阅链接，由 mihomo 边车拉取并解析节点。应用的全部流量都会走选中的节点。</p>
      <div class="composer">
        <input class="input" id="sub-url" placeholder="https://.../subscribe?token=..." value="">
        <button class="btn small" id="sub-save">保存并启用</button>
        <button class="btn small secondary" id="sub-refresh">刷新</button>
      </div>
      ${p.subscription_set ? `<p class="hint">✅ 已配置订阅。</p>` : ""}
      <div id="nodes" style="margin-top:14px"></div>
    </div>
    <div class="panel">
      <h2>🔌 或使用外部代理</h2>
      <p class="panel-note">若你已在 NAS/路由器上运行 Clash 等代理，可直接填写它的地址，不必用订阅。</p>
      <div class="setting-row"><div class="label">模式</div>
        <select class="input" id="ext-mode" style="max-width:160px">
          <option value="off">直连（不走代理）</option>
          <option value="external">外部代理</option>
        </select></div>
      <div class="row"><div class="field"><label>类型</label>
        <select class="input" id="ext-type"><option value="socks5">SOCKS5</option><option value="http">HTTP</option></select></div>
        <div class="field"><label>地址</label><input class="input" id="ext-host" placeholder="127.0.0.1"></div>
        <div class="field"><label>端口</label><input class="input" id="ext-port" placeholder="7890"></div></div>
      <button class="btn small" id="ext-save">保存</button>
    </div>`;

  // Prefill external fields.
  $("#ext-mode").value = p.mode === "external" ? "external" : "off";
  $("#ext-type").value = p.type || "socks5";
  if (p.host) $("#ext-host").value = p.host;
  if (p.port) $("#ext-port").value = p.port;

  $("#sub-save").addEventListener("click", async () => {
    const url = $("#sub-url").value.trim();
    if (!url) return toast("请填写订阅链接", "err");
    toast("正在应用订阅…");
    try {
      const r = await api.setSubscription({ url });
      if (r.ok) { toast(r.note || "已应用", "ok"); loadNodes(); }
      else toast(r.error, "err");
    } catch (e) { toast(e.message, "err"); }
  });
  $("#sub-refresh").addEventListener("click", async () => {
    const r = await api.proxyRefresh();
    toast(r.ok ? "已刷新订阅" : (r.error || "刷新失败"), r.ok ? "ok" : "err");
    loadNodes();
  });
  $("#ext-save").addEventListener("click", async () => {
    const body = { mode: $("#ext-mode").value, type: $("#ext-type").value,
      host: $("#ext-host").value.trim(), port: parseInt($("#ext-port").value.trim(), 10) || null };
    try { const r = await api.setProxy(body); toast(r.note || "已保存", "ok"); }
    catch (e) { toast(e.message, "err"); }
  });

  async function loadNodes() {
    const box = $("#nodes");
    box.innerHTML = `<div class="empty"><span class="spin"></span></div>`;
    const r = await api.proxyNodes();
    if (!r.ok) { box.innerHTML = `<p class="hint">${esc(r.error || "无法获取节点")}</p>`; return; }
    if (!r.nodes.length) { box.innerHTML = `<p class="hint">暂无节点。请先保存订阅。</p>`; return; }
    box.innerHTML = r.nodes.map((n) => `
      <div class="node ${n === r.selected ? "selected" : ""}" data-name="${esc(n)}">
        <span class="name">${esc(n)}</span>
        <span class="delay" data-delay></span>
        <button class="btn tiny secondary" data-test>测速</button>
        <button class="btn tiny" data-select ${n === r.selected ? "disabled" : ""}>${n === r.selected ? "使用中" : "选择"}</button>
      </div>`).join("");
    box.querySelectorAll(".node").forEach((el) => {
      const name = el.dataset.name;
      el.querySelector("[data-select]").addEventListener("click", async () => {
        const r2 = await api.proxySelect({ name });
        toast(r2.ok ? "已切换节点" : (r2.error || "失败"), r2.ok ? "ok" : "err");
        if (r2.ok) loadNodes();
      });
      el.querySelector("[data-test]").addEventListener("click", async (ev) => {
        ev.target.textContent = "…";
        const r2 = await api.proxyTest({ name });
        el.querySelector("[data-delay]").textContent = r2.ok ? r2.delay + " ms" : "超时";
        ev.target.textContent = "测速";
      });
    });
  }
  if (p.subscription_set) loadNodes();
  return {};
}

// ---------- Settings ---------- //
async function mountSettings(host) {
  const s = await api.settings();
  host.innerHTML = `
    <div class="panel">
      <h2>⚡ 下载并发</h2>
      <div class="setting-row">
        <div class="label">同时下载数<small>调低不会打断进行中的下载</small></div>
        <div class="stepper"><button id="c-dec">−</button><span class="val" id="c-val">${s.concurrency}</span><button id="c-inc">+</button></div>
      </div>
    </div>
    <div class="panel">
      <h2>🤖 Telegram 机器人（可选）</h2>
      <p class="panel-note">保留手机端快捷控制。与面板共享同一下载引擎与状态。</p>
      <div class="setting-row"><div class="label">启用机器人</div>
        <label class="toggle"><input type="checkbox" id="bot-enabled" ${s.bot.enabled ? "checked" : ""}><span class="slider"></span></label></div>
      <div class="field"><label>Bot Token${s.bot.token_set ? "（已设置，留空则不变）" : ""}</label>
        <input class="input" id="bot-token" placeholder="123456:ABC-..."></div>
      <div class="field"><label>管理员 User ID</label>
        <input class="input" id="bot-admin" value="${esc(s.bot.admin_id || "")}" placeholder="从 @userinfobot 获取"></div>
      <button class="btn small" id="bot-save">保存机器人设置</button>
      <p class="hint">状态：${s.bot.running ? "🟢 运行中" : "⚪ 未运行"}</p>
    </div>
    <div class="panel">
      <h2>📁 工作根目录</h2>
      <div class="composer"><input class="input" id="root-path" value="${esc(s.root_path)}">
        <button class="btn small" id="root-save">保存</button></div>
      <p class="hint">整个下载树的根。移动目录后改这里，未完成任务会自动重定位。</p>
    </div>
    <div class="panel">
      <h2>🔐 修改密码</h2>
      <div class="field"><label>当前密码</label><input class="input" id="pw-old" type="password"></div>
      <div class="field"><label>新密码（至少 8 位）</label><input class="input" id="pw-new" type="password"></div>
      <button class="btn small" id="pw-save">更新密码（将登出所有设备）</button>
    </div>`;

  const setConc = async (v) => { const r = await api.concurrency(v); $("#c-val").textContent = r.value; };
  $("#c-inc").addEventListener("click", () => setConc(parseInt($("#c-val").textContent) + 1));
  $("#c-dec").addEventListener("click", () => setConc(parseInt($("#c-val").textContent) - 1));

  $("#bot-save").addEventListener("click", async () => {
    const body = { enabled: $("#bot-enabled").checked, admin_id: parseInt($("#bot-admin").value.trim(), 10) || null };
    const tok = $("#bot-token").value.trim();
    if (tok) body.token = tok;
    try { const r = await api.setBot(body); toast(r.note || "已保存", "ok"); }
    catch (e) { toast(e.message, "err"); }
  });
  $("#root-save").addEventListener("click", async () => {
    try { const r = await api.setRoot({ path: $("#root-path").value.trim() });
      toast(`已保存${r.pending ? `，${r.pending} 个待处理任务将重定位` : ""}`, "ok"); }
    catch (e) { toast(e.message, "err"); }
  });
  $("#pw-save").addEventListener("click", async () => {
    try {
      await api.changePassword({ old_password: $("#pw-old").value, new_password: $("#pw-new").value });
      toast("密码已更新，请重新登录", "ok");
      setTimeout(() => location.reload(), 1200);
    } catch (e) { toast(e.message, "err"); }
  });
  return {};
}

boot();
