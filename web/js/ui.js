// Small DOM + formatting helpers shared across views.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function human(n) {
  n = Number(n) || 0;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + u[i];
}

export function dur(sec) {
  sec = Math.max(0, Math.floor(sec));
  if (sec < 60) return sec + "秒";
  if (sec < 3600) return Math.floor(sec / 60) + "分" + (sec % 60) + "秒";
  return Math.floor(sec / 3600) + "时" + Math.floor((sec % 3600) / 60) + "分";
}

let toastN = 0;
export function toast(msg, kind = "") {
  const box = $("#toasts");
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  box.appendChild(el);
  const id = ++toastN;
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 2600);
  setTimeout(() => { if (el.parentNode) el.remove(); }, 3000);
  return id;
}

export function initTheme() {
  const saved = localStorage.getItem("tmm-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
}

export function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "dark" ? "light"
    : cur === "light" ? "dark"
    : (matchMedia("(prefers-color-scheme: dark)").matches ? "light" : "dark");
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("tmm-theme", next);
  return next;
}

// Render an HTML string into a fresh element tree.
export function frag(htmlStr) {
  const t = document.createElement("template");
  t.innerHTML = htmlStr.trim();
  return t.content;
}
