/* MDS Connect — веб-панель (PWA) для майстрів, адміністратора та директора.
 *
 * Без збірки та залежностей: один файл, ванільний JS.
 * Структура:  api → утиліти → каркас (навігація, модальні вікна) → екрани (VIEWS).
 * Усі гроші з API — цілі копійки; на екрані — гривні.
 * DOM будується через h() — без innerHTML для даних користувача (захист від XSS).
 */
"use strict";

// Сумісність зі старими вбудованими браузерами (Android 7–11 без оновлень WebView, Chrome < 86):
// replaceChildren — основа перемальовування екранів.
if (!Element.prototype.replaceChildren) {
  const replaceChildren = function (...nodes) {
    while (this.lastChild) this.removeChild(this.lastChild);
    this.append(...nodes);
  };
  Element.prototype.replaceChildren = replaceChildren;
  DocumentFragment.prototype.replaceChildren = replaceChildren;
}

// ============================================================ API
// сховище може бути недоступне (приватний режим, заборона сайтам) — програма працює і без нього
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* лише на сесію */ } },
  del(k) { try { localStorage.removeItem(k); } catch (_) { /* немає сховища */ } },
};
const api = {
  token: store.get("mds_token"),
  async req(method, path, body) {
    const opts = { method, headers: {} };
    if (this.token) opts.headers.Authorization = "Bearer " + this.token;
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      throw new Error("Немає з'єднання з сервером");
    }
    if (res.status === 401 && path !== "/api/auth/login") {
      logout();
      throw new Error("Сесія завершилась");
    }
    const data = res.status === 204 ? null : await res.json().catch(() => null);
    if (!res.ok) {
      const msg = (data && data.detail) || "Помилка " + res.status;
      if (res.status >= 500 || res.status === 0) pushErr(`${method} ${path} → ${res.status}: ${msg}`);
      throw new Error(msg);
    }
    return data;
  },
  get(p) { return this.req("GET", p); },
  post(p, b) { return this.req("POST", p, b === undefined ? {} : b); },
  patch(p, b) { return this.req("PATCH", p, b); },
  put(p, b) { return this.req("PUT", p, b); },
  del(p) { return this.req("DELETE", p); },
};

const state = { user: null, services: [], masters: [], materials: [], settings: null };

// ============================================================ утиліти
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const money = (k) => {
  const v = (k || 0) / 100;
  return v.toLocaleString("uk-UA", { minimumFractionDigits: k % 100 ? 2 : 0, maximumFractionDigits: 2 }) + " ₴";
};
const toKop = (s) => {
  const n = parseFloat(String(s).replace(/\s/g, "").replace(",", ".").replace(/[^\d.\-]/g, ""));
  return Number.isFinite(n) ? Math.round(n * 100) : NaN;
};
const priceText = (s) => (s.price_from ? "від " : "") + money(s.price);
const kopToInput = (k) => ((k || 0) / 100).toString();
const pct = (bp) => (bp / 100).toLocaleString("uk-UA") + "%";
const pad = (n) => String(n).padStart(2, "0");
// «1 запис», «2 записи», «5 записів»
const plural = (n, one, few, many) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many; };
const MONTHS_GEN = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"];
const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const today = () => isoDate(new Date());
const addDays = (iso, n) => { const d = new Date(iso + "T12:00"); d.setDate(d.getDate() + n); return isoDate(d); };
const hm = (iso) => iso.slice(11, 16);
const dmy = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}`;
const dm = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
const WD = ["Нд", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
const WD_LONG = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"];
const weekday = (iso) => WD[new Date(iso + "T12:00").getDay()];
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now() + "-" + Math.random().toString(16).slice(2));

const STATUS = {
  pending: "Очікує", confirmed: "Підтверджено", in_progress: "Триває",
  completed: "Завершено", cancelled: "Скасовано", no_show: "Не прийшов",
};
const SHIFT_STATUS = { open: "Відкрита", closed: "Очікує виплати", paid: "Виплачено" };
const PAY = { cash: "Готівка", card: "Картка", transfer: "Переказ" }; // «Переказ» — лише для старих записів
const PAY_CHOICES = { cash: "Готівка", card: "Картка" };
const ROLES = { director: "Директор", admin: "Адміністратор", master: "Майстер" };
const MAT_MODES = {
  salon: "Матеріали за рахунок салону",
  master: "Матеріали за рахунок майстра",
  split: "Матеріали ділимо пропорційно",
};
// «Оренда» прибрана з вибору (власне приміщення); старі записи з нею, якщо є, показуються як «Оренда»
const EXP_CAT = { utilities: "Комунальні", advertising: "Реклама", materials: "Закупівля матеріалів", fuel: "Пальне (закупівля)", other: "Інше" };
const expCat = (k) => EXP_CAT[k] || (k === "rent" ? "Оренда" : k);

const chip = (st, map = STATUS) => h("span", { class: "chip st-" + st }, map[st] || st);

// ------------------------------------------------------------ іконки (статичні SVG, у стилі Lucide)
const ICONS = {
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 15v2M11 11v6M15 7v10M19 12v5"/>',
  wallet: '<path d="M20 7H5a2 2 0 0 1 0-4h13v4"/><path d="M3 5v14a2 2 0 0 0 2 2h15v-5"/><path d="M21 12h-4a2 2 0 0 0 0 4h4v-4z"/>',
  check: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  users: '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="M16 4a4 4 0 0 1 0 8M22 21a7 7 0 0 0-4-6.3"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  receipt: '<path d="M4 2v20l3-2 3 2 3-2 3 2 3-2 1 .7V2l-3 2-3-2-3 2-3-2-3 2z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  banknote: '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/>',
  scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  left: '<path d="M15 18l-6-6 6-6"/>',
  right: '<path d="M9 18l6-6-6-6"/>',
  phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.8.6 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.5 2.7.6a2 2 0 0 1 1.7 2z"/>',
  x: '<path d="M18 6L6 18M6 6l12 12"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  trend: '<path d="M23 6l-9.5 9.5-5-5L1 18"/><path d="M17 6h6v6"/>',
  card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
  transfer: '<path d="M17 3l4 4-4 4M3 7h18M7 21l-4-4 4-4M21 17H3"/>',
  send: '<path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/>',
  box: '<path d="M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4a2 2 0 0 0 1-1.7z"/><path d="M3.3 7L12 12l8.7-5M12 22V12"/>',
  percent: '<path d="M19 5L5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
  home: '<path d="M3 21h18M5 21V7l7-4 7 4v14M9 21v-6h6v6"/>',
  megaphone: '<path d="M3 11l18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/>',
  zap: '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>',
  ok: '<path d="M20 6L9 17l-5-5"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  history: '<path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l4 2"/>',
  cancel: '<circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/>',
  noshow: '<circle cx="10" cy="8" r="4"/><path d="M3 21a7 7 0 0 1 12.5-4.3M17 14l5 5M22 14l-5 5"/>',
  play: '<path d="M6 4l14 8-14 8z"/>',
  move: '<path d="M5 9l-3 3 3 3M9 5l3-3 3 3M15 19l-3 3-3-3M19 9l3 3-3 3M2 12h20M12 2v20"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  bug: '<rect x="8" y="6" width="8" height="14" rx="4"/><path d="M19 7l-3 2M5 7l3 2M19 19l-3-2M5 19l3-2M20 13h-4M4 13h4M10 4l1 2M14 4l-1 2"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="M21 15l-5-5L5 21"/>',
  refresh: '<path d="M21 12a9 9 0 0 1-15.4 6.4L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 15.4-6.4L21 8"/><path d="M21 3v5h-5"/>',
  calclock: '<path d="M21 10.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h6"/><path d="M16 2v4M8 2v4M3 10h18"/><circle cx="17.5" cy="17.5" r="4.5"/><path d="M17.5 15.5v2l1.3 1"/>',
  filetext: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h8M8 9h2"/>',
  landmark: '<path d="M3 22h18M6 18v-7M10 18v-7M14 18v-7M18 18v-7M12 2l9 5H3z"/>',
  usercog: '<circle cx="9" cy="7" r="4"/><path d="M2 21a7 7 0 0 1 10.5-6"/><circle cx="18" cy="17" r="2.5"/><path d="M18 13v1.5M18 19.5V21M14 17h1.5M20.5 17H22M15.2 14.2l1 1M19.8 18.8l1 1M15.2 19.8l1-1M19.8 15.2l1-1"/>',
  coins: '<circle cx="8" cy="8" r="6"/><path d="M18.1 10.4A6 6 0 1 1 10.3 18M7 6h1v4M16.7 13.9l.7.7-2.8 2.8"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  rows: '<rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  eyeoff: '<path d="M17.9 17.9A10.4 10.4 0 0 1 12 19c-6.5 0-10-7-10-7a18.4 18.4 0 0 1 5.1-5.9M9.9 5.2A9.6 9.6 0 0 1 12 5c6.5 0 10 7 10 7a18.5 18.5 0 0 1-2.2 3.2M14.1 14.1a3 3 0 1 1-4.2-4.2M2 2l20 20"/>',
  gauge: '<path d="M12 14l4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0"/>',
};
function icon(name, cls = "") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", "i " + cls);
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || ""; // лише статичні рядки з ICONS — без даних користувача
  return s;
}

const AV_COLORS = ["#1d4d3b", "#8a6a2f", "#3f5a7a", "#7a4a5c", "#4f6b3a", "#5b4a7a", "#2f6b6b", "#7a5a3a"];
function initials(name) { return (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join(""); }
function avatar(name, cls = "") {
  let hsh = 0;
  for (const c of name || "") hsh = (hsh * 31 + c.charCodeAt(0)) >>> 0;
  return h("span", { class: "avatar " + cls, style: { background: AV_COLORS[hsh % AV_COLORS.length] } }, initials(name));
}

// ------------------------------------------------------------ тема
function applyTheme(t) {
  if (t === "dark") document.documentElement.setAttribute("data-theme", "dark");
  else document.documentElement.removeAttribute("data-theme");
}
function toggleTheme() {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem("mds_theme", next); } catch (_) {}
  renderApp();
}
try { applyTheme(localStorage.getItem("mds_theme") || "light"); } catch (_) { applyTheme("light"); }

function toast(msg, err = false) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const t = h("div", { class: "toast" + (err ? " err" : ""), role: "status" }, icon(err ? "alert" : "ok", "sm"), msg);
  document.body.append(t);
  setTimeout(() => t.remove(), err ? 4500 : 2600);
}

async function run(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg);
    return r;
  } catch (e) {
    toast(e.message, true);
    throw e;
  }
}

function field(label, input) { return h("label", { class: "f" }, h("span", {}, label), input); }

function select(options, value, attrs = {}) {
  return h("select", attrs, options.map(([v, t]) => h("option", { value: v, selected: String(v) === String(value) }, t)));
}

function seg(options, value, onChange) {
  const wrap = h("div", { class: "seg", role: "tablist" });
  for (const [v, t] of options) {
    wrap.append(h("button", {
      class: v === value ? "on" : "", type: "button",
      onclick: () => { [...wrap.children].forEach((b) => b.classList.remove("on")); wrap.children[options.findIndex((o) => o[0] === v)].classList.add("on"); onChange(v); },
    }, t));
  }
  return wrap;
}

function deltaEl(cur, prev) {
  if (prev === undefined || prev === null || !prev) return null; // за попередній період даних немає
  const p = Math.round(((cur - prev) / Math.abs(prev)) * 100);
  const cls = p > 0 ? "up" : p < 0 ? "down" : "flat";
  return h("span", { class: "delta " + cls, title: "Порівняно з попереднім періодом: " + money(prev) }, (p > 0 ? "▲ " : p < 0 ? "▼ " : "") + Math.abs(p) + "%");
}

function kpi(label, value, cls = "", { ico = null, prev, vs = null, sub = null, raw = false, onclick = null } = {}) {
  // vs — з чим порівнюємо («мин. місяць»): показується лише коли є дані за попередній період
  // onclick — плитка відкриває подробиці (стрілка справа)
  const d = deltaEl(value, prev);
  return h(onclick ? "button" : "div", { class: "card kpi-tile " + cls + (onclick ? " kpi-link" : ""), onclick, type: onclick ? "button" : null },
    h("div", { class: "kpi-top" }, ico ? h("span", { class: "kpi-ico" }, icon(ico, "sm")) : null, h("span", { class: "kpi-label" }, label),
      onclick ? h("span", { class: "kpi-more" }, icon("right", "sm")) : null),
    h("div", { class: "kpi-value" + (value < 0 ? " neg" : "") }, raw ? value : money(value)),
    d || sub ? h("div", { class: "row small muted" }, d, d && vs ? vs : null, sub) : null);
}

// смуги «частка від цілого» для подробиць: rows = [[назва, сума, підпис?, href?]]
function barList(rows, total = null) {
  const max = total || Math.max(1, ...rows.map((r) => r[1]));
  return h("div", { class: "bars" }, rows.map(([label, v, sub, href], i) => {
    const w = Math.max(0, Math.min(100, (v / max) * 100));
    const bar = h("i", { style: { width: "0%" } });
    requestAnimationFrame(() => setTimeout(() => { bar.style.width = w + "%"; }, 30 + i * 40));
    return h(href ? "a" : "div", { class: "bar-row", href: href || null },
      h("div", { class: "bar-head" }, h("span", {}, label), h("b", {}, money(v)), sub ? h("small", { class: "muted" }, sub) : null),
      h("div", { class: "bar-track" }, bar));
  }));
}
const pctOf = (v, total) => (total ? `${Math.round((v / total) * 1000) / 10}%`.replace(".", ",") : "");

function emptyState(text, ico = "inbox") { return h("div", { class: "empty" }, icon(ico), h("div", {}, text)); }

// На телефоні таблиці показуються картками: кожна клітинка отримує підпис зі свого заголовка
function labelTables(root) {
  root.querySelectorAll("table:not([data-labeled])").forEach((t) => {
    const ths = [...(t.querySelector("tr")?.querySelectorAll("th") || [])];
    const heads = ths.map((th) => (th.innerText || th.textContent).replace(/\s+/g, " ").trim());
    const hide = ths.map((th) => th.classList.contains("m-hide"));
    if (!heads.length) return;
    t.setAttribute("data-labeled", "1");
    t.classList.add("resp");
    t.querySelectorAll("tr").forEach((tr) => {
      let i = 0;
      tr.querySelectorAll("td").forEach((td) => {
        const span = Number(td.getAttribute("colspan") || 1);
        if (heads[i] && span === 1) td.setAttribute("data-label", heads[i]);
        if (hide[i] && span === 1) td.classList.add("m-hide");
        i += span;
      });
    });
  });
}
new MutationObserver(() => labelTables(document)).observe(document.documentElement, { childList: true, subtree: true });

// ------------------------------------------------------------ баг-репорт
// Останні помилки програми — додаються до звіту, щоб було легше знайти причину
const recentErrors = [];
const pushErr = (msg) => { recentErrors.push(`${new Date().toLocaleTimeString("uk-UA")} ${String(msg).slice(0, 300)}`); if (recentErrors.length > 5) recentErrors.shift(); };
window.addEventListener("error", (e) => pushErr(e.message || "Помилка"));
window.addEventListener("unhandledrejection", (e) => pushErr(e.reason?.message || e.reason || "Помилка"));

function readImage(file) {
  // зменшуємо знімок до 1600 px, щоб швидко відправлявся
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, 1600 / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(img.src);
      resolve(c.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => reject(new Error("Не вдалося відкрити зображення"));
    img.src = URL.createObjectURL(file);
  });
}

function bugReportForm() {
  // назва сторінки так, як її бачить користувач («Календар», «Склад»…)
  const page = document.querySelector(".main h1")?.textContent?.trim() || (location.hash.replace(/^#\/?/, "") || "головна").split("?")[0];
  const text = h("textarea", { rows: 5, id: "bug-text", placeholder: "Що сталося? Що ви робили перед цим? Наприклад: «Натиснула «Закрити замовлення» — нічого не відбувається»" });
  let image = null;
  const preview = h("div", { class: "row" });
  const file = h("input", { type: "file", accept: "image/*", style: { display: "none" }, onchange: async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      image = await readImage(f);
      preview.replaceChildren(h("img", { src: image, alt: "Знімок екрана", style: { maxHeight: "120px", borderRadius: "8px", border: "1px solid var(--line)" } }),
        h("button", { type: "button", class: "btn sm ghost", onclick: () => { image = null; file.value = ""; preview.replaceChildren(); } }, icon("x", "sm"), "Прибрати"));
    } catch (err) { toast(err.message, true); }
  } });
  const send = h("button", { class: "btn primary big", onclick: async () => {
    if (text.value.trim().length < 3) return toast("Опишіть, будь ласка, що сталося", true);
    send.disabled = true;
    try {
      const r = await run(() => api.post("/api/bug-report", {
        text: text.value.trim(), page, image,
        context: { device: navigator.userAgent, screen: `${window.innerWidth}×${window.innerHeight}`, version: `web ${state.version || "?"}`, errors: recentErrors },
      }));
      closeSheet();
      toast(r.delivered ? "Дякуємо! Повідомлення надіслано директору в Telegram" : "Збережено. Директор ще не підключив Telegram — повідомлення буде в журналі");
    } catch (_) { send.disabled = false; }
  } }, icon("send"), "Надіслати директору");
  openSheet("Повідомити про помилку", h("div", { class: "stack" },
    h("p", { class: "muted", style: { margin: 0 } }, "Звіт прийде директору в Telegram разом зі сторінкою, пристроєм і технічними даними."),
    field("Опис", text),
    h("button", { type: "button", class: "btn", onclick: () => file.click() }, icon("image", "sm"), "Додати знімок екрана"), file, preview,
    send));
  autoFocus(text);
}

// ============================================================ бічна панель
// На телефонах (сенсорний екран) не відкриваємо клавіатуру автоматично — вона закриває вікно
const isTouch = () => window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
function autoFocus(el, select = false) {
  if (isTouch()) return;
  select ? el.select() : el.focus();
}

// Вікно над клавіатурою: на iPhone клавіатура зменшує видиму область (visualViewport),
// тож підганяємо підкладку під видиму частину екрана, а поле в фокусі прокручуємо в поле зору
function fitToKeyboard(ov) {
  const vv = window.visualViewport;
  if (!vv) return () => {};
  const fit = () => {
    ov.style.top = vv.offsetTop + "px";
    ov.style.height = vv.height + "px";
    ov.style.bottom = "auto";
  };
  const onFocus = (e) => {
    if (e.target.matches("input, textarea, select")) setTimeout(() => e.target.scrollIntoView({ block: "center", behavior: "smooth" }), 250);
  };
  vv.addEventListener("resize", fit);
  vv.addEventListener("scroll", fit);
  ov.addEventListener("focusin", onFocus);
  fit();
  return () => { vv.removeEventListener("resize", fit); vv.removeEventListener("scroll", fit); };
}
let unfitSheet = () => {};

function openSheet(title, ...content) {
  closeSheet();
  const ov = h("div", { class: "overlay", id: "overlay", onclick: (e) => { if (e.target === ov) closeSheet(); } },
    h("div", { class: "sheet", role: "dialog", "aria-modal": "true" },
      h("div", { class: "sheet-head" }, h("h2", {}, title), h("button", { class: "x", onclick: closeSheet, "aria-label": "Закрити" }, icon("x"))),
      ...content));
  document.body.append(ov);
  unfitSheet = fitToKeyboard(ov);
  return ov;
}
function closeSheet() { unfitSheet(); unfitSheet = () => {}; const o = document.getElementById("overlay"); if (o) o.remove(); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
// Масштабування сторінки вимкнено: програма має виглядати як застосунок
["gesturestart", "gesturechange", "gestureend"].forEach((ev) => document.addEventListener(ev, (e) => e.preventDefault(), { passive: false }));
document.addEventListener("touchmove", (e) => { if (e.touches && e.touches.length > 1) e.preventDefault(); }, { passive: false });
document.addEventListener("wheel", (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });
let lastTouchEnd = 0;
document.addEventListener("touchend", (e) => { const n = Date.now(); if (n - lastTouchEnd < 300 && !e.target.closest("input, textarea, select")) e.preventDefault(); lastTouchEnd = n; }, { passive: false });

function confirmSheet(title, text, okText, onOk, { danger = false, withReason = false } = {}) {
  const reason = h("input", { placeholder: "Причина" });
  openSheet(title,
    h("p", {}, text),
    withReason ? field("Причина", reason) : null,
    h("div", { class: "row", style: { marginTop: "16px" } },
      h("button", { class: "btn grow", onclick: closeSheet }, "Назад"),
      h("button", { class: "btn grow " + (danger ? "danger" : "primary"), onclick: async (e) => {
        e.currentTarget.disabled = true;
        const b = e.currentTarget;
        try { await onOk(reason.value.trim()); closeSheet(); } catch (_) { b.disabled = false; }
      } }, okText)));
}

// ============================================================ каркас
// [id, іконка, назва, група]
const NAV = {
  master: [
    ["today", "clock", "Сьогодні"], ["calendar", "calendar", "Календар"], ["earnings", "wallet", "Заробіток"],
    ["shift", "check", "Зміна"], ["profile", "user", "Профіль"],
  ],
  admin: [["calendar", "calendar", "Календар", "Робота"], ["schedule", "calclock", "Графік роботи", "Робота"], ["clients", "users", "Клієнти", "Робота"],
    ["stock", "box", "Склад", "Робота"], ["generator", "gauge", "Генератор", "Робота"], ["profile", "user", "Профіль"]],
  director: [
    ["dashboard", "chart", "Огляд", "Огляд"],
    ["calendar", "calendar", "Календар", "Робота"], ["schedule", "calclock", "Графік роботи", "Робота"], ["clients", "users", "Клієнти", "Робота"],
    ["stock", "box", "Склад", "Робота"],
    ["orders", "receipt", "Замовлення", "Фінанси"], ["shifts", "coins", "Зміни й виплати", "Фінанси"], ["expenses", "wallet", "Витрати", "Фінанси"], ["utilities", "gauge", "Комунальні і генератор", "Фінанси"], ["taxreport", "filetext", "Звіт для податкової", "Фінанси"], ["taxes", "landmark", "Податки і зарплата", "Фінанси"],
    ["staff", "usercog", "Персонал", "Салон"], ["catalog", "scissors", "Послуги", "Салон"],
    ["settings", "settings", "Налаштування", "Салон"], ["journal", "book", "Журнал", "Салон"],
    ["profile", "user", "Профіль"],
  ],
};
const MOBILE_TABS = { director: ["dashboard", "calendar", "orders", "stock", "more"], admin: ["calendar", "schedule", "stock", "clients", "more"],
  master: ["today", "calendar", "earnings", "shift", "more"] };

function currentRoute() { return (location.hash.replace(/^#\/?/, "") || "").split("?")[0]; }

function logout() {
  api.token = null;
  store.del("mds_token");
  state.user = null;
  renderLogin();
}

// Версія системи — з сервера (/api/health), тож завжди та, що реально розгорнута
function versionTag(cls = "version") {
  return h("span", { class: cls, "data-version": "1" }, state.version ? `Версія ${state.version}` : "");
}
function loadVersion() {
  if (state.version) return;
  api.get("/api/health").then((r) => {
    state.version = r.version;
    document.querySelectorAll("[data-version]").forEach((e) => { e.textContent = `Версія ${r.version}`; });
  }).catch(() => {});
}

async function boot() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  loadVersion();
  if (!api.token) return renderLogin();
  try {
    state.user = await api.get("/api/auth/me");
  } catch (_) {
    return renderLogin();
  }
  await loadRefs();
  window.onhashchange = renderApp;
  renderApp();
}

async function loadRefs() {
  const [services, masters, materials, settings] = await Promise.all([
    api.get("/api/services?all=true"), api.get("/api/users"), api.get("/api/materials"), api.get("/api/settings"),
  ]);
  state.services = services;
  state.masters = masters.filter((u) => u.role === "master" && u.is_active !== false);
  state.allUsers = masters;
  state.materials = materials;
  state.settings = settings;
}

function setThemeColor(c) {
  const m = document.querySelector('meta[name="theme-color"]');
  if (m) m.setAttribute("content", c);
}

function renderLogin() {
  window.onhashchange = null;
  setThemeColor(document.documentElement.getAttribute("data-theme") === "dark" ? "#0b1a15" : "#f4f3ee"); // як основна сторінка
  const root = document.getElementById("root");
  const lastUser = store.get("mds_last_user") || "";
  const user = h("input", { autocomplete: "username", autocapitalize: "none", spellcheck: "false", placeholder: "Ваш логін", id: "login-user", value: lastUser });
  const pass = h("input", { type: "password", autocomplete: "current-password", placeholder: "Пароль", id: "login-pass" });
  const eye = h("button", { type: "button", class: "pw-eye", "aria-label": "Показати пароль", onclick: () => {
    const show = pass.type === "password"; pass.type = show ? "text" : "password";
    eye.replaceChildren(icon(show ? "eyeoff" : "eye")); eye.setAttribute("aria-label", show ? "Сховати пароль" : "Показати пароль"); pass.focus();
  } }, icon("eye"));
  const caps = h("div", { class: "login-hint hidden" }, icon("alert", "sm"), "Увімкнено Caps Lock");
  pass.addEventListener("keyup", (e) => caps.classList.toggle("hidden", !(e.getModifierState && e.getModifierState("CapsLock"))));
  const err = h("div", { class: "login-err hidden", role: "alert" });
  const showErr = (m) => { err.textContent = m; err.classList.remove("hidden"); card.classList.remove("shake"); void card.offsetWidth; card.classList.add("shake"); };
  const btn = h("button", { class: "btn login-btn big", type: "submit" }, h("span", {}, "Увійти"), icon("right", "sm"));
  const busy = (b, on) => { b.disabled = on; b.classList.toggle("loading", on); };
  const done = async (r) => {
    api.token = r.token;
    store.set("mds_token", r.token);
    if (r.device_token) store.set("mds_device", r.device_token);
    store.set("mds_last_user", user.value.trim());
    location.hash = "";
    await boot();
  };
  const submitPassword = async () => {
    err.classList.add("hidden");
    if (!user.value.trim() || !pass.value) return showErr("Введіть логін і пароль");
    busy(btn, true);
    try {
      const r = await api.post("/api/auth/login", { username: user.value.trim(), password: pass.value, device_token: store.get("mds_device") || null });
      if (r.twofa) return codeStep(r);
      await done(r);
    } catch (e) {
      showErr(e.message);
    } finally {
      busy(btn, false);
    }
  };
  const form = h("form", { class: "stack login-form", onsubmit: (e) => { e.preventDefault(); submitPassword(); } },
    field("Логін", user), field("Пароль", h("div", { class: "pw-wrap" }, pass, eye)), caps, err, btn);

  // другий крок: 6-значний код з Telegram
  function codeStep(r) {
    const boxes = Array.from({ length: 6 }, (_, i) => h("input", { class: "code-box", inputmode: "numeric", maxlength: 1, "aria-label": `Цифра ${i + 1}`,
      autocomplete: i === 0 ? "one-time-code" : "off" }));
    const remember = h("input", { type: "checkbox", checked: true });
    const cerr = h("div", { class: "login-err hidden", role: "alert" });
    const vbtn = h("button", { class: "btn login-btn big", type: "submit" }, h("span", {}, "Підтвердити"), icon("ok", "sm"));
    const timer = h("span", {});
    let left = r.expires_in || 300;
    const tick = () => { timer.textContent = left > 0 ? `Код дійсний ще ${Math.floor(left / 60)}:${pad(left % 60)}` : "Код застарів — надішліть новий"; };
    tick();
    const iv = setInterval(() => { left -= 1; tick(); if (left <= 0 || !timer.isConnected) clearInterval(iv); }, 1000);
    const value = () => boxes.map((b) => b.value).join("");
    const verify = async () => {
      cerr.classList.add("hidden");
      if (value().length !== 6) { cerr.textContent = "Введіть усі 6 цифр"; cerr.classList.remove("hidden"); return; }
      busy(vbtn, true);
      try {
        const ua = navigator.userAgent;
        const label = /MDSAndroid|Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iPhone / iPad" : /Windows/.test(ua) ? "Windows" : /Mac/.test(ua) ? "Mac" : "Браузер";
        const res = await api.post("/api/auth/login/verify", { challenge_id: r.challenge_id, code: value(), remember: remember.checked, device_label: label });
        clearInterval(iv);
        await done(res);
      } catch (e) {
        cerr.textContent = e.message; cerr.classList.remove("hidden");
        card.classList.remove("shake"); void card.offsetWidth; card.classList.add("shake");
        boxes.forEach((b) => { b.value = ""; b.classList.remove("filled"); }); boxes[0].focus();
      } finally {
        busy(vbtn, false);
      }
    };
    boxes.forEach((b, i) => {
      b.addEventListener("input", () => {
        const digits = b.value.replace(/\D/g, "");
        if (digits.length > 1) { // вставили весь код або автозаповнення з SMS/Telegram
          digits.slice(0, 6 - i).split("").forEach((d, k) => { boxes[i + k].value = d; boxes[i + k].classList.add("filled"); });
          boxes[Math.min(5, i + digits.length - 1)].focus();
        } else {
          b.value = digits; b.classList.toggle("filled", !!digits);
          if (digits && i < 5) boxes[i + 1].focus();
        }
        if (value().length === 6) verify();
      });
      b.addEventListener("keydown", (e) => {
        if (e.key === "Backspace" && !b.value && i > 0) { boxes[i - 1].focus(); boxes[i - 1].value = ""; boxes[i - 1].classList.remove("filled"); }
        if (e.key === "ArrowLeft" && i > 0) boxes[i - 1].focus();
        if (e.key === "ArrowRight" && i < 5) boxes[i + 1].focus();
      });
      b.addEventListener("focus", () => b.select());
    });
    const resend = h("button", { type: "button", class: "link-btn", onclick: async (e) => {
      busy(e.currentTarget, true);
      try {
        const n = await api.post("/api/auth/login", { username: user.value.trim(), password: pass.value });
        if (n.twofa) { clearInterval(iv); codeStep(n); toast("Новий код надіслано в Telegram"); } else await done(n);
      } catch (x) { toast(x.message, true); } finally { busy(e.currentTarget, false); }
    } }, "Надіслати код ще раз");
    body.replaceChildren(h("form", { class: "stack login-form step-in", onsubmit: (e) => { e.preventDefault(); verify(); } },
      h("div", { class: "code-head" }, h("span", { class: "code-ico" }, icon("lock")),
        h("div", {}, h("b", {}, "Підтвердження входу"), h("div", {}, "Ми надіслали 6-значний код у Telegram-бот салону."))),
      h("div", { class: "code-row" }, boxes), cerr,
      h("label", { class: "row remember" }, remember, "Запам'ятати цей пристрій на 30 днів"),
      vbtn,
      h("div", { class: "code-foot" }, timer, resend),
      h("button", { type: "button", class: "link-btn back", onclick: () => { clearInterval(iv); body.replaceChildren(form); pass.value = ""; autoFocus(pass); } }, icon("left", "sm"), "Інший акаунт")));
    boxes[0].focus();
  }

  const body = h("div", { class: "login-body" }, form);
  const card = h("div", { class: "login-card" },
    h("div", { class: "login-mark" }, "M&D"),
    h("div", { class: "login-name" }, "MDS Connect"),
    h("div", { class: "login-sub" }, state.settings?.salon_name ? "Салон " + state.settings.salon_name : "Салон краси M&D"),
    body);
  root.replaceChildren(
    h("div", { class: "login" },
      h("div", { class: "login-glow" }),
      card,
      h("div", { class: "login-foot" }, icon("lock", "sm"), "Внутрішня система салону · доступ лише для персоналу", h("br"), versionTag())));
  autoFocus(lastUser ? pass : user);
}

function renderApp() {
  setThemeColor(document.documentElement.getAttribute("data-theme") === "dark" ? "#0b1a15" : "#f4f3ee");
  const role = state.user.role;
  const items = NAV[role];
  let route = currentRoute();
  if (route === "more") route = "more";
  else if (!items.find((i) => i[0] === route)) route = items[0][0];

  const navLinks = [];
  let group = null;
  for (const [id, ico, label, grp] of items.filter((i) => i[0] !== "profile")) {
    if (grp && grp !== group) { navLinks.push(h("div", { class: "nav-group" }, grp)); group = grp; }
    navLinks.push(h("a", { href: "#/" + id, class: id === route ? "active" : "" }, icon(ico), label));
  }
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  const sidebar = h("aside", { class: "sidebar" },
    h("div", { class: "brand" }, h("div", { class: "brand-mark" }, "M&D"),
      h("div", {}, h("div", { class: "brand-name" }, "MDS Connect"), h("div", { class: "brand-sub" }, state.settings?.salon_name ? "Салон " + state.settings.salon_name : "Салон M&D"),
        versionTag("brand-ver"))),
    h("nav", { class: "nav" }, navLinks),
    h("div", { class: "side-foot" },
      h("a", { href: "#/profile", style: { display: "contents" } }, avatar(state.user.full_name),
        h("div", { class: "who" }, h("b", {}, state.user.full_name), h("span", {}, ROLES[role]))),
      h("button", { class: "icon-btn", title: "Повідомити про помилку", "aria-label": "Повідомити про помилку", onclick: bugReportForm }, icon("bug")),
      h("button", { class: "icon-btn", title: dark ? "Світла тема" : "Темна тема", "aria-label": "Змінити тему", onclick: toggleTheme }, icon(dark ? "sun" : "moon")),
      h("button", { class: "icon-btn", title: "Вийти", "aria-label": "Вийти", onclick: logout }, icon("logout"))));

  const tabIds = MOBILE_TABS[role] || items.map((i) => i[0]);
  const tabbar = h("nav", { class: "tabbar" }, tabIds.map((id) => {
    const it = items.find((i) => i[0] === id) || ["more", "more", "Ще"];
    const active = id === route || (id === "more" && !tabIds.includes(route));
    return h("a", { href: "#/" + id, class: active ? "active" : "" }, icon(it[1]), it[2]);
  }));
  const main = h("main", { class: "main" });
  // телефон: закріплена шапка з суцільним фоном — вміст прокручується під нею, а не під годинником iPhone
  const title = route === "more" ? "Меню" : (items.find((i) => i[0] === route) || [])[2] || "";
  // сторінки поза нижніми вкладками (Послуги, Персонал, Журнал…) — кнопка «Назад» замість логотипа
  const isTab = route === "more" || tabIds.includes(route);
  const backTo = tabIds.includes("more") ? "more" : tabIds[0];
  const lead = isTab ? h("div", { class: "brand-mark" }, "M&D")
    : h("button", { class: "icon-btn back-btn", "aria-label": "Назад", onclick: () => { location.hash = "#/" + backTo; } }, icon("left"), h("span", {}, "Назад"));
  const mbar = h("header", { class: "mbar" }, lead, h("b", { class: "grow" }, title),
    h("button", { class: "icon-btn", "aria-label": "Оновити сторінку", onclick: (e) => refreshView(e.currentTarget) }, icon("refresh")),
    h("button", { class: "icon-btn", "aria-label": "Повідомити про помилку", onclick: bugReportForm }, icon("bug")));
  main.append(mbar);
  document.getElementById("root").replaceChildren(h("div", { class: "app" }, sidebar, main), tabbar);
  closeSheet();
  const view = route === "more" ? viewMore : VIEWS[route];
  return Promise.resolve(view(main)).catch((e) => { main.replaceChildren(emptyState(e.message, "alert")); });
}

// «Оновити»: свіжі довідники (майстри, послуги, налаштування) і перемальована сторінка без перезавантаження застосунку
let refreshing = false;
async function refreshView(btn) {
  if (refreshing || !state.user) return;
  refreshing = true;
  btn?.classList.add("spin");
  const y = window.scrollY;
  try {
    await loadRefs();
    await renderApp();
    window.scrollTo(0, y);
    toast("Оновлено");
  } catch (e) {
    toast(e.message || "Не вдалося оновити", true);
  } finally {
    refreshing = false;
    btn?.classList.remove("spin");
  }
}

// Потягнути сторінку вниз, щоб оновити (телефон / планшет)
(function pullToRefresh() {
  const TH = 72;
  let y0 = null, dy = 0, ind = null;
  const reset = () => { ind?.remove(); ind = null; y0 = null; dy = 0; };
  window.addEventListener("touchstart", (e) => {
    y0 = (state.user && window.scrollY <= 0 && !document.querySelector(".overlay") && e.touches.length === 1) ? e.touches[0].clientY : null;
    dy = 0;
  }, { passive: true });
  window.addEventListener("touchmove", (e) => {
    if (y0 === null) return;
    dy = e.touches[0].clientY - y0;
    if (dy <= 4) { ind?.remove(); ind = null; return; }
    if (!ind) { ind = h("div", { class: "ptr" }, icon("refresh")); document.body.append(ind); }
    const p = Math.min(dy, 130);
    ind.style.transform = `translate(-50%, ${p * 0.55}px) rotate(${p * 2.4}deg)`;
    ind.style.opacity = String(Math.min(1, p / TH));
    ind.classList.toggle("ready", dy > TH);
  }, { passive: true });
  window.addEventListener("touchend", () => {
    if (ind && dy > TH) { const el = ind; ind = null; el.classList.add("spin"); refreshView().finally(() => el.remove()); y0 = null; dy = 0; }
    else reset();
  });
  window.addEventListener("touchcancel", reset);
})();

function topbar(title, ...actions) {
  let sub = null;
  if (actions.length && typeof actions[0] === "string") sub = actions.shift();
  return h("div", { class: "topbar" },
    h("div", {}, h("h1", {}, title), sub ? h("div", { class: "sub" }, sub) : null),
    h("div", { class: "row" }, actions,
      h("button", { class: "icon-btn refresh-desk", title: "Оновити сторінку", "aria-label": "Оновити сторінку", onclick: (e) => refreshView(e.currentTarget) }, icon("refresh"))));
}

function viewMore(main) {
  main.append(topbar("Меню"));
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  const tabs = MOBILE_TABS[state.user.role] || [];
  main.append(h("div", { class: "card", style: { padding: "6px" } }, NAV[state.user.role].filter(([id]) => !tabs.includes(id)).map(([id, ico, label]) =>
    h("a", { href: "#/" + id, class: "row", style: { padding: "12px", color: "var(--ink)" } }, icon(ico), h("span", { class: "grow" }, label), icon("right", "sm"))),
    h("button", { class: "btn ghost", style: { width: "100%", justifyContent: "flex-start", padding: "12px" }, onclick: bugReportForm }, icon("bug"), "Повідомити про помилку"),
    h("button", { class: "btn ghost", style: { width: "100%", justifyContent: "flex-start", padding: "12px" }, onclick: toggleTheme }, icon(dark ? "sun" : "moon"), dark ? "Світла тема" : "Темна тема"),
    h("button", { class: "btn ghost", style: { width: "100%", justifyContent: "flex-start", padding: "12px", color: "var(--danger)" }, onclick: logout }, icon("logout"), "Вийти")),
    h("p", { class: "muted small", style: { textAlign: "center", marginTop: "14px" } }, "MDS Connect · ", versionTag()));
}

const reload = () => renderApp();

// ============================================================ спільні форми
function apptSheet(a, onChange) {
  const canEdit = ["pending", "confirmed", "in_progress"].includes(a.status);
  const done = async (p, msg) => { await run(p, msg); closeSheet(); onChange(); };
  const secondary = h("div", { class: "grid", style: { gridTemplateColumns: "1fr 1fr", gap: "8px" } });
  if (canEdit) {
    if (a.status === "pending") {
      secondary.append(h("button", { class: "btn", onclick: () => done(() => api.post(`/api/appointments/${a.id}/status`, { status: "confirmed" }), "Підтверджено") }, icon("ok", "sm"), "Підтвердити"));
      secondary.append(h("button", { class: "btn", onclick: () => proposeForm(a, onChange) }, icon("clock", "sm"), "Інший час"));
      secondary.append(h("button", { class: "btn danger", onclick: () => declineBusy(a, onChange) }, icon("cancel", "sm"), "Зайнято"));
    }
    if (a.status !== "in_progress") {
      secondary.append(h("button", { class: "btn", onclick: () => done(() => api.post(`/api/appointments/${a.id}/status`, { status: "in_progress" }), "Клієнта прийнято") }, icon("play", "sm"), "Клієнт прийшов"));
    }
    secondary.append(h("button", { class: "btn", onclick: () => apptForm({ appt: a }, onChange) }, icon("move", "sm"), "Перенести"));
    if (a.status !== "in_progress") {
      secondary.append(h("button", { class: "btn", onclick: () => confirmSheet("Клієнт не прийшов?", `${a.client.name}, ${hm(a.starts_at)}`, "Так, не прийшов",
        async () => { await run(() => api.post(`/api/appointments/${a.id}/status`, { status: "no_show" })); onChange(); }) }, icon("noshow", "sm"), "Не прийшов"));
    }
    secondary.append(h("button", { class: "btn danger", onclick: () => confirmSheet("Скасувати запис?", `${a.client.name}, ${dm(a.starts_at)} ${hm(a.starts_at)}`, "Скасувати запис",
      async (reason) => { await run(() => api.post(`/api/appointments/${a.id}/status`, { status: "cancelled", reason: reason || null }), "Запис скасовано"); onChange(); },
      { danger: true, withReason: true }) }, icon("cancel", "sm"), "Скасувати"));
  }
  openSheet("Запис",
    h("div", { class: "stack" },
      h("div", { class: "row" }, h("div", { class: "grow" },
        h("div", { style: { fontSize: "26px", fontWeight: 700, letterSpacing: "-.02em" } }, `${hm(a.starts_at)}–${hm(a.ends_at)}`),
        h("div", { class: "muted" }, `${weekday(a.starts_at.slice(0, 10))}, ${dmy(a.starts_at)} · ${a.duration_min} хв`)), chip(a.status)),
      h("div", { class: "client-box" }, avatar(a.client.name, "lg"),
        h("div", { class: "grow" }, h("div", { style: { fontWeight: 700, fontSize: "15px" } }, a.client.name),
          h("a", { href: "tel:" + a.client.phone, class: "row small", style: { gap: "5px" } }, icon("phone", "sm"), a.client.phone)),
        h("button", { class: "btn sm", onclick: () => clientSheet(a.client.id) }, icon("history", "sm"), "Історія")),
      h("div", { class: "info-list" },
        h("div", { class: "info-row" }, icon("scissors"), h("b", {}, a.service_name)),
        h("div", { class: "info-row" }, icon("user"), "Майстер: ", h("b", {}, a.master_name)),
        h("div", { class: "info-row" }, icon("banknote"), "Ціна: ", h("b", {}, money(a.price))),
        a.source === "telegram" ? h("div", { class: "info-row" }, icon("send"), "Запис через Telegram-бот") : null,
        a.comment ? h("div", { class: "info-row" }, icon("edit"), a.comment) : null,
        a.cancel_reason ? h("div", { class: "info-row" }, icon("cancel"), "Причина: ", a.cancel_reason) : null,
        a.order_id ? h("div", { class: "info-row" }, icon("receipt"), "Замовлення #", a.order_id) : null),
      canEdit ? h("button", { class: "btn primary big", onclick: () => closeOrderSheet(a, onChange) }, icon("banknote"), "Закрити замовлення") : null,
      canEdit ? secondary : null));
}

// Вибір використаних матеріалів: пошук за назвою/тоном, кількість упаковками або г/мл, залишок на складі.
// selected: Map(material_id → кількість у базових одиницях)
function materialsPicker(selected, custom, { open = false } = {}) {
  const all = state.materials.filter((m) => m.is_active !== false);
  const q = h("input", { type: "search", placeholder: "Пошук: назва або тон, напр. 7-1", id: "mat-search" });
  const chosen = h("div", { class: "stack", style: { marginTop: "8px" } });
  const results = h("div");
  const total = h("span", { class: "chip plain" });
  const draw = () => {
    let sum = 0;
    chosen.replaceChildren(...[...[...selected.entries()].map(([id, qty]) => {
      const m = all.find((x) => x.id === id);
      if (!m) return null;
      sum += matCost(m, qty);
      const inp = h("input", { type: "number", min: 0, step: "any", value: qty, style: { width: "92px", minHeight: "34px" },
        onchange: (e) => { const v = parseFloat(e.target.value); v > 0 ? selected.set(id, v) : selected.delete(id); draw(); } });
      const quick = m.pack_size > 1
        ? [["¼", m.pack_size / 4], ["½", m.pack_size / 2], [`1 ${m.pack_name}`, m.pack_size]].map(([l, v]) =>
          h("button", { type: "button", class: "btn sm" + (qty === v ? " primary" : ""), onclick: () => { selected.set(id, v); draw(); } }, l))
        : [h("button", { type: "button", class: "btn sm", onclick: () => { selected.set(id, Math.max(1, qty - 1)); draw(); } }, "−"),
          h("button", { type: "button", class: "btn sm", onclick: () => { selected.set(id, qty + 1); draw(); } }, "+")];
      const left = m.stock - qty;
      return h("div", { class: "card", style: { padding: "10px 12px" } },
        h("div", { class: "row" }, h("b", { class: "grow" }, m.display_name), h("span", {}, money(matCost(m, qty))),
          h("button", { type: "button", class: "icon-btn", "aria-label": "Прибрати", onclick: () => { selected.delete(id); draw(); } }, icon("x", "sm"))),
        h("div", { class: "row", style: { marginTop: "6px" } }, ...quick, inp, h("span", { class: "muted small" }, m.unit)),
        h("div", { class: "small " + (left < 0 ? "neg" : "muted"), style: { marginTop: "4px" } },
          `Після списання залишиться: ${fmtQty(m, left)}`));
    }), ...custom.map((c, i) => {
      sum += c.unit_cost;
      return h("div", { class: "row card", style: { padding: "8px 12px" } }, h("span", { class: "grow" }, c.name), money(c.unit_cost),
        h("button", { type: "button", class: "icon-btn", onclick: () => { custom.splice(i, 1); draw(); } }, icon("x", "sm")));
    })].filter(Boolean));
    total.textContent = selected.size + custom.length ? `${selected.size + custom.length} · ${money(sum)}` : "не обрано";
    const s = q.value.trim().toLowerCase();
    const found = all.filter((m) => !selected.has(m.id) && (!s || (m.display_name + " " + (m.category || "")).toLowerCase().includes(s)));
    results.replaceChildren(...[...found.slice(0, s ? 30 : 8).map((m) => h("button", {
      type: "button", class: "row", style: { width: "100%", padding: "8px 4px", border: 0, borderBottom: "1px solid var(--line)", background: "none", cursor: "pointer", textAlign: "left" },
      onclick: () => { selected.set(m.id, m.pack_size > 1 ? m.pack_size : 1); q.value = ""; draw(); },
    }, icon("plus", "sm"), h("span", { class: "grow" }, h("b", {}, m.display_name), m.category ? h("span", { class: "muted small" }, " · " + m.category) : null),
      h("span", { class: "small " + (m.low ? "neg" : "muted") }, "є " + fmtQty(m, m.stock)))),
    found.length > (s ? 30 : 8) ? h("div", { class: "small muted", style: { padding: "6px 4px" } }, `Ще ${found.length - (s ? 30 : 8)}… уточніть пошук`) : null,
    !all.length ? h("div", { class: "small muted", style: { padding: "6px 4px" } }, "Склад порожній — матеріали додає директор у розділі «Склад»") : null].filter(Boolean));
  };
  q.addEventListener("input", draw);
  const cName = h("input", { placeholder: "Інший матеріал (не зі складу)" });
  const cCost = h("input", { placeholder: "Собівартість, ₴", inputmode: "decimal" });
  draw();
  return h("details", { open: open || null, class: "card", style: { padding: "4px 12px" } },
    h("summary", { class: "row", style: { padding: "8px 0", cursor: "pointer", listStyle: "none" } }, icon("box", "sm"), h("b", { class: "grow" }, "Використані матеріали"), total, icon("right", "sm")),
    h("div", { class: "search" }, icon("search", "sm"), q), results, chosen,
    h("div", { class: "row", style: { margin: "10px 0 8px" } }, h("div", { class: "grow" }, cName), h("div", { style: { width: "130px" } }, cCost),
      h("button", { class: "btn sm", type: "button", onclick: () => {
        const cost = toKop(cCost.value);
        if (!cName.value.trim() || !(cost >= 0)) return toast("Вкажіть назву і собівартість", true);
        custom.push({ name: cName.value.trim(), unit_cost: cost, qty: 1 }); cName.value = ""; cCost.value = ""; draw();
      } }, "Додати")));
}

function closeOrderSheet(a, onChange) {
  const key = uuid(); // ідемпотентність: повторне натискання не задвоїть оплату
  let method = null;
  const mats = new Map(); // material_id -> кількість у базових одиницях
  const custom = [];
  const amount = h("input", { class: "huge", inputmode: "decimal", value: kopToInput(a.price) });
  const PAY_ICON = { cash: "banknote", card: "card", transfer: "transfer" };
  const pick = (k) => (e) => { method = k; [...methods.children].forEach((b) => b.classList.remove("on")); e.currentTarget.classList.add("on");
    btn.replaceChildren(icon("ok"), k === "terminal" ? "Оплатити на терміналі" : "Закрити замовлення"); };
  const methods = h("div", { class: "choice", style: { gridTemplateColumns: "1fr 1fr" } }, Object.entries(PAY_CHOICES).map(([k, t]) =>
    h("button", { type: "button", onclick: pick(k) }, icon(PAY_ICON[k]), t)));
  // «Термінал» ПриватБанку на планшеті (NFC): показуємо, якщо увімкнено в налаштуваннях
  api.get("/api/card-terminal/status").then((st) => {
    if (st.mode === "off") return;
    methods.style.gridTemplateColumns = "1fr 1fr 1fr";
    methods.append(h("button", { type: "button", onclick: pick("terminal") }, icon("card"), st.mode === "mock" ? "Термінал (імітація)" : "Термінал (NFC)"));
  }).catch(() => {});
  // ім'я та прізвище клієнта — для нормальної бази (з Telegram часто приходить нікнейм)
  const [first0, ...rest0] = (a.client.name || "").split(/\s+/).filter(Boolean);
  const firstName = h("input", { value: first0 || "", placeholder: "Ім'я", autocomplete: "off", autocapitalize: "words", id: "close-first" });
  const lastName = h("input", { value: rest0.join(" "), placeholder: "Прізвище", autocomplete: "off", autocapitalize: "words", id: "close-last" });
  const looksLikeNick = /[_@\d]|^[A-Za-z]+$/.test(a.client.name || "") || a.source === "telegram";
  const clientBox = h("div", { class: "card", style: { padding: "12px", background: looksLikeNick ? "var(--warn-bg)" : null } },
    h("div", { class: "row small", style: { marginBottom: "8px" } }, icon("user", "sm"), h("b", {}, "Клієнт"),
      h("span", { class: "muted" }, a.client.phone),
      looksLikeNick ? h("span", { class: "muted grow right" }, "перевірте ім'я — могло прийти з Telegram") : null),
    h("div", { class: "form-grid", style: { gridTemplateColumns: "1fr 1fr" } }, firstName, lastName));
  const svc = state.services.find((s) => s.id === a.service_id) || {};
  const isColoring = /фарб|мелір|blonde|чорн|тон/i.test([svc.category, svc.subcategory, svc.name].join(" "));
  const matsBox = materialsPicker(mats, custom, { open: isColoring });

  const btn = h("button", { class: "btn primary big", onclick: async () => {
    const kop = toKop(amount.value);
    if (!(kop >= 0)) return toast("Вкажіть фактичну вартість", true);
    if (!method) return toast("Оберіть спосіб оплати", true);
    const clientName = [firstName.value, lastName.value].map((x) => x.trim()).filter(Boolean).join(" ");
    if (firstName.value.trim().length < 2) { firstName.focus(); return toast("Вкажіть ім'я клієнта", true); }
    const materials = [...mats.entries()].filter(([, q]) => q > 0).map(([id, q]) => ({ material_id: id, qty: q })).concat(custom);
    if (method === "terminal") {
      if (kop < 100) return toast("Оплата карткою — від 1 грн", true);
      btn.disabled = true;
      try {
        const p = await run(() => api.post("/api/card-payments", { appointment_id: a.id, amount: kop, materials, client_name: clientName }));
        closeSheet();
        terminalPaySheet(p, onChange);
      } catch (_) { btn.disabled = false; }
      return;
    }
    btn.disabled = true;
    try {
      const o = await run(() => api.post(`/api/appointments/${a.id}/close`, { amount: kop, payment_method: method, materials, idempotency_key: key, client_name: clientName }));
      closeSheet();
      toast(`Замовлення закрито. Заробіток майстра: ${money(o.master_earning)}`);
      state.materials = await api.get("/api/materials").catch(() => state.materials);
      onChange();
    } catch (_) { btn.disabled = false; }
  } }, icon("ok"), "Закрити замовлення");

  openSheet("Закрити замовлення",
    h("div", { class: "muted", style: { marginBottom: "10px" } }, `${a.service_name} · ${hm(a.starts_at)}`),
    h("div", { class: "stack" }, clientBox, field("Фактична вартість, ₴", amount), field("Спосіб оплати", methods), matsBox, btn));
  autoFocus(amount, true);
  // на телефоні: торкнулися суми — одразу виділяємо її, щоб ввести нову
  amount.addEventListener("focus", () => setTimeout(() => amount.select(), 0));
}

// ---- оплата карткою через «Термінал» ПриватБанку (NFC на планшеті). Фінальний статус перевіряє СЕРВЕР у банку.
function terminalPaySheet(p, onChange) {
  const bridge = window.MDSAndroid && typeof window.MDSAndroid.startTerminal === "function" ? window.MDSAndroid : null;
  const msg = h("p", { class: "muted" });
  let busy = false;
  const done = (r) => {
    if (r.status === "paid") {
      window.mdsTerminalResult = null;
      closeSheet();
      toast(`Оплачено карткою ${money(r.amount)}${r.masked_pan ? " · " + r.masked_pan : ""}. Замовлення закрито`);
      onChange();
      return true;
    }
    if (r.status === "failed") {
      window.mdsTerminalResult = null;
      msg.textContent = "Оплата не пройшла: " + (r.message || "відмова") + ". Закрийте вікно і спробуйте ще раз або оберіть інший спосіб.";
      actions.replaceChildren(h("button", { class: "btn", onclick: closeSheet }, "Закрити"));
      return true;
    }
    msg.textContent = r.message || "Очікуємо оплату на терміналі…";
    return false;
  };
  const check = async () => {
    if (busy) return;
    busy = true;
    try { done(await run(() => api.post(`/api/card-payments/${p.id}/check`))); } catch (_) { /* повідомлення вже показано */ }
    busy = false;
  };
  const launch = () => { if (bridge) bridge.startTerminal(p.deeplink); };
  window.mdsTerminalResult = () => { check(); }; // відповідь застосунку — лише сигнал «перевір у банку»
  const actions = h("div", { class: "row" },
    p.mode === "mock" ? [
      h("button", { class: "btn primary", onclick: async () => { try { done(await run(() => api.post(`/api/card-payments/${p.id}/mock-complete?ok=true`))); } catch (_) {} } }, "Імітувати оплату"),
      h("button", { class: "btn", onclick: async () => { try { done(await run(() => api.post(`/api/card-payments/${p.id}/mock-complete?ok=false`))); } catch (_) {} } }, "Імітувати відмову")]
      : bridge ? h("button", { class: "btn primary", onclick: launch }, icon("card"), "Відкрити «Термінал»") : null,
    h("button", { class: "btn", onclick: check }, "Перевірити оплату"),
    h("button", { class: "btn", onclick: async () => {
      try {
        const r = await run(() => api.post(`/api/card-payments/${p.id}/cancel`));
        if (r.status === "failed") { window.mdsTerminalResult = null; closeSheet(); toast("Оплату скасовано"); } else done(r);
      } catch (_) { /* банк недоступний — скасування неможливе, поки не перевіримо */ }
    } }, "Скасувати"));
  openSheet("Оплата карткою",
    h("div", { class: "stack" },
      h("div", { class: "huge", style: { textAlign: "center", fontWeight: 700 } }, money(p.amount)),
      p.mode === "mock" ? h("p", { class: "muted small" }, "Режим імітації: гроші не списуються.")
        : bridge ? h("p", { class: "muted small" }, "Відкриється «Термінал» ПриватБанку — клієнт прикладає картку або телефон до планшета.")
        : h("p", { class: "muted small" }, "Відкрийте MDS на планшеті з застосунком «Термінал» ПриватБанку, щоб прийняти оплату. Після оплати натисніть «Перевірити оплату»."),
      msg, actions));
  if (p.mode !== "mock") launch();
}

function apptForm({ appt = null, master_id = null, date = today(), time = "10:00" } = {}, onChange) {
  const isMaster = state.user.role === "master";
  const masters = isMaster ? [{ id: state.user.id, full_name: state.user.full_name }] : state.masters;
  const activeServices = state.services.filter((s) => s.is_active);
  const svc = select(activeServices.map((s) => [s.id, `${[s.section, s.subcategory || s.category].filter(Boolean).join(" · ")}${s.section || s.category ? " · " : ""}${s.name} · ${priceText(s)} · ${s.duration_min} хв`]), appt ? appt.service_id : activeServices[0]?.id);
  const mst = select(masters.map((m) => [m.id, m.full_name]), appt ? appt.master_id : master_id || masters[0]?.id);
  const d = h("input", { type: "date", value: appt ? appt.starts_at.slice(0, 10) : date });
  const t = h("input", { type: "time", step: 300, value: appt ? hm(appt.starts_at) : time });
  const dur = h("input", { type: "number", min: 5, step: 5, value: appt ? appt.duration_min : "" , placeholder: "за послугою" });
  const price = h("input", { inputmode: "decimal", value: appt ? kopToInput(appt.price) : "", placeholder: "за послугою" });
  const comment = h("input", { value: appt?.comment || "" });
  const slots = h("div", { class: "row small" });
  const phone = h("input", { type: "tel", placeholder: "+380…" });
  const name = h("input", { placeholder: "Ім'я" });
  const found = h("div", { class: "muted small" });
  let clientId = null;

  const loadSlots = async () => {
    slots.replaceChildren(h("span", { class: "muted" }, "Вільний час: …"));
    try {
      const list = await api.get(`/api/availability?master_id=${mst.value}&service_id=${svc.value}&day=${d.value}`);
      slots.replaceChildren(h("span", { class: "muted" }, "Вільно:"), ...(list.length ? list.slice(0, 24).map((x) =>
        h("button", { class: "btn sm ghost", type: "button", onclick: () => { t.value = x; } }, x)) : [h("span", { class: "muted" }, "немає")]));
    } catch (e) { slots.replaceChildren(); }
  };
  [svc, mst, d].forEach((el) => el.addEventListener("change", loadSlots));
  svc.addEventListener("change", () => {
    const s = state.services.find((x) => String(x.id) === svc.value);
    if (s && !appt) { dur.placeholder = s.duration_min + " хв"; price.placeholder = kopToInput(s.price); }
  });
  let timer;
  phone.addEventListener("input", () => {
    clearTimeout(timer);
    clientId = null;
    timer = setTimeout(async () => {
      const digits = phone.value.replace(/\D/g, "");
      if (digits.length < 9 || isMaster && false) { found.textContent = ""; return; }
      try {
        const list = await api.get("/api/clients?q=" + encodeURIComponent(digits.slice(-9)));
        if (list.length === 1) {
          clientId = list[0].id; name.value = list[0].name; found.textContent = "Постійний клієнт: " + list[0].name;
          const card = await api.get(`/api/clients/${clientId}`).catch(() => null);
          const fav = card?.favorite_master;
          if (fav && !isMaster && masters.some((m) => m.id === fav.id)) {
            if (!master_id) { mst.value = String(fav.id); loadSlots(); }
            found.textContent = `Постійний клієнт: ${list[0].name} · ⭐ улюблений майстер — ${fav.full_name} (${fav.visits} візитів)`;
          }
        }
        else found.textContent = list.length ? `Знайдено ${list.length}` : "Новий клієнт";
      } catch (_) { found.textContent = ""; }
    }, 350);
  });

  const save = h("button", { class: "btn gold big", onclick: async () => {
    const body = {
      master_id: +mst.value, service_id: +svc.value, starts_at: `${d.value}T${t.value}`,
      comment: comment.value.trim() || null,
    };
    if (dur.value) body.duration_min = +dur.value;
    if (price.value) body.price = toKop(price.value);
    save.disabled = true;
    try {
      if (appt) {
        await run(() => api.patch(`/api/appointments/${appt.id}`, body), "Запис змінено");
      } else {
        if (clientId) body.client_id = clientId;
        else { body.client_phone = phone.value; body.client_name = name.value; }
        await run(() => api.post("/api/appointments", body), "Запис створено");
      }
      closeSheet();
      onChange();
    } catch (_) { save.disabled = false; }
  } }, appt ? "Зберегти зміни" : "Записати");

  openSheet(appt ? "Перенести / змінити запис" : "Новий запис",
    h("div", { class: "stack" },
      appt ? h("div", { class: "muted" }, appt.client.name, " · ", appt.client.phone) :
        h("div", { class: "form-grid" }, field("Телефон клієнта", phone), field("Ім'я", name)),
      appt ? null : found,
      field("Послуга", svc),
      isMaster ? null : field("Майстер", mst),
      h("div", { class: "form-grid" }, field("Дата", d), field("Час", t)),
      slots,
      h("div", { class: "form-grid" }, field("Тривалість, хв", dur), field("Ціна, ₴", price)),
      field("Коментар", comment),
      save));
  loadSlots();
}

async function clientSheet(id) {
  const c = await run(() => api.get(`/api/clients/${id}`));
  const isMgr = state.user.role !== "master";
  const visits = c.history.filter((a) => a.status === "completed").length;
  const name = h("input", { value: c.name });
  const phone = h("input", { value: c.phone });
  const notes = h("textarea", { rows: 2 }, c.notes || "");
  openSheet(c.name,
    h("div", { class: "stack" },
      h("div", {}, h("a", { href: "tel:" + c.phone }, c.phone), c.telegram_linked ? h("span", { class: "chip", style: { marginLeft: "8px" } }, "Telegram") : null),
      h("div", { class: "muted" }, `Візитів: ${visits} · записів усього: ${c.history.length}`),
      c.favorite_master ? h("div", { class: "info-row" }, icon("user"), "⭐ Улюблений майстер: ", h("b", {}, c.favorite_master.full_name), ` (${c.favorite_master.visits} візитів)`) : null,
      isMgr ? h("details", {}, h("summary", { class: "muted", style: { cursor: "pointer" } }, "Редагувати"),
        h("div", { class: "stack", style: { marginTop: "8px" } }, field("Ім'я", name), field("Телефон", phone), field("Нотатки", notes),
          h("button", { class: "btn gold", onclick: async () => {
            await run(() => api.patch(`/api/clients/${id}`, { name: name.value, phone: phone.value, notes: notes.value }), "Збережено");
            clientSheet(id);
          } }, "Зберегти"))) : (c.notes ? h("div", { class: "muted" }, c.notes) : null),
      h("h3", {}, "Історія відвідувань"),
      c.history.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("tr", {}, h("th", {}, "Дата"), h("th", {}, "Послуга"), h("th", {}, "Майстер"), h("th", {}, "Статус")),
        c.history.map((a) => h("tr", {}, h("td", {}, dmy(a.starts_at), " ", hm(a.starts_at)), h("td", {}, a.service_name),
          h("td", {}, a.master_name), h("td", {}, chip(a.status)))))) : emptyState("Ще немає візитів")));
}

// ============================================================ екрани
const VIEWS = {};

// ---------- Непідтверджені записи (блок угорі для майстра й адміністратора)
function dayWord(iso) {
  const d = iso.slice(0, 10);
  if (d === today()) return "Сьогодні";
  if (d === addDays(today(), 1)) return "Завтра";
  return `${weekday(d)}, ${dm(d)}`;
}

function proposeForm(a, onChange) {
  const d = h("input", { type: "date", value: a.starts_at.slice(0, 10), min: today(), id: "propose-day" });
  const box = h("div", { class: "row" });
  const load = async () => {
    box.replaceChildren(h("span", { class: "muted small" }, "Шукаю вільний час…"));
    const list = await api.get(`/api/availability?master_id=${a.master_id}&service_id=${a.service_id}&day=${d.value}`).catch(() => []);
    box.replaceChildren(...(list.length ? list.map((t) => h("button", { class: "btn sm", onclick: async () => {
      const r = await run(() => api.post(`/api/appointments/${a.id}/propose`, { starts_at: `${d.value}T${t}` }));
      closeSheet();
      toast(r.notified ? "Клієнту надіслано пропозицію в Telegram" : `Час змінено. Клієнт не в Telegram — зателефонуйте: ${a.client.phone}`);
      onChange();
    } }, t)) : [h("span", { class: "muted small" }, "На цей день вільного часу немає")]));
  };
  d.addEventListener("change", load);
  openSheet("Запропонувати інший час", h("div", { class: "stack" },
    h("div", { class: "muted" }, `${a.client.name} хоче на ${dayWord(a.starts_at)} о ${hm(a.starts_at)} · ${a.service_name}`),
    field("День", d), h("div", { class: "kpi-label" }, "Вільний час майстра"), box,
    h("p", { class: "muted small" }, a.client.telegram_linked
      ? "Клієнт отримає в боті: «Майстер пропонує інший час» з кнопками «Підходить» / «Інший час» / «Скасувати»."
      : `Клієнт записаний не через Telegram — після зміни зателефонуйте йому: ${a.client.phone}`)));
  load();
}

function declineBusy(a, onChange) {
  confirmSheet("Цей час зайнятий?", a.client.telegram_linked
    ? `Запис ${dayWord(a.starts_at)} о ${hm(a.starts_at)} буде знято. Бот напише клієнту, що час зайнятий, і запропонує найближчі вільні години.`
    : `Запис буде знято. Клієнт не в Telegram — зателефонуйте йому: ${a.client.phone}`,
  "Так, зайнято", async () => {
    const r = await run(() => api.post(`/api/appointments/${a.id}/decline-busy`));
    toast(r.notified ? "Клієнту запропоновано інший вільний час" : "Запис знято. Зателефонуйте клієнту");
    onChange();
  }, { danger: true });
}

async function pendingBlock(onChange) {
  const list = await api.get("/api/appointments/pending").catch(() => []);
  if (!list.length) return null;
  const isMgr = state.user.role !== "master";
  return h("div", { class: "card", style: { borderColor: "rgba(183,121,31,.45)", background: "linear-gradient(180deg, var(--warn-bg), var(--surface) 70%)", marginBottom: "14px" } },
    h("div", { class: "card-head" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("alert"), `Непідтверджені записи: ${list.length}`),
      h("span", { class: "muted small" }, "Підтвердіть, запропонуйте інший час або дайте відповідь «зайнято»")),
    list.map((a) => h("div", { class: "row", style: { padding: "10px 0", borderTop: "1px solid var(--line)", alignItems: "flex-start" } },
      h("div", { style: { minWidth: "96px" } }, h("b", {}, dayWord(a.starts_at)), h("div", { style: { fontSize: "18px", fontWeight: 700 } }, hm(a.starts_at))),
      h("div", { class: "grow", style: { minWidth: "180px" } },
        h("div", {}, h("b", {}, a.client.name), " · ", h("a", { href: "tel:" + a.client.phone }, a.client.phone)),
        h("div", { class: "muted small" }, a.service_name, isMgr ? ` · ${a.master_name}` : "", a.source === "telegram" ? " · з Telegram" : ""),
        a.awaiting_client ? h("div", { class: "chip st-in_progress", style: { marginTop: "6px", whiteSpace: "normal", borderRadius: "8px" } }, `Запропоновано ${hm(a.starts_at)} — чекаємо відповіді клієнта в боті`) : null),
      h("div", { class: "row" },
        h("button", { class: "btn sm primary", title: a.awaiting_client ? "Якщо клієнт погодився телефоном, а не в боті" : null,
          onclick: async () => { await run(() => api.post(`/api/appointments/${a.id}/status`, { status: "confirmed" }), "Запис підтверджено"); onChange(); } },
          icon("ok", "sm"), a.awaiting_client ? "Клієнт погодився" : "Підтвердити"),
        h("button", { class: "btn sm", onclick: () => proposeForm(a, onChange) }, icon("clock", "sm"), "Інший час"),
        h("button", { class: "btn sm danger", onclick: () => declineBusy(a, onChange) }, "Зайнято")))));
}

// ---------- Сьогодні (майстер)
VIEWS.today = async (main) => {
  const d = today();
  const [list, my, shift] = await Promise.all([
    api.get(`/api/appointments?date_from=${d}`), api.get("/api/finance/my"), api.get("/api/shifts/current"),
  ]);
  const active = list.filter((a) => !["cancelled", "no_show"].includes(a.status));
  const left = active.filter((a) => a.status !== "completed");
  main.append(topbar(`Добрий день, ${state.user.full_name.split(" ")[0]}`,
    `${WD_LONG[(new Date().getDay() + 6) % 7]}, ${dm(d)} · записів: ${active.length}, залишилось: ${left.length}`,
    h("button", { class: "btn primary", onclick: () => apptForm({ date: d }, reload) }, icon("plus", "sm"), "Запис")));
  main.append(h("div", { class: "grid kpi", style: { marginBottom: "16px" } },
    kpi("Мій заробіток сьогодні", my.day.earning, "dark", { ico: "wallet" }),
    kpi("Виручка сьогодні", my.day.revenue, "", { ico: "banknote" }),
    kpi("Цей місяць", my.month.earning, "", { ico: "chart", sub: `${my.month.orders_count} послуг` }),
    h("div", { class: "card kpi-tile" }, h("div", { class: "kpi-top" }, h("span", { class: "kpi-ico" }, icon("check", "sm")), h("span", { class: "kpi-label" }, "Зміна")),
      h("div", { class: "kpi-value", style: { fontSize: "18px" } }, shift ? "Відкрита з " + hm(shift.opened_at) : "Не відкрита"),
      h("a", { href: "#/shift", class: "small" }, shift ? "Закрити зміну →" : "Деталі →"))));
  const pend = await pendingBlock(reload);
  if (pend) main.querySelector(".topbar").after(pend);
  if (!active.length) { main.append(h("div", { class: "card" }, emptyState("На сьогодні записів немає", "calendar"))); return; }
  for (const a of active) {
    const done = a.status === "completed";
    main.append(h("div", { class: `card appt st-b-${a.status}` + (done ? " done" : ""), onclick: () => (done ? null : apptSheet(a, reload)) },
      h("div", { class: "appt-time" }, hm(a.starts_at), h("small", {}, "до " + hm(a.ends_at))),
      h("div", { class: "appt-body" },
        h("div", { class: "row" }, h("span", { class: "appt-title" }, a.client.name), chip(a.status)),
        h("div", { class: "muted" }, a.service_name, " · ", money(a.price)),
        h("a", { href: "tel:" + a.client.phone, class: "small row", style: { gap: "4px", marginTop: "2px" }, onclick: (e) => e.stopPropagation() }, icon("phone", "sm"), a.client.phone)),
      done ? h("span", { class: "muted small" }, "Закрито") :
        h("button", { class: "btn primary", onclick: (e) => { e.stopPropagation(); closeOrderSheet(a, reload); } }, icon("banknote", "sm"), "Закрити")));
  }
};

// ---------- Календар
VIEWS.calendar = async (main) => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const day = params.get("d") || today();
  const isMaster = state.user.role === "master";
  const narrow = window.matchMedia("(max-width: 760px)").matches;
  // вигляд: «Сітка» (колонки майстрів) або «Список» (стрічка записів); на телефоні за замовчуванням — список
  const mode = params.get("v") || store.get("mds_cal_view") || (narrow ? "list" : "grid");
  const go = (dd, v = mode) => { location.hash = `#/calendar?d=${dd}&v=${v}`; };
  const setMode = (v) => { store.set("mds_cal_view", v); go(day, v); };

  // вибір дати: справжнє поле дати поверх кнопки — відкриває системний календар і на iPhone, і на Android
  const datePick = h("span", { class: "btn date-pick", title: "Обрати дату" }, icon("calendar", "sm"), h("span", { class: "m-hide" }, "Дата"),
    h("input", { type: "date", value: day, "aria-label": "Обрати дату", onchange: (e) => e.target.value && go(e.target.value) }));
  main.append(topbar("Календар", `${WD_LONG[(new Date(day + "T12:00").getDay() + 6) % 7]}, ${dmy(day)}`,
    h("div", { class: "btn-group" },
      h("button", { class: "btn", "aria-label": "Попередній день", onclick: () => go(addDays(day, -1)) }, icon("left", "sm")),
      h("button", { class: "btn" + (day === today() ? " on" : ""), onclick: () => go(today()) }, "Сьогодні"),
      h("button", { class: "btn", "aria-label": "Наступний день", onclick: () => go(addDays(day, 1)) }, icon("right", "sm"))),
    datePick,
    h("div", { class: "seg icons", role: "tablist" },
      h("button", { class: mode === "grid" ? "on" : "", title: "Сітка по майстрах", "aria-label": "Сітка", onclick: () => setMode("grid") }, icon("grid", "sm"), h("span", {}, "Сітка")),
      h("button", { class: mode === "list" ? "on" : "", title: "Список записів", "aria-label": "Список", onclick: () => setMode("list") }, icon("rows", "sm"), h("span", {}, "Список"))),
    h("button", { class: "btn primary", onclick: () => apptForm({ date: day }, reload) }, icon("plus", "sm"), "Новий запис")));

  // стрічка днів (тиждень довкола) з кількістю записів — одним запитом на весь діапазон
  const wdIdx = (iso) => String((new Date(iso + "T12:00").getDay() + 6) % 7);
  const salonSched = state.settings.schedule || {};
  const stripFrom = addDays(day, -3), stripTo = addDays(day, 10);
  const range = await api.get(`/api/appointments?date_from=${stripFrom}&date_to=${stripTo}`);
  const isActive = (a) => !["cancelled", "no_show"].includes(a.status);
  const perDay = {};
  for (const a of range) if (isActive(a)) perDay[a.starts_at.slice(0, 10)] = (perDay[a.starts_at.slice(0, 10)] || 0) + 1;
  const strip = h("div", { class: "daystrip" });
  for (let i = -3; i <= 10; i++) {
    const dd = addDays(day, i);
    const closed = !salonSched[wdIdx(dd)];
    const n = perDay[dd] || 0;
    strip.append(h("button", { class: (dd === day ? "on " : "") + (dd === today() ? "today " : "") + (closed ? "off" : ""), onclick: () => go(dd),
      title: closed ? "Вихідний" : n ? `Записів: ${n}` : "Записів немає" },
      h("small", {}, weekday(dd)), h("b", {}, Number(dd.slice(8, 10))), h("i", { class: "cnt" + (n ? "" : " zero") }, n ? String(n) : "·")));
  }
  main.append(strip);
  requestAnimationFrame(() => strip.querySelector(".on")?.scrollIntoView({ inline: "center", block: "nearest" }));

  const list = range.filter((a) => a.starts_at.slice(0, 10) === day);
  const activeList = list.filter(isActive).sort((a, b) => a.starts_at.localeCompare(b.starts_at));
  // непідтверджені: на телефоні — компактна кнопка (список відкривається знизу), щоб не витісняти календар
  const pend = await pendingBlock(reload);
  if (pend) {
    if (narrow) {
      const n = pend.querySelectorAll(":scope > .row").length;
      main.append(h("button", { class: "callout pend-btn", onclick: () => openSheet("Непідтверджені записи", pend) },
        icon("alert"), h("b", { class: "grow" }, `Непідтверджені записи: ${n}`), h("span", { class: "small" }, "Відкрити"), icon("right", "sm")));
    } else main.append(pend);
  }
  if (!isMaster) {
    // «Швидкий запис» з Telegram: клієнти просять передзвонити
    const calls = await api.get("/api/callbacks").catch(() => []);
    if (calls.length) {
      main.append(h("div", { class: "callout" }, icon("phone"),
        h("div", { class: "grow" }, h("b", {}, `Просять передзвонити: ${calls.length}`),
          calls.map((c) => h("div", { class: "row", style: { padding: "5px 0" } },
            h("span", {}, c.name || "Клієнт"), h("a", { href: "tel:" + c.phone }, h("b", {}, c.phone)),
            h("span", { class: "muted small grow" }, dm(c.created_at), " ", hm(c.created_at)),
            h("button", { class: "btn sm", onclick: async () => { await run(() => api.post(`/api/callbacks/${c.id}/done`), "Позначено"); reload(); } }, icon("ok", "sm"), "Передзвонили"))))));
    }
  }
  const masters = isMaster ? [{ id: state.user.id, full_name: state.user.full_name, schedule: state.user.schedule }] : state.masters;
  if (!masters.length) { main.append(h("div", { class: "card" }, emptyState("Додайте майстрів у розділі «Персонал»", "users"))); return; }

  // робочі години на цю дату з урахуванням змін графіка (розділ «Графік роботи»)
  const sched = await api.get(`/api/schedule?date_from=${day}&date_to=${day}`).catch(() => null);
  const wd = wdIdx(day);
  const spanOf = (m) => {
    const c = sched?.cells?.[m.id]?.[day];
    if (c) return c.off ? null : [c.start, c.end];
    return ((m.schedule || salonSched)[wd] || null);
  };
  const toMin = (s) => { const [a, b] = s.split(":").map(Number); return a * 60 + b; };

  // підсумок дня
  const waiting = activeList.filter((a) => a.status === "pending").length;
  const planned = activeList.reduce((s, a) => s + (a.price || 0), 0);
  const working = masters.filter((m) => spanOf(m)).length;
  main.append(h("div", { class: "day-sum" },
    h("span", {}, icon("calendar", "sm"), h("b", {}, activeList.length), " ", plural(activeList.length, "запис", "записи", "записів")),
    waiting ? h("span", { class: "warn" }, icon("alert", "sm"), "очікують: ", h("b", {}, waiting)) : null,
    !isMaster ? h("span", {}, icon("users", "sm"), working ? `працюють: ${working} з ${masters.length}` : "усі майстри вихідні") : null,
    planned ? h("span", {}, icon("banknote", "sm"), "за прайсом ", h("b", {}, money(planned))) : null));

  if (mode === "list") {
    const agenda = h("div", { class: "agenda anim-in" });
    const nowMin = day === today() ? new Date().getHours() * 60 + new Date().getMinutes() : null;
    let nowShown = nowMin === null;
    const nowMark = () => h("div", { class: "ag-now" }, h("span", {}, "Зараз ", `${pad(Math.floor(nowMin / 60))}:${pad(nowMin % 60)}`));
    for (const a of activeList) {
      if (!nowShown && toMin(hm(a.starts_at)) > nowMin) { agenda.append(nowMark()); nowShown = true; }
      const past = nowMin !== null && toMin(hm(a.ends_at)) <= nowMin;
      agenda.append(h("button", { class: "ag-item st-" + a.status + (past ? " past" : ""), onclick: () => apptSheet(a, reload) },
        h("div", { class: "ag-time" }, h("b", {}, hm(a.starts_at)), h("span", {}, hm(a.ends_at))),
        h("div", { class: "ag-body" },
          h("div", { class: "ag-client" }, a.client.name),
          h("div", { class: "ag-svc" }, icon("scissors", "sm"), a.service_name),
          !isMaster ? h("div", { class: "ag-master" }, avatar(a.master_name, "xs"), a.master_name) : null),
        h("div", { class: "ag-side" }, chip(a.status), h("b", {}, money(a.price)))));
    }
    if (!nowShown && activeList.length) agenda.append(nowMark());
    if (!activeList.length) {
      agenda.append(h("div", { class: "card" }, emptyState(working ? "На цей день записів ще немає" : "Вихідний день салону", "calendar"),
        working ? h("div", { style: { textAlign: "center", paddingBottom: "14px" } },
          h("button", { class: "btn primary", onclick: () => apptForm({ date: day }, reload) }, icon("plus", "sm"), "Записати клієнта")) : null));
    }
    main.append(agenda);
  } else {
    let startMin = 24 * 60, endMin = 0;
    for (const m of masters) { const sp = spanOf(m); if (sp) { startMin = Math.min(startMin, toMin(sp[0])); endMin = Math.max(endMin, toMin(sp[1])); } }
    for (const a of list) { startMin = Math.min(startMin, toMin(hm(a.starts_at))); endMin = Math.max(endMin, toMin(hm(a.ends_at))); }
    if (startMin >= endMin) { startMin = 9 * 60; endMin = 20 * 60; }
    startMin = Math.floor(startMin / 60) * 60; endMin = Math.ceil(endMin / 60) * 60;
    const STEP = 30, ROW = 40, HEAD = 56, TIMEW = 58;
    const rows = (endMin - startMin) / STEP;
    const colW = isMaster ? "minmax(240px, 1fr)" : "minmax(180px, 1fr)";

    const grid = h("div", { class: "cal-grid", style: { gridTemplateColumns: `${TIMEW}px repeat(${masters.length}, ${colW})`, gridTemplateRows: `${HEAD}px repeat(${rows}, ${ROW}px)` } });
    grid.append(h("div", { class: "cal-corner" }));
    masters.forEach((m) => {
      const n = activeList.filter((a) => a.master_id === m.id).length;
      const sp = spanOf(m);
      grid.append(h("div", { class: "cal-head" + (sp ? "" : " is-off") }, avatar(m.full_name, "sm"),
        h("div", { class: "who" }, h("b", {}, m.full_name), h("span", {}, sp ? `${sp[0]}–${sp[1]}` : "вихідний")),
        n ? h("i", { class: "cnt" }, String(n)) : null));
    });
    for (let r = 0; r < rows; r++) {
      const min = startMin + r * STEP;
      const time = `${pad(Math.floor(min / 60))}:${pad(min % 60)}`;
      grid.append(h("div", { class: "cal-time" + (r === 0 ? " first" : "") }, min % 60 === 0 ? time : ""));
      masters.forEach((m) => {
        const sp = spanOf(m);
        const off = !sp || min < toMin(sp[0]) || min >= toMin(sp[1]);
        grid.append(h("div", { class: "cal-cell" + (off ? " off" : "") + (min % 60 ? " half" : ""), title: off ? "Неробочий час" : `Новий запис · ${m.full_name} · ${time}`,
          onclick: off ? null : () => apptForm({ master_id: m.id, date: day, time }, reload) }, off ? null : h("span", { class: "cell-add" }, "+ ", time)));
      });
    }
    const wrap = h("div", { class: "cal" }, grid);
    main.append(wrap);
    main.append(h("div", { class: "legend" },
      ["pending", "confirmed", "in_progress", "completed"].map((st) => chip(st)),
      h("span", {}, "Натисніть на вільний час, щоб записати клієнта")));
    // події позиціонуємо після рендеру, коли відомі ширини колонок
    requestAnimationFrame(() => {
      const heads = grid.querySelectorAll(".cal-head");
      activeList.forEach((a, i) => {
        const col = masters.findIndex((m) => m.id === a.master_id);
        if (col < 0) return;
        const head = heads[col];
        const top = HEAD + ((toMin(hm(a.starts_at)) - startMin) / STEP) * ROW;
        const height = Math.max(((toMin(hm(a.ends_at)) - toMin(hm(a.starts_at))) / STEP) * ROW - 3, 24);
        grid.append(h("div", { class: "cal-ev " + a.status, title: `${hm(a.starts_at)}–${hm(a.ends_at)} ${a.client.name} · ${a.service_name}`,
          style: { top: top + 2 + "px", height: height + "px", left: head.offsetLeft + 4 + "px", width: head.offsetWidth - 8 + "px", animationDelay: Math.min(i * 25, 300) + "ms" },
          onclick: () => apptSheet(a, reload) },
          h("div", { class: "ev-top" }, h("b", {}, a.client.name), height > 30 ? h("span", { class: "t" }, `${hm(a.starts_at)}–${hm(a.ends_at)}`) : null),
          height > 50 ? h("div", { class: "ev-svc" }, a.service_name) : null,
          height > 72 ? h("div", { class: "ev-price" }, money(a.price)) : null));
      });
      // лінія «зараз»
      if (day === today()) {
        const now = new Date();
        const nm = now.getHours() * 60 + now.getMinutes();
        if (nm >= startMin && nm <= endMin) {
          grid.append(h("div", { class: "now-line", style: { top: HEAD + ((nm - startMin) / STEP) * ROW + "px", left: TIMEW + "px", right: "0" } }));
          if (params.get("d") === null) wrap.scrollTop = Math.max(0, ((nm - startMin) / STEP) * ROW - 120);
        }
      }
    });
  }
  const cancelled = list.filter((a) => !isActive(a));
  if (cancelled.length) {
    main.append(h("details", { class: "card", style: { marginTop: "12px" } },
      h("summary", { class: "muted", style: { cursor: "pointer" } }, `Скасовані та неявки: ${cancelled.length}`),
      cancelled.map((a) => h("div", { class: "row", style: { padding: "6px 0", cursor: "pointer" }, onclick: () => apptSheet(a, reload) },
        h("b", {}, hm(a.starts_at)), a.client.name, h("span", { class: "muted grow" }, isMaster ? a.service_name : `${a.master_name} · ${a.service_name}`), chip(a.status)))));
  }
};

// ---------- Заробіток (майстер)
VIEWS.earnings = async (main) => {
  const [my, orders, payouts] = await Promise.all([api.get("/api/finance/my"), api.get("/api/orders?period=month"), api.get("/api/payouts")]);
  main.append(topbar("Мій заробіток", "Нарахування з кожного закритого замовлення та виплати від директора"));
  main.append(h("div", { class: "grid kpi" },
    kpi("Сьогодні", my.day.earning, "", { ico: "clock", sub: `${my.day.orders_count} посл.` }),
    kpi("Цей тиждень", my.week.earning, "", { ico: "calendar", sub: `${my.week.orders_count} посл.` }),
    kpi("Цей місяць", my.month.earning, "", { ico: "chart", sub: `${my.month.orders_count} посл.` }),
    kpi("До виплати", my.balance.balance, "dark", { ico: "wallet" })));
  const paidShare = my.balance.accrued ? Math.min(100, Math.round((my.balance.paid / my.balance.accrued) * 100)) : 0;
  main.append(h("div", { class: "card", style: { marginTop: "12px" } },
    h("div", { class: "sum-line" }, h("span", {}, "Нараховано за весь час"), h("b", {}, money(my.balance.accrued))),
    h("div", { class: "sum-line" }, h("span", {}, "Виплачено"), h("b", {}, money(my.balance.paid))),
    my.balance.charged ? h("div", { class: "sum-line" }, h("span", {}, "Списано: комунальні / генератор"), h("b", {}, "− " + money(my.balance.charged))) : null,
    h("div", { class: "progress", style: { margin: "6px 0" } }, h("div", { style: { width: paidShare + "%" } })),
    h("div", { class: "sum-line total" }, h("span", {}, "Залишок до виплати"), h("span", {}, money(my.balance.balance)))));
  main.append(h("div", { class: "section" }, h("h2", {}, "Замовлення цього місяця")));
  main.append(orders.length ? h("div", { class: "card table-wrap" }, h("table", {},
    h("tr", {}, h("th", {}, "Дата"), h("th", {}, "Послуга"), h("th", {}, "Клієнт"), h("th", { class: "num" }, "Сума"), h("th", { class: "num" }, "Мій заробіток")),
    orders.map((o) => h("tr", {}, h("td", {}, dm(o.closed_at), " ", h("span", { class: "muted" }, hm(o.closed_at))), h("td", {}, o.service_name), h("td", {}, o.client_name),
      h("td", { class: "num" }, money(o.amount)), h("td", { class: "num" }, h("b", {}, money(o.master_earning))))))) : h("div", { class: "card" }, emptyState("Ще немає закритих замовлень", "receipt")));
  if (payouts.length) {
    main.append(h("div", { class: "section" }, h("h2", {}, "Виплати")));
    main.append(h("div", { class: "card table-wrap" }, h("table", {},
      h("tr", {}, h("th", {}, "Дата"), h("th", {}, "Спосіб"), h("th", {}, "Примітка"), h("th", { class: "num" }, "Сума")),
      payouts.map((p) => h("tr", {}, h("td", {}, dmy(p.paid_at)), h("td", {}, PAY[p.method]), h("td", { class: "muted" }, p.note || (p.shift_id ? "За зміну" : "")), h("td", { class: "num" }, money(p.amount)))))));
  }
};

function shiftReport(r) {
  const n = (label, v, ico) => h("div", { class: "card kpi-tile" }, h("div", { class: "kpi-top" }, h("span", { class: "kpi-ico" }, icon(ico, "sm")), h("span", { class: "kpi-label" }, label)), h("div", { class: "kpi-value" }, v));
  return h("div", { class: "stack" },
    h("div", { class: "grid", style: { gridTemplateColumns: "repeat(3, 1fr)" } },
      n("Клієнтів", r.clients_count, "users"), n("Послуг", r.services_count, "scissors"), n("Скасувань", r.cancellations, "cancel")),
    h("div", { class: "card" },
      (r.services || []).map((s) => h("div", { class: "sum-line" }, h("span", {}, s.service, h("span", { class: "muted" }, " × ", s.count)), h("span", {}, money(s.revenue)))),
      h("div", { class: "divider" }),
      h("div", { class: "sum-line" }, h("span", {}, "Загальна виручка"), h("b", {}, money(r.revenue))),
      Object.entries(r.by_payment || {}).map(([k, v]) => h("div", { class: "sum-line muted small" }, h("span", {}, PAY[k] || k), h("span", {}, money(v)))),
      r.cash ? null : h("div", { class: "sum-line" }, h("span", {}, "Заробіток майстра"), h("b", {}, money(r.master_earning))),
      r.cash ? cashBlock(r) : h("div", { class: "sum-line total" }, h("span", {}, "Сума до виплати"), h("span", {}, money(r.payout_due)))));
}

// Розрахунок після зміни: спершу — скільки заробив майстер і як отримає гроші, потім — каса
function cashBlock(r) {
  const c = r.cash;
  const line = (label, v, cls = "") => h("div", { class: "sum-line " + cls }, h("span", {}, label), h("b", {}, money(v)));
  const takeFromTill = c.mode === "take_from_till";
  const fromTill = takeFromTill ? c.master_takes : 0;
  const toCard = takeFromTill ? c.director_pays : r.payout_due;
  return h("div", {},
    h("div", { class: "divider" }),
    // заробіток майстра і з чого він складається
    h("div", { style: { background: "var(--ok-bg)", borderRadius: "10px", padding: "10px 12px", margin: "4px 0 10px" } },
      h("div", { class: "sum-line", style: { fontSize: "16px" } }, h("b", {}, "Заробіток майстра"), h("b", { style: { color: "var(--ok)" } }, money(r.master_earning))),
      line("💵 Готівкою з каси (одразу)", fromTill, "small"),
      line(takeFromTill ? "💳 На карту від директора" : "💳 Виплатить директор", toCard, "small"),
      takeFromTill && toCard <= 0 ? h("div", { class: "small muted" }, "Увесь заробіток отримано готівкою з каси") : null,
      takeFromTill && toCard > 0 && fromTill === 0 ? h("div", { class: "small muted" }, "Клієнти платили карткою, тож готівки в касі немає — увесь заробіток директор переказує на карту") : null),
    // каса салону
    h("div", { class: "kpi-label", style: { margin: "4px 0 6px" } }, "Каса"),
    line("Готівка від клієнтів", c.from_clients),
    line("Карткою — на рахунок салону", c.noncash, "muted"),
    h("div", { class: "sum-line total" }, h("span", {}, "🏦 Залишити в касі"), h("span", {}, money(c.leave_in_till))));
}

// ---------- Зміна (майстер)
VIEWS.shift = async (main) => {
  const [cur, history] = await Promise.all([api.get("/api/shifts/current"), api.get("/api/shifts")]);
  main.append(topbar("Моя зміна", cur ? `Відкрита з ${dmy(cur.opened_at)} ${hm(cur.opened_at)}` : "Зміна відкривається автоматично з першим закритим замовленням"));
  if (cur) {
    main.append(shiftReport(cur.report));
    main.append(h("button", { class: "btn primary big", style: { marginTop: "14px" }, onclick: () => confirmSheet("Закрити зміну?",
      "Буде сформовано звіт, директор отримає його на підтвердження виплати.", "Закрити зміну", async () => {
        const sh = await run(() => api.post("/api/shifts/close"), "Зміну закрито");
        renderApp();
        // показати звіт після того, як закриється вікно підтвердження
        setTimeout(() => openSheet("Звіт зміни", shiftReport(sh.report)), 60);
      }) }, icon("lock"), "Закрити зміну"));
  } else {
    main.append(h("div", { class: "card" }, emptyState("Зараз зміна не відкрита", "clock"),
      h("div", { style: { textAlign: "center" } }, h("button", { class: "btn", onclick: async () => { await run(() => api.post("/api/shifts/open"), "Зміну відкрито"); reload(); } }, "Відкрити зміну зараз"))));
  }
  const past = history.filter((s) => s.status !== "open");
  if (past.length) {
    main.append(h("div", { class: "section" }, h("h2", {}, "Попередні зміни")));
    main.append(h("div", { class: "card table-wrap" }, h("table", {},
      h("tr", {}, h("th", {}, "Дата"), h("th", { class: "num" }, "Виручка"), h("th", { class: "num" }, "До виплати"), h("th", {}, "Статус")),
      past.map((s) => h("tr", { class: "click", onclick: () => openSheet("Зміна " + dmy(s.opened_at), shiftReport(s.report)) },
        h("td", {}, dmy(s.opened_at)), h("td", { class: "num" }, money(s.report.revenue)), h("td", { class: "num" }, money(s.payout_due)), h("td", {}, chip(s.status, SHIFT_STATUS)))))));
  }
};

// ---------- Профіль
VIEWS.profile = async (main) => {
  const u = state.user;
  main.append(topbar("Профіль", "Ваші дані, пароль і підключення Telegram"));
  const oldP = h("input", { type: "password", autocomplete: "current-password", id: "pw-old" });
  const newP = h("input", { type: "password", autocomplete: "new-password", id: "pw-new" });
  const code = h("div", { class: "kpi-value" });
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  main.append(h("div", { class: "grid two" },
    h("div", { class: "card stack" },
      h("div", { class: "row" }, avatar(u.full_name, "lg"), h("div", {}, h("h3", { style: { margin: 0 } }, u.full_name), h("div", { class: "muted" }, ROLES[u.role], " · ", u.username))),
      u.role === "master" && u.share_bp !== null ? h("div", { class: "info-row" }, icon("percent"), "Ваш відсоток: ", h("b", {}, pct(u.share_bp))) : null,
      h("div", { class: "row" },
        h("button", { class: "btn", onclick: toggleTheme }, icon(dark ? "sun" : "moon", "sm"), dark ? "Світла тема" : "Темна тема"),
        h("button", { class: "btn", onclick: bugReportForm }, icon("bug", "sm"), "Повідомити про помилку"),
        h("button", { class: "btn danger", onclick: logout }, icon("logout", "sm"), "Вийти"))),
    h("div", { class: "card stack" }, h("h3", {}, "Сповіщення в Telegram"),
      h("p", { class: "muted", style: { margin: 0 } }, u.telegram_linked ? "Telegram підключено ✓" : "Увійдіть у бот через «Для майстрів» з PIN — або отримайте код і надішліть боту: /link КОД"),
      code,
      h("button", { class: "btn", onclick: async () => { const r = await run(() => api.post("/api/auth/telegram-code")); code.textContent = "/link " + r.code; } }, icon("send", "sm"), "Отримати код")),
    h("div", { class: "card stack" }, h("h3", {}, "Змінити пароль"), field("Поточний пароль", oldP), field("Новий пароль (8+ символів, літери й цифри)", newP),
      h("button", { class: "btn primary", onclick: async () => {
        await run(() => api.post("/api/auth/change-password", { old_password: oldP.value, new_password: newP.value }), "Пароль змінено, увійдіть знову");
        logout();
      } }, "Змінити пароль")),
    ["director", "admin"].includes(u.role) ? await devicesCard(u) : null));
};

// Безпека входу: двофакторний код і довірені пристрої
async function devicesCard(u) {
  const roles = state.settings?.twofa_roles || ["director"];
  const on = roles.includes(u.role);
  const list = await api.get("/api/auth/devices").catch(() => []);
  const ago = (iso) => (iso ? `${dmy(iso)} ${hm(iso)}` : "—");
  return h("div", { class: "card stack" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("lock"), "Безпека входу"),
    !on ? h("p", { class: "muted small", style: { margin: 0 } }, "Код підтвердження для вашої ролі вимкнено (Налаштування → Безпека входу).")
      : u.telegram_linked ? h("p", { class: "small", style: { margin: 0 } }, "✓ Після пароля на новому пристрої приходить 6-значний код у Telegram.")
        : h("div", { class: "callout", style: { margin: 0 } }, icon("alert"), h("div", { class: "small" }, "Підключіть Telegram (картка вище) — без нього код підтвердження надіслати нікуди, і вхід захищений лише паролем.")),
    list.length ? h("div", {}, h("div", { class: "muted small" }, "Запам'ятовані пристрої (вхід без коду до 30 днів):"),
      list.map((d) => h("div", { class: "sum-line" }, h("span", {}, icon("lock", "sm"), " ", d.label), h("span", { class: "muted small" }, "останній вхід ", ago(d.last_used_at)),
        h("button", { class: "btn sm ghost", "aria-label": "Забути", onclick: async () => { await run(() => api.del("/api/auth/devices?device_id=" + d.id), "Пристрій забуто"); reload(); } }, icon("x", "sm"))))) : null,
    list.length ? h("button", { class: "btn danger", onclick: () => confirmSheet("Забути всі пристрої?", "Наступного разу на кожному пристрої знадобиться код з Telegram.", "Забути всі",
      async () => { await run(() => api.del("/api/auth/devices"), "Усі пристрої забуто"); store.del("mds_device"); reload(); }, { danger: true }) }, "Забути всі пристрої") : null);
}

// ---------- Клієнти
VIEWS.clients = async (main) => {
  main.append(topbar("Клієнти", "Пошук, картка клієнта та історія відвідувань"));
  const q = h("input", { placeholder: "Пошук за ім'ям або телефоном", type: "search", id: "client-search" });
  const box = h("div", { class: "card table-wrap", style: { marginTop: "12px" } });
  main.append(h("div", { class: "search" }, icon("search", "sm"), q), box);
  let timer;
  const load = async () => {
    const list = await api.get("/api/clients?q=" + encodeURIComponent(q.value.trim()));
    box.replaceChildren(list.length ? h("table", {}, h("tr", {}, h("th", {}, "Клієнт"), h("th", {}, "Телефон"), h("th", {}, "")),
      list.map((c) => h("tr", { class: "click", onclick: () => clientSheet(c.id) },
        h("td", {}, h("div", { class: "row" }, avatar(c.name, "sm"), h("b", {}, c.name))), h("td", {}, c.phone),
        h("td", { class: "right" }, c.telegram_linked ? h("span", { class: "chip st-confirmed" }, "Telegram") : "")))) : emptyState("Нікого не знайдено", "search"));
  };
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 300); });
  await load();
};

// ---------- Панель директора
function periodPicker(onChange, initial = "month") {
  const from = h("input", { type: "date", style: { width: "auto" }, "aria-label": "З" });
  const to = h("input", { type: "date", style: { width: "auto" }, "aria-label": "По" });
  const custom = h("span", { class: "row hidden" }, from, "—", to,
    h("button", { class: "btn sm", onclick: () => from.value && onChange(`date_from=${from.value}&date_to=${to.value || from.value}`, "custom") }, "Показати"));
  const s = seg([["day", "День"], ["week", "Тиждень"], ["month", "Місяць"], ["year", "Рік"], ["custom", "Період…"]], initial, (v) => {
    custom.classList.toggle("hidden", v !== "custom");
    if (v !== "custom") onChange("period=" + v, v);
  });
  return h("div", { class: "row", style: { marginBottom: "14px" } }, s, custom);
}

// попередній період для порівняння (минулий місяць/рік або той самий відрізок перед поточним)
function prevRange(from, to, kind) {
  const d = (iso) => new Date(iso + "T12:00");
  if (kind === "month") { const f = d(from); const pf = new Date(f.getFullYear(), f.getMonth() - 1, 1, 12); const pt = new Date(f.getFullYear(), f.getMonth(), 0, 12); return [isoDate(pf), isoDate(pt)]; }
  if (kind === "year") { const y = Number(from.slice(0, 4)) - 1; return [`${y}-01-01`, `${y}-12-31`]; }
  const len = Math.round((d(to) - d(from)) / 86400000) + 1;
  return [addDays(from, -len), addDays(from, -1)];
}

function revenueChart(byDay) {
  const NS = "http://www.w3.org/2000/svg";
  const W = 760, H = 190, L = 46, B = 22, T = 10;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Виручка по днях");
  const max = Math.max(1, ...byDay.map((x) => x.revenue));
  const step = Math.pow(10, Math.floor(Math.log10(max / 100 / 4 || 1)));
  const nice = Math.ceil(max / 100 / 4 / step) * step * 4 * 100 || max;
  const el = (tag, attrs, text) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text !== undefined) e.textContent = text; svg.append(e); return e; };
  for (let i = 0; i <= 4; i++) {
    const y = T + (H - T - B) * (1 - i / 4);
    el("line", { x1: L, x2: W, y1: y, y2: y, class: "grid-line" });
    el("text", { x: L - 6, y: y + 3, "text-anchor": "end" }, Math.round((nice * i) / 4 / 100).toLocaleString("uk-UA"));
  }
  const bw = (W - L) / byDay.length;
  byDay.forEach((x, i) => {
    const bh = ((H - T - B) * x.revenue) / nice;
    const r = el("rect", { x: L + i * bw + bw * 0.18, y: H - B - bh, width: Math.max(2, bw * 0.64), height: Math.max(0, bh), rx: 3, class: "bar" });
    const t = document.createElementNS(NS, "title"); t.textContent = `${dm(x.date)}: ${money(x.revenue)}`; r.append(t);
    if (byDay.length <= 16 || i % Math.ceil(byDay.length / 12) === 0) el("text", { x: L + i * bw + bw / 2, y: H - 6, "text-anchor": "middle" }, dm(x.date));
  });
  return svg;
}

VIEWS.dashboard = async (main) => {
  const body = h("div");
  main.append(topbar("Огляд", "Фінанси салону: виручка, зарплати майстрів, витрати й прибуток",
    h("button", { class: "btn", onclick: () => { location.hash = "#/expenses"; } }, icon("plus", "sm"), "Витрата"),
    h("button", { class: "btn primary", onclick: () => { location.hash = "#/calendar"; } }, icon("calendar", "sm"), "Календар")),
    periodPicker((q, kind) => load(q, kind)), body);
  const load = async (q, kind = "month") => {
    body.replaceChildren(h("div", { class: "empty" }, "Завантаження…"));
    const [d, ins] = await Promise.all([api.get("/api/finance/dashboard?" + q), api.get("/api/finance/insights?" + q).catch(() => null)]);
    const s = d.summary;
    const [pf, pt] = prevRange(s.from, s.to, kind);
    const p = (await api.get(`/api/finance/dashboard?date_from=${pf}&date_to=${pt}`).catch(() => null))?.summary || {};
    const vs = kind === "month" ? "мин. місяць" : kind === "year" ? "мин. рік" : "попер. період";
    const exp = s.expenses;
    const expRows = [...(exp.rent ? [["Оренда", exp.rent, "home"]] : []), ["Комунальні", exp.utilities, "zap", "#/utilities"],
      ["Генератор", exp.generator || 0, "gauge", "#/utilities"], ["Реклама", exp.advertising, "megaphone", "#/expenses"], ["Інші витрати", exp.other, "receipt", "#/expenses"]];
    const opEx = s.operating_expenses;
    const ex = { s, p, d, vs, exp, expRows, opEx };
    body.replaceChildren(...[
      h("div", { class: "grid kpi" },
        kpi("Виручка салону", s.revenue, "dark", { ico: "banknote", prev: p.revenue, vs, onclick: () => revenueSheet(ex) }),
        kpi("Витрати", opEx, "", { ico: "wallet", prev: p.operating_expenses, sub: "комунальні, генератор, реклама…", onclick: () => expensesSheet(ex) }),
        kpi("Чистий прибуток", s.net_profit, "accent", { ico: "trend", prev: p.net_profit, vs, onclick: () => profitSheet(ex) }),
        kpi("Валовий прибуток", s.gross_profit, "", { ico: "chart", prev: p.gross_profit, onclick: () => profitSheet(ex) }),
        kpi("Середній чек", s.average_check, "", { ico: "receipt", prev: p.average_check, sub: `${s.orders_count} замовл. · ${s.clients_count} клієнтів`, onclick: () => checkSheet(ex) })),
      ins ? insightsBlock(ins) : null,
      h("div", { class: "grid two", style: { marginTop: "12px" } },
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Куди йдуть гроші"), h("span", { class: "muted small" }, `${dmy(s.from)} — ${dmy(s.to)}`)),
          h("div", { class: "sum-line" }, h("span", {}, "Виручка"), h("b", {}, money(s.revenue))),
          h("div", { class: "sum-line" }, h("span", { class: "muted" }, "− Частка майстрів"), h("span", {}, money(s.masters_share))),
          h("div", { class: "sum-line" }, h("span", { class: "muted" }, "− Матеріали"), h("span", {}, money(s.materials))),
          h("div", { class: "sum-line total" }, h("span", {}, "Валовий прибуток"), h("span", {}, money(s.gross_profit))),
          expRows.map(([l, v, , href]) => h(href ? "a" : "div", { class: "sum-line" + (href ? " link" : ""), href: href || null }, h("span", { class: "muted" }, "− " + l), h("span", {}, money(v)))),
          h("div", { class: "sum-line total" }, h("span", {}, "Чистий прибуток"), h("span", { class: s.net_profit < 0 ? "neg" : "" }, money(s.net_profit)))),
        h("div", { class: "stack" },
          h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Зарплата майстрів")),
            h("div", { class: "sum-line" }, h("span", {}, "Нараховано за період"), h("b", {}, money(s.masters_share))),
            h("div", { class: "sum-line" }, h("span", {}, "Виплачено за період"), h("b", {}, money(s.payouts))),
            h("div", { class: "sum-line total" }, h("span", {}, "Борг по зарплаті зараз"), h("span", {}, money(s.salary_debt_total))),
            s.salary_debt_total > 0 ? h("a", { href: "#/shifts", class: "small" }, "Підтвердити виплати →") : null),
          h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Оплати клієнтів")),
            [["cash", "banknote"], ["card", "card"], ["transfer", "transfer"]].filter(([k]) => k !== "transfer" || s.by_payment.transfer).map(([k, ic]) => {
              const v = s.by_payment[k] || 0;
              return h("div", { class: "stack", style: { marginBottom: "8px" } },
                h("div", { class: "sum-line", style: { padding: 0 } }, h("span", { class: "row" }, icon(ic, "sm"), PAY[k]), h("b", {}, money(v))),
                h("div", { class: "progress" }, h("div", { style: { width: (s.revenue ? (v / s.revenue) * 100 : 0) + "%" } })));
            }),
            h("div", { class: "sum-line muted small" }, h("span", {}, "Закупівля матеріалів (грошовий потік)"), h("span", {}, money(exp.materials_purchases)))))),
      d.by_day.length > 1 ? h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Виручка по днях"), h("span", { class: "muted small" }, "грн")), revenueChart(d.by_day)) : null,
      h("div", { class: "section" }, h("h2", {}, "По майстрах")),
      d.by_master.length ? h("div", { class: "card table-wrap" }, h("table", {},
        h("tr", {}, h("th", {}, "Майстер"), h("th", { class: "num m-hide" }, "Послуг"), h("th", { class: "num" }, "Виручка"), h("th", { class: "num" }, "Нараховано"), h("th", { class: "num m-hide" }, "Виплачено"), h("th", { class: "num" }, "Борг")),
        d.by_master.map((m) => h("tr", {}, h("td", {}, h("div", { class: "row" }, avatar(m.master_name, "sm"), m.master_name)), h("td", { class: "num" }, m.orders_count), h("td", { class: "num" }, money(m.revenue)),
          h("td", { class: "num" }, money(m.masters_share)), h("td", { class: "num" }, money(m.paid)), h("td", { class: "num" }, h("b", {}, money(m.balance)))))))
        : h("div", { class: "card" }, emptyState("Додайте майстрів у розділі «Персонал»", "users")),
      h("div", { class: "section" }, h("h2", {}, "По послугах")),
      d.by_service.length ? h("div", { class: "card table-wrap" }, h("table", {},
        h("tr", {}, h("th", {}, "Послуга"), h("th", { class: "num" }, "К-сть"), h("th", { class: "num" }, "Виручка"), h("th", { class: "num m-hide" }, "Майстрам"), h("th", { class: "num m-hide" }, "Матеріали"), h("th", { class: "num" }, "Салону")),
        d.by_service.map((x) => h("tr", {}, h("td", {}, x.service_name), h("td", { class: "num" }, x.orders_count), h("td", { class: "num" }, money(x.revenue)),
          h("td", { class: "num" }, money(x.masters_share)), h("td", { class: "num" }, money(x.materials)), h("td", { class: "num" }, money(x.salon_net_from_orders))))))
        : h("div", { class: "card" }, emptyState("Немає замовлень за період", "receipt")),
    ].filter(Boolean));
  };
  await load("period=month", "month");
};

// ---------- подробиці плиток «Огляду»
function sheetHead(value, prev, vs, from, to) {
  return h("div", { class: "detail-head" }, h("div", { class: "kpi-value big" }, money(value)),
    h("div", { class: "row small muted" }, deltaEl(value, prev), prev ? `${vs || "попер. період"}: ${money(prev)}` : null, h("span", { class: "grow" }), `${dmy(from)} — ${dmy(to)}`));
}
function revenueSheet({ s, p, d, vs }) {
  const pay = s.by_payment || {};
  const masters = [...d.by_master].filter((m) => m.revenue).sort((a, b) => b.revenue - a.revenue);
  const services = [...d.by_service].sort((a, b) => b.revenue - a.revenue);
  openSheet("Виручка салону", h("div", { class: "stack detail" },
    sheetHead(s.revenue, p.revenue, vs, s.from, s.to),
    h("div", { class: "mini-stats" },
      h("div", {}, h("span", {}, "Замовлень"), h("b", {}, String(s.orders_count))),
      h("div", {}, h("span", {}, "Клієнтів"), h("b", {}, String(s.clients_count))),
      h("div", {}, h("span", {}, "Середній чек"), h("b", {}, money(s.average_check))),
      h("div", {}, h("span", {}, "Скасувань"), h("b", {}, String(s.cancellations || 0)))),
    d.by_day.length > 1 ? h("div", {}, h("h3", {}, "По днях"), revenueChart(d.by_day)) : null,
    h("h3", {}, "Як платили"),
    barList([["Готівка", pay.cash || 0, pctOf(pay.cash || 0, s.revenue)], ["Картка", pay.card || 0, pctOf(pay.card || 0, s.revenue)],
      ...(pay.transfer ? [["Переказ", pay.transfer, pctOf(pay.transfer, s.revenue)]] : [])], s.revenue),
    masters.length ? h("h3", {}, "По майстрах") : null,
    masters.length ? barList(masters.map((m) => [m.master_name, m.revenue, `${m.orders_count} замовл. · ${pctOf(m.revenue, s.revenue)}`]), s.revenue) : null,
    services.length ? h("h3", {}, "Найприбутковіші послуги") : null,
    services.length ? barList(services.slice(0, 7).map((x) => [x.service_name, x.revenue, `${x.orders_count} шт · ${pctOf(x.revenue, s.revenue)}`]), services[0].revenue) : null,
    h("a", { class: "btn", href: "#/orders" }, icon("receipt", "sm"), "Усі замовлення")));
}
function expensesSheet({ s, p, vs, exp, expRows, opEx }) {
  const rows = expRows.filter((r) => r[1]).sort((a, b) => b[1] - a[1]);
  openSheet("Витрати салону", h("div", { class: "stack detail" },
    sheetHead(opEx, p.operating_expenses, vs, s.from, s.to),
    rows.length ? barList(rows.map(([l, v, , href]) => [l, v, pctOf(v, opEx), href]), opEx) : emptyState("За період витрат немає", "wallet"),
    exp.master_charges ? h("div", { class: "sum-line" }, h("span", {}, "Компенсували майстри (комунальні/генератор)"), h("b", { class: "pos" }, "+ " + money(exp.master_charges))) : null,
    h("div", { class: "card stack", style: { boxShadow: "none" } },
      h("div", { class: "sum-line muted small" }, h("span", {}, "Закупівля матеріалів (гроші, не витрата періоду)"), h("span", {}, money(exp.materials_purchases))),
      h("div", { class: "sum-line muted small" }, h("span", {}, "Закупівля пального"), h("span", {}, money(exp.fuel_purchases || 0))),
      h("div", { class: "sum-line muted small" }, h("span", {}, "Матеріали, списані в замовленнях"), h("span", {}, money(s.materials)))),
    h("div", { class: "row" }, h("a", { class: "btn", href: "#/expenses" }, icon("wallet", "sm"), "Витрати"),
      h("a", { class: "btn", href: "#/utilities" }, icon("gauge", "sm"), "Комунальні і генератор"))));
}
function profitSheet({ s, p, vs, opEx, exp }) {
  const parts = [["Майстрам", s.masters_share, "c-masters"], ["Матеріали", s.materials, "c-mat"], ["Витрати", Math.max(0, opEx - (exp.master_charges || 0)), "c-exp"],
    ["Прибуток", Math.max(0, s.net_profit), "c-profit"]];
  const total = Math.max(1, parts.reduce((a, x) => a + x[1], 0));
  openSheet("Прибуток: куди йдуть гроші", h("div", { class: "stack detail" },
    sheetHead(s.net_profit, p.net_profit, vs, s.from, s.to),
    h("div", { class: "stack-bar" }, parts.filter((x) => x[1]).map(([l, v, c]) => h("i", { class: c, style: { width: (v / total) * 100 + "%" }, title: `${l}: ${money(v)}` }))),
    h("div", { class: "legend-list" }, parts.map(([l, v, c]) => h("div", {}, h("span", { class: "dot " + c }), h("span", { class: "grow" }, l), h("b", {}, money(v)), h("small", { class: "muted" }, pctOf(v, s.revenue))))),
    h("div", { class: "card", style: { boxShadow: "none" } },
      h("div", { class: "sum-line" }, h("span", {}, "Виручка"), h("b", {}, money(s.revenue))),
      h("div", { class: "sum-line" }, h("span", { class: "muted" }, "− Частка майстрів"), h("span", {}, money(s.masters_share))),
      h("div", { class: "sum-line" }, h("span", { class: "muted" }, "− Матеріали"), h("span", {}, money(s.materials))),
      h("div", { class: "sum-line total" }, h("span", {}, "Валовий прибуток"), h("span", {}, money(s.gross_profit))),
      h("div", { class: "sum-line" }, h("span", { class: "muted" }, "− Витрати"), h("span", {}, money(opEx))),
      exp.master_charges ? h("div", { class: "sum-line" }, h("span", { class: "muted" }, "+ Компенсація майстрів"), h("span", {}, money(exp.master_charges))) : null,
      h("div", { class: "sum-line total" }, h("span", {}, "Чистий прибуток"), h("span", { class: s.net_profit < 0 ? "neg" : "" }, money(s.net_profit)))),
    s.revenue ? h("p", { class: "muted small" }, `Рентабельність: ${pctOf(s.net_profit, s.revenue)} — стільки з кожної гривні виручки лишається салону.`) : null));
}
function checkSheet({ s, p, vs, d }) {
  const rows = d.by_master.filter((m) => m.orders_count).map((m) => [m.master_name, Math.round(m.revenue / m.orders_count), `${m.orders_count} замовл.`]).sort((a, b) => b[1] - a[1]);
  const svc = d.by_service.filter((x) => x.orders_count).map((x) => [x.service_name, Math.round(x.revenue / x.orders_count), `${x.orders_count} шт`]).sort((a, b) => b[1] - a[1]).slice(0, 7);
  openSheet("Середній чек", h("div", { class: "stack detail" },
    sheetHead(s.average_check, p.average_check, vs, s.from, s.to),
    rows.length ? h("h3", {}, "По майстрах") : null, rows.length ? barList(rows) : null,
    svc.length ? h("h3", {}, "Найдорожчі послуги (середня ціна)") : null, svc.length ? barList(svc) : null));
}

// Аналітика і прогноз (директор): прогноз місяця, середній чек по майстрах, клієнти, дні тижня
const MONTHS_IN = ["січні", "лютому", "березні", "квітні", "травні", "червні", "липні", "серпні", "вересні", "жовтні", "листопаді", "грудні"];
function changeChip(bp) {
  if (bp === null || bp === undefined) return h("span", { class: "muted small" }, "немає даних для порівняння");
  const up = bp >= 0;
  return h("span", { class: "chip " + (up ? "st-completed" : "st-cancelled") }, `${up ? "▲ +" : "▼ "}${(bp / 100).toLocaleString("uk-UA", { maximumFractionDigits: 1 })}%`);
}
function insightsBlock(ins) {
  const f = ins.forecast;
  const month = MONTHS_IN[+f.month_from.slice(5, 7) - 1];
  const pct = (x) => (f.forecast ? Math.min(100, (x / f.forecast) * 100) : 0);
  const forecastCard = h("div", { class: "card forecast-card" },
    h("div", { class: "card-head" }, h("h3", {}, `Прогноз виручки в ${month}`), h("span", { class: "muted small" }, `день ${f.days_elapsed} з ${f.days_total}`)),
    h("div", { class: "kpi-value big" }, money(f.forecast)),
    h("div", { class: "forecast-bar", role: "img", "aria-label": `Факт ${money(f.actual)}, записано наперед ${money(f.booked)}, прогноз ${money(f.forecast)}` },
      h("div", { class: "fact", style: { width: pct(f.actual) + "%" } }),
      h("div", { class: "booked", style: { width: pct(f.booked) + "%" } })),
    h("div", { class: "row small", style: { flexWrap: "wrap", gap: "14px" } },
      h("span", { class: "row" }, h("i", { class: "dot fact" }), "Факт: ", h("b", {}, money(f.actual))),
      h("span", { class: "row" }, h("i", { class: "dot booked" }), "Записано наперед: ", h("b", {}, money(f.booked)), ` (${f.booked_count})`)),
    h("div", { class: "sum-line" }, h("span", { class: "muted" }, "За поточним темпом"), h("span", {}, money(f.run_rate))),
    h("div", { class: "sum-line" }, h("span", { class: "muted" }, "Середня виручка за день"), h("span", {}, money(f.daily_average))),
    h("div", { class: "sum-line" }, h("span", { class: "muted" }, "Комунальні, генератор, реклама, інше"), h("span", {}, "− " + money(f.operating_expenses))),
    h("div", { class: "sum-line total" }, h("span", {}, "Прогноз чистого прибутку"), h("span", { class: f.forecast_net < 0 ? "neg" : "" }, money(f.forecast_net))),
    h("p", { class: "muted small" }, "Прогноз — більше з двох: «за темпом» або «факт + записи наперед». Прибуток — за структурою цього місяця (частки майстрів і матеріали)."));

  const c = ins.compare;
  const maxAvg = Math.max(1, ...ins.masters.map((m) => m.average_check));
  const checkCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", {}, "Середній чек"), changeChip(c.average_check.change_bp)),
    h("div", { class: "kpi-value" }, money(c.average_check.current)),
    h("div", { class: "muted small", style: { marginBottom: "10px" } }, c.average_check.previous
      ? `було ${money(c.average_check.previous)} · ${dmy(ins.previous.from)} — ${dmy(ins.previous.to)}` : "за попередній період замовлень не було"),
    ins.masters.filter((m) => m.orders).map((m) => h("div", { class: "stack", style: { gap: "4px", marginBottom: "8px" } },
      h("div", { class: "sum-line", style: { padding: 0 } }, h("span", {}, m.master_name, h("span", { class: "muted small" }, ` · ${m.orders} замовл.`)), h("b", {}, money(m.average_check))),
      h("div", { class: "progress" }, h("div", { style: { width: (m.average_check / maxAvg) * 100 + "%" } })))));

  const cl = ins.clients;
  const clientsCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", {}, "Клієнти"), changeChip(c.clients_count.change_bp)),
    h("div", { class: "grid", style: { gridTemplateColumns: "1fr 1fr", gap: "10px" } },
      h("div", {}, h("div", { class: "kpi-value" }, String(cl.new)), h("div", { class: "muted small" }, "нових")),
      h("div", {}, h("div", { class: "kpi-value" }, String(cl.returning)), h("div", { class: "muted small" }, "повернулися"))),
    cl.total ? h("div", { class: "forecast-bar", style: { marginTop: "10px" } },
      h("div", { class: "fact", style: { width: (cl.returning / cl.total) * 100 + "%" } }), h("div", { class: "booked", style: { width: (cl.new / cl.total) * 100 + "%" } })) : null,
    h("p", { class: "muted small" }, cl.total ? `Повторних візитів у періоді: ${cl.repeat_in_period}. Частка постійних: ${Math.round((cl.returning / cl.total) * 100)}%.` : "Немає візитів за період."));

  const WDN = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"];
  const maxWd = Math.max(1, ...ins.weekdays.map((w) => w.average));
  const best = ins.weekdays.reduce((a, w) => (w.average > a.average ? w : a), ins.weekdays[0]);
  const weekCard = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", {}, "Дні тижня"), best.average ? h("span", { class: "muted small" }, `найкращий — ${WDN[best.weekday]}`) : null),
    h("div", { class: "wd-bars" }, ins.weekdays.map((w) => h("div", { class: "wd" },
      h("span", { class: "v" }, w.average ? Math.round(w.average / 100).toLocaleString("uk-UA") : ""),
      h("div", { class: "bar" + (w === best && w.average ? " best" : ""), style: { height: Math.max(4, (w.average / maxWd) * 110) + "px" } }),
      h("span", { class: "l" }, WDN[w.weekday])))),
    h("p", { class: "muted small" }, "Середня виручка за один такий день у періоді, грн."));

  return h("div", { class: "stack", style: { marginTop: "12px" } },
    h("div", { class: "section", style: { marginTop: "4px" } }, h("h2", {}, "Аналітика і прогноз")),
    h("div", { class: "grid two" }, forecastCard, checkCard, clientsCard, weekCard));
}

// ---------- Комунальні і генератор (директор) / Генератор (персонал)
const MONTHS_UA = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень", "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"];
const monthTitle = (iso) => `${MONTHS_UA[+iso.slice(5, 7) - 1]} ${iso.slice(0, 4)}`;
const addMonths = (iso, n) => { const d = new Date(iso.slice(0, 7) + "-01T12:00"); d.setMonth(d.getMonth() + n); return isoDate(d).slice(0, 7) + "-01"; };
const fmtH = (h) => { const m = Math.round(h * 60); return `${Math.floor(m / 60)} год ${m % 60} хв`; };

// Генератор: мотогодини вносяться, як показники комунальних (з лічильника генератора або кількістю годин).
// Вартість мотогодини = витрата бензину × ціна + амортизація (задаються в «Налаштуваннях»).
const num = (v) => parseFloat(String(v).replace(",", "."));
function hourCostText(st) {
  return st.hour_cost ? `${money(st.hour_cost)} за мотогодину = ${fmtNum(st.fuel_lph_milli / 1000)} л × ${money(st.fuel_price)} + амортизація ${money(st.amort_per_hour)}` : "вартість мотогодини не задано";
}
function generatorHoursForm(g, onDone) {
  let mode = "meter";
  const val = h("input", { inputmode: "decimal", placeholder: "напр. 125,5" });
  const on = h("input", { type: "date", value: today(), max: today() });
  const note = h("input", { placeholder: "необов'язково (напр. «відключення 14:00–18:00»)" });
  const lbl = h("span", {}, "Показник лічильника мотогодин");
  const hint = h("p", { class: "muted small" });
  const preview = h("div", { class: "card gen-preview" });
  const st = g.settings;
  const calc = () => {
    const x = num(val.value);
    const hrs = mode === "meter" ? x - g.motohours : x;
    hint.textContent = mode === "meter" ? `Попередній показник: ${fmtNum(g.motohours)} мотогод. Різниця = відпрацьовано.` : "Скільки мотогодин генератор пропрацював (можна дробове: 2,5).";
    if (!(hrs > 0)) { preview.replaceChildren(h("span", { class: "muted small" }, Number.isFinite(x) && mode === "meter" ? "Показник має бути більший за попередній" : "Вкажіть значення — тут з'явиться розрахунок")); return; }
    const fuel = hrs * st.fuel_lph_milli / 1000;
    const fuelCost = Math.round(fuel * st.fuel_price), amort = Math.round(hrs * st.amort_per_hour);
    preview.replaceChildren(
      h("div", { class: "sum-line" }, h("span", {}, "Відпрацьовано"), h("b", {}, `${fmtNum(Math.round(hrs * 1000) / 1000)} мотогод.`)),
      h("div", { class: "sum-line" }, h("span", {}, `Бензин ${fmtNum(Math.round(fuel * 100) / 100)} л × ${money(st.fuel_price)}`), h("span", {}, money(fuelCost))),
      h("div", { class: "sum-line" }, h("span", {}, `Амортизація ${money(st.amort_per_hour)} × мотогод.`), h("span", {}, money(amort))),
      h("div", { class: "sum-line total" }, h("span", {}, "Разом витрати"), h("b", {}, money(fuelCost + amort))));
  };
  val.addEventListener("input", calc);
  const modeSeg = seg([["meter", "Показник лічильника"], ["hours", "Відпрацьовано годин"]], mode, (v) => { mode = v; lbl.textContent = v === "meter" ? "Показник лічильника мотогодин" : "Відпрацьовано мотогодин"; val.value = ""; calc(); });
  calc();
  openSheet("Внести мотогодини", h("div", { class: "stack" },
    modeSeg,
    h("div", { class: "form-grid" }, h("label", { class: "f" }, lbl, val), field("Дата", on)),
    hint, preview, field("Примітка", note),
    !g.configured ? h("p", { class: "muted small" }, "Вартість мотогодини не задано — витрати будуть 0 ₴. Задайте витрату бензину й амортизацію в «Налаштуваннях».") : null,
    h("button", { class: "btn primary big", onclick: async () => {
      const x = val.value.trim().replace(",", ".");
      if (!x) return toast("Вкажіть значення", true);
      await run(() => api.post("/api/generator/hours", { on: on.value, note: note.value.trim() || null, [mode === "meter" ? "current" : "hours"]: x }), "Мотогодини внесено");
      closeSheet(); onDone();
    } }, "Зберегти")));
  autoFocus(val);
}
function generatorCard(g, onChange) {
  return h("div", { class: "card gen-card" },
    h("div", { class: "row", style: { alignItems: "center", gap: "14px", flexWrap: "wrap" } },
      h("span", { class: "gen-ico" }, icon("gauge")),
      h("div", { class: "grow" }, h("div", { class: "muted small" }, "Лічильник мотогодин"),
        h("div", { class: "kpi-value" }, `${fmtNum(g.motohours)} мотогод.`),
        h("div", { class: "muted small" }, hourCostText(g.settings))),
      h("button", { class: "btn primary", onclick: () => generatorHoursForm(g, onChange) }, icon("plus", "sm"), "Внести мотогодини")),
    !g.configured ? h("div", { class: "callout", style: { marginTop: "10px", marginBottom: 0 } }, icon("alert"),
      h("div", { class: "small" }, "Задайте витрату бензину, ціну й амортизацію в ", h("a", { href: "#/settings" }, "«Налаштуваннях»"), " — інакше вартість буде 0 ₴.")) : null);
}
// стовпчики по місяцях: мотогодини й вартість
function genHistoryChart(hist) {
  const max = Math.max(1, ...hist.map((x) => x.total));
  return h("div", { class: "gen-bars" }, hist.map((x) => h("div", { class: "gen-bar", title: `${monthTitle(x.month)}: ${fmtNum(x.hours)} мотогод., ${fmtNum(x.fuel_l)} л, ${money(x.total)}` },
    h("span", { class: "v" }, x.total ? money(x.total) : "—"),
    h("div", { class: "col" }, h("i", { class: "fuel", style: { height: (x.fuel_cost / max) * 100 + "%" } }), h("i", { class: "amort", style: { height: (x.amort_cost / max) * 100 + "%" } })),
    h("b", {}, MONTHS_UA[+x.month.slice(5, 7) - 1].slice(0, 3)), h("span", { class: "muted" }, `${fmtNum(x.hours)} год`))));
}
function genRunsTable(runs, onEdit) {
  return runs.length ? h("div", { class: "table-wrap" }, h("table", {},
    h("tr", {}, h("th", {}, "Дата"), h("th", { class: "num" }, "Мотогод."), h("th", { class: "num" }, "Бензин"), h("th", { class: "num m-hide" }, "Амортизація"), h("th", { class: "num" }, "Разом"), h("th", { class: "m-hide" }, "Примітка"), onEdit ? h("th", {}, "") : null),
    runs.map((x) => h("tr", {}, h("td", {}, dmy(x.started_at), x.corrected ? h("span", { class: "chip plain", style: { marginLeft: "6px" } }, "виправлено") : null),
      h("td", { class: "num" }, fmtNum(x.hours)), h("td", { class: "num" }, `${fmtNum(x.fuel_l)} л · ${money(x.fuel_cost)}`), h("td", { class: "num" }, money(x.amort_cost)),
      h("td", { class: "num" }, h("b", {}, money(x.total_cost))), h("td", { class: "small muted" }, x.note || ""),
      onEdit ? h("td", { class: "right" }, x.running ? null : h("button", { class: "btn sm ghost", "aria-label": "Виправити", onclick: () => onEdit(x) }, icon("edit", "sm"))) : null))))
    : emptyState("Цього місяця генератор не використовувався", "gauge");
}
function twofaSettingsCard(st) {
  const roles = st.twofa_roles || [];
  const dir = h("input", { type: "checkbox", checked: roles.includes("director") });
  const adm = h("input", { type: "checkbox", checked: roles.includes("admin") });
  return h("div", { class: "card stack" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("lock"), "Безпека входу"),
    h("p", { class: "muted small", style: { margin: 0 } }, "Після правильного пароля на новому пристрої — 6-значний код у Telegram (дійсний 5 хв). Пристрій можна запам'ятати на 30 днів. Працює, якщо в людини підключено Telegram."),
    h("label", { class: "row" }, dir, "Директор"), h("label", { class: "row" }, adm, "Адміністратор"),
    h("button", { class: "btn primary", onclick: async () => {
      const v = [dir.checked ? "director" : null, adm.checked ? "admin" : null].filter(Boolean);
      state.settings = await run(() => api.put("/api/settings", { twofa_roles: v }), "Збережено"); reload();
    } }, "Зберегти"));
}
function generatorSettingsCard(st) {
  const lph = h("input", { inputmode: "decimal", value: (st.generator_fuel_lph_milli || 0) / 1000 });
  const price = h("input", { inputmode: "decimal", value: kopToInput(st.generator_fuel_price || 0) });
  const amort = h("input", { inputmode: "decimal", value: kopToInput(st.generator_amort_per_hour || 0) });
  const out = h("p", { class: "muted small" });
  const calc = () => {
    const c = Math.round((num(lph.value) || 0) * (toKop(price.value) || 0)) + (toKop(amort.value) || 0);
    out.textContent = `Вартість мотогодини: ${money(c)}. Нові значення діють для наступних внесень — минулі не перераховуються.`;
  };
  [lph, price, amort].forEach((x) => x.addEventListener("input", calc));
  calc();
  return h("div", { class: "card stack" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("gauge"), "Генератор"),
    h("div", { class: "form-grid" }, field("Витрата бензину, л за мотогодину", lph), field("Ціна бензину, ₴/л", price), field("Амортизація, ₴ за мотогодину", amort)),
    out,
    h("button", { class: "btn primary", onclick: async () => {
      const body = { generator_fuel_lph_milli: Math.round((num(lph.value) || 0) * 1000), generator_fuel_price: toKop(price.value) || 0, generator_amort_per_hour: toKop(amort.value) || 0 };
      state.settings = await run(() => api.put("/api/settings", body), "Налаштування генератора збережено"); reload();
    } }, "Зберегти"));
}

VIEWS.generator = async (main) => {
  // адміністратор: внести мотогодини і переглянути записи місяця
  main.append(topbar("Генератор", "Внесіть мотогодини, якщо генератор працював — витрати порахуються самі"));
  const month = today().slice(0, 7) + "-01";
  const [g, runs] = await Promise.all([api.get("/api/generator"), api.get("/api/generator/runs?month=" + month)]);
  main.append(generatorCard(g, reload), h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Записи за " + monthTitle(month).toLowerCase())), genRunsTable(runs, null)));
};

VIEWS.utilities = async (main) => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const month = params.get("m") || today().slice(0, 7) + "-01";
  const go = (m) => { location.hash = "#/utilities?m=" + m; };
  main.append(topbar("Комунальні і генератор", monthTitle(month),
    h("button", { class: "btn", "aria-label": "Попередній місяць", onclick: () => go(addMonths(month, -1)) }, icon("left", "sm")),
    h("button", { class: "btn", onclick: () => go(today().slice(0, 7) + "-01") }, "Цей місяць"),
    h("button", { class: "btn", "aria-label": "Наступний місяць", onclick: () => go(addMonths(month, 1)) }, icon("right", "sm"))));
  const [g, sum, readings, runs, prev, charges, hist] = await Promise.all([
    api.get("/api/generator"), api.get("/api/utilities/summary?month=" + month), api.get("/api/utilities/readings"),
    api.get("/api/generator/runs?month=" + month), api.get("/api/utilities/allocation?month=" + month), api.get("/api/utilities/charges?month=" + month),
    api.get("/api/generator/history?months=6").catch(() => [])]);
  const st = state.settings || {};

  // ---- підсумки місяця
  const e = sum.electricity, gen = sum.generator;
  const kpis = h("div", { class: "grid kpi", style: { marginTop: "12px" } },
    kpi("Електроенергія", e.cost, "", { ico: "zap", sub: `${fmtNum(e.kwh)} кВт·год${e.has_reading && !e.confirmed ? " · не підтверджено" : ""}` }),
    kpi("Генератор", gen.total, "", { ico: "gauge", sub: `${fmtNum(gen.hours)} мотогод. · ${fmtNum(gen.fuel_l)} л` }),
    kpi("Разом витрати", sum.total, "dark", { ico: "wallet", sub: sum.charged_to_masters ? `з майстрів ${money(sum.charged_to_masters)}` : "" }),
    kpi("Вплив на чистий прибуток", sum.net_impact, "accent", { ico: "trend", sub: "витрати мінус компенсація майстрів" }));
  const detail = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Деталі місяця")),
    h("div", { class: "sum-line" }, h("span", {}, "Електроенергія"), h("span", {}, `${fmtNum(e.kwh)} кВт·год × ${e.tariff ? money(e.tariff) : "—"}`), h("b", {}, money(e.cost))),
    e.paid !== null ? h("div", { class: "sum-line muted" }, h("span", {}, "Фактично сплачено"), h("span", {}, money(e.paid))) : null,
    h("div", { class: "sum-line" }, h("span", {}, "Бензин"), h("span", {}, fmtNum(gen.fuel_l) + " л"), h("b", {}, money(gen.fuel_cost))),
    h("div", { class: "sum-line" }, h("span", {}, "Амортизація генератора"), h("span", {}, fmtNum(gen.hours) + " мотогод."), h("b", {}, money(gen.amort_cost))),
    h("div", { class: "sum-line total" }, h("span", {}, "Разом"), h("b", {}, money(sum.total))),
    sum.by_master.map((m) => h("div", { class: "sum-line" }, h("span", { class: "muted" }, "− списано: " + m.master_name), h("span", {}, money(m.amount)))),
    sum.other_utilities ? h("p", { class: "muted small" }, `Інші комунальні витрати (внесені вручну): ${money(sum.other_utilities)}`) : null,
    null);

  // ---- електроенергія
  const cur = readings.find((r) => r.month === month);
  const elecCard = h("div", { class: "card stack" }, h("div", { class: "card-head" }, h("h3", {}, "Лічильник електроенергії"),
    cur ? h("span", { class: "chip " + (cur.confirmed ? "st-completed" : "st-pending") }, cur.confirmed ? "підтверджено" : "не підтверджено") : null),
    cur ? h("div", { class: "stack" },
      h("div", { class: "sum-line" }, h("span", {}, "Показники"), h("b", {}, `${fmtNum(cur.prev)} → ${fmtNum(cur.curr)} = ${fmtNum(cur.used)} кВт·год`)),
      h("div", { class: "sum-line" }, h("span", {}, "Тариф"), h("b", {}, money(cur.tariff) + " / кВт·год")),
      h("div", { class: "sum-line total" }, h("span", {}, "Вартість"), h("b", {}, money(cur.cost))),
      cur.paid_amount !== null ? h("div", { class: "sum-line" }, h("span", {}, "Сплачено"), h("b", {}, money(cur.paid_amount)),
        cur.difference ? h("span", { class: "muted small" }, cur.difference > 0 ? ` переплата ${money(cur.difference)}` : ` борг ${money(-cur.difference)}`) : null) : null,
      h("div", { class: "row" },
        !cur.confirmed ? h("button", { class: "btn primary", onclick: () => confirmSheet("Підтвердити електроенергію?", `${money(cur.cost)} стане витратою «Комунальні» за ${monthTitle(month).toLowerCase()}.`, "Підтвердити",
          async () => { await run(() => api.post(`/api/utilities/readings/${cur.id}/confirm`), "Підтверджено"); reload(); }) }, "Підтвердити") : null,
        h("button", { class: "btn", onclick: () => readingForm(cur) }, icon("edit", "sm"), "Редагувати")))
      : h("div", { class: "stack" }, h("p", { class: "muted" }, "Показники за цей місяць ще не внесено."),
        h("button", { class: "btn primary", onclick: () => readingForm(null) }, icon("plus", "sm"), "Внести показники")));
  function readingForm(r) {
    const last = readings.find((x) => x.month < month);
    const prevIn = h("input", { inputmode: "decimal", value: r ? r.prev : (last ? last.curr : ""), placeholder: "кВт·год" });
    const currIn = h("input", { inputmode: "decimal", value: r ? r.curr : "", placeholder: "кВт·год" });
    const tariffIn = h("input", { inputmode: "decimal", value: r ? kopToInput(r.tariff) : (last ? kopToInput(last.tariff) : ""), placeholder: "напр. 4,32" });
    const readOn = h("input", { type: "date", value: r ? r.read_on : today() });
    const paid = h("input", { inputmode: "decimal", value: r && r.paid_amount !== null ? kopToInput(r.paid_amount) : "", placeholder: "можна пізніше" });
    const reason = h("input", { placeholder: "обов'язково, якщо місяць підтверджено" });
    const preview = h("div", { class: "sum-line total" });
    const calc = () => {
      const used = parseFloat(String(currIn.value).replace(",", ".")) - parseFloat(String(prevIn.value).replace(",", "."));
      const t = toKop(tariffIn.value);
      preview.replaceChildren(h("span", {}, used >= 0 ? `${fmtNum(used)} кВт·год` : "Перевірте показники"), h("b", {}, used >= 0 && t > 0 ? money(Math.round(used * t)) : "—"));
    };
    [prevIn, currIn, tariffIn].forEach((x) => x.addEventListener("input", calc));
    calc();
    openSheet(r ? "Показники: " + monthTitle(month) : "Нові показники: " + monthTitle(month), h("div", { class: "stack" },
      h("div", { class: "form-grid" }, field("Попередній показник", prevIn), field("Поточний показник", currIn)),
      h("div", { class: "form-grid" }, field("Ціна за 1 кВт·год, ₴", tariffIn), field("Дата зняття", readOn)),
      preview, field("Фактично сплачено, ₴", paid), r && r.confirmed ? field("Причина виправлення", reason) : null,
      h("button", { class: "btn primary big", onclick: async () => {
        const body = { prev: prevIn.value.replace(",", "."), curr: currIn.value.replace(",", "."), tariff: toKop(tariffIn.value), read_on: readOn.value,
          paid_amount: paid.value.trim() ? toKop(paid.value) : null };
        if (!(body.tariff > 0)) return toast("Вкажіть ціну за 1 кВт·год", true);
        if (r) await run(() => api.patch(`/api/utilities/readings/${r.id}`, { ...body, reason: reason.value.trim() || null }), "Збережено");
        else await run(() => api.post("/api/utilities/readings", { ...body, month }), "Показники внесено");
        closeSheet(); reload();
      } }, "Зберегти")));
  }

  // ---- генератор: детально
  const f = g.forecast;
  const genCard = h("div", { class: "card stack" },
    h("div", { class: "card-head" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("gauge"), "Генератор: " + monthTitle(month).toLowerCase())),
    h("div", { class: "gen-stats" },
      h("div", {}, h("span", {}, "Мотогодини"), h("b", {}, fmtNum(gen.hours))),
      h("div", {}, h("span", {}, "Бензин"), h("b", {}, `${fmtNum(gen.fuel_l)} л`), h("small", {}, money(gen.fuel_cost))),
      h("div", {}, h("span", {}, "Амортизація"), h("b", {}, money(gen.amort_cost))),
      h("div", {}, h("span", {}, "Разом"), h("b", {}, money(gen.total))),
      h("div", {}, h("span", {}, "Записів"), h("b", {}, String(gen.runs))),
      gen.hours ? h("div", {}, h("span", {}, "Середня мотогодина"), h("b", {}, money(Math.round(gen.total / gen.hours)))) : null),
    f && f.hours_so_far && month === today().slice(0, 7) + "-01" ? h("p", { class: "muted small" }, `Прогноз на місяць: ${fmtNum(f.projected_hours)} мотогод. ≈ ${money(f.projected_cost)}.`) : null,
    hist.length ? h("div", {}, h("div", { class: "row small muted", style: { justifyContent: "space-between" } }, h("span", {}, "Останні 6 місяців"),
      h("span", { class: "row", style: { gap: "10px" } }, h("span", { class: "lg fuel" }, "бензин"), h("span", { class: "lg amort" }, "амортизація"))), genHistoryChart(hist)) : null,
    h("h3", { style: { marginTop: "6px" } }, "Записи мотогодин"),
    genRunsTable(runs, (x) => runForm(x)));
  function runForm(x) {
    const hours = h("input", { inputmode: "decimal", value: x.hours, placeholder: "напр. 2,5" });
    const fuelPrice = h("input", { inputmode: "decimal", value: kopToInput(x.fuel_price) });
    const fuelCost = h("input", { inputmode: "decimal", placeholder: "лише якщо відома точна сума (чек АЗС)" });
    const reason = h("input", { placeholder: "обов'язково" });
    openSheet("Виправити запис генератора", h("div", { class: "stack" },
      field("Мотогодини", hours),
      h("div", { class: "form-grid" }, field("Ціна бензину, ₴/л", fuelPrice), field("Бензин, ₴ (вручну)", fuelCost)),
      field("Причина", reason),
      h("button", { class: "btn primary big", onclick: async () => {
        const body = { hours: hours.value.replace(",", ".") || null, reason: reason.value.trim() };
        if (fuelPrice.value.trim()) body.fuel_price = toKop(fuelPrice.value);
        if (fuelCost.value.trim()) body.fuel_cost = toKop(fuelCost.value);
        await run(() => api.patch(`/api/generator/runs/${x.id}`, body), "Запис виправлено");
        closeSheet(); reload();
      } }, "Зберегти")));
  }

  // ---- розподіл між майстрами
  const mode = st.utilities_charge_mode || "none";
  const split = select([["revenue", "пропорційно виручці"], ["equal", "порівну"]], st.utilities_split || "revenue");
  const share = h("input", { type: "number", min: 0, max: 100, step: 1, value: (st.utilities_charge_share_bp ?? 10000) / 100 });
  const fixedAmt = h("input", { inputmode: "decimal", value: kopToInput(st.utilities_fixed_amount || 0) });
  const fixedPer = select([["shift", "за зміну"], ["month", "за місяць"]], st.utilities_fixed_per || "shift");
  let newMode = mode;
  const modeBox = h("div", { class: "stack" });
  const drawMode = () => modeBox.replaceChildren(...[
    newMode === "month_end" ? h("div", { class: "form-grid" }, field("Як ділити", split), field("Яку частку витрат розподіляти, %", share)) : null,
    newMode === "fixed" ? h("div", { class: "form-grid" }, field("Сума з майстра, ₴", fixedAmt), field("Списувати", fixedPer)) : null,
    newMode === "fixed" ? h("p", { class: "muted small" }, "Сума автоматично віднімається від заробітку майстра при закритті зміни (або раз на місяць — при першій закритій зміні місяця).") : null,
    newMode === "month_end" ? h("p", { class: "muted small" }, "Після завершення місяця натисніть «Розподілити» — сума електроенергії й генератора поділиться між майстрами.") : null,
  ].filter(Boolean));
  drawMode();
  const ended = addMonths(month, 1) <= today();
  const allocCard = h("div", { class: "card stack" }, h("h3", {}, "Розподіл витрат між майстрами"),
    seg([["none", "Не списувати"], ["month_end", "Наприкінці місяця"], ["fixed", "Фіксована ставка"]], mode, (v) => { newMode = v; drawMode(); }),
    modeBox,
    h("button", { class: "btn primary", onclick: async () => {
      const body = { utilities_charge_mode: newMode, utilities_split: split.value, utilities_charge_share_bp: Math.round(parseFloat(share.value || "0") * 100),
        utilities_fixed_amount: toKop(fixedAmt.value) || 0, utilities_fixed_per: fixedPer.value };
      state.settings = await run(() => api.put("/api/settings", body), "Спосіб розподілу збережено"); reload();
    } }, "Зберегти спосіб розподілу"),
    mode === "month_end" ? h("div", { class: "stack", style: { borderTop: "1px solid var(--line)", paddingTop: "12px" } },
      h("b", {}, `${monthTitle(month)}: до розподілу ${money(prev.to_split)}`),
      prev.rows.map((r) => h("div", { class: "sum-line" }, h("span", {}, r.master_name, h("span", { class: "muted small" }, ` · виручка ${money(r.revenue)}`)), h("b", {}, money(r.amount)))),
      prev.already_charged ? h("p", { class: "muted small" }, `За цей місяць уже списано ${money(prev.already_charged)}.`) : null,
      !prev.costs.electricity_confirmed ? h("p", { class: "muted small" }, "Електроенергію за місяць ще не підтверджено — вона не увійде в розподіл.") : null,
      h("div", { class: "row" },
        !prev.already_charged ? h("button", { class: "btn primary", disabled: !ended || !prev.to_split || null, onclick: () => confirmSheet("Розподілити витрати?",
          `${money(prev.to_split)} буде списано з майстрів за ${monthTitle(month).toLowerCase()}. Повторно за цей місяць списати не можна.`, "Розподілити",
          async () => { await run(() => api.post("/api/utilities/allocate", { month }), "Розподілено"); reload(); }) }, ended ? "Розподілити" : "Доступно після завершення місяця") : null,
        prev.already_charged ? h("button", { class: "btn danger", onclick: () => confirmSheet("Скасувати списання за місяць?", "Буде створено сторно; після цього можна розподілити знову.", "Скасувати списання",
          async (reason) => { await run(() => api.post("/api/utilities/reverse", { month, reason }), "Списання скасовано"); reload(); }, { danger: true, withReason: true }) }, "Скасувати списання") : null)) : null,
    charges.length ? h("details", {}, h("summary", { class: "small" }, `Списання за місяць (${charges.length})`),
      charges.map((c) => h("div", { class: "sum-line small" }, h("span", {}, `${dm(c.charged_on)} · ${c.master_name}`, c.reversed ? h("span", { class: "muted" }, " (скасовано)") : null), h("span", {}, money(c.amount))))) : null);

  main.append(generatorCard(g, reload), kpis,
    h("div", { class: "grid two", style: { marginTop: "12px" } }, detail, elecCard),
    h("div", { style: { marginTop: "12px" } }, genCard),
    h("div", { style: { marginTop: "12px" } }, allocCard));
};

// ---------- Зміни й виплати
VIEWS.shifts = async (main) => {
  const [closed, all, payouts] = await Promise.all([api.get("/api/shifts?status=closed"), api.get("/api/shifts"), api.get("/api/payouts")]);
  main.append(topbar("Зміни й виплати", "Каса змін, нарахування майстрам і виплати", h("button", { class: "btn", onclick: () => payoutForm(null) }, icon("plus", "sm"), "Інша виплата / аванс")));
  main.append(h("h2", {}, "Очікують підтвердження виплати"));
  if (!closed.length) main.append(h("div", { class: "card" }, emptyState("Немає закритих змін, що очікують виплати")));
  for (const s of closed) {
    const r = s.report;
    main.append(h("div", { class: "card stack" },
      h("div", { class: "row", style: { alignItems: "flex-start" } }, avatar(s.master_name),
        h("div", { class: "grow" }, h("b", {}, s.master_name),
          h("div", { class: "muted small" }, `${dmy(s.opened_at)} · ${hm(s.opened_at)}–${s.closed_at ? hm(s.closed_at) : ""}`)),
        h("div", { class: "right" }, h("div", { class: "kpi-value" }, money(s.payout_due)), h("div", { class: "muted small" }, r.cash?.mode === "take_from_till" ? "доплатити на карту" : "до виплати"))),
      h("div", { class: "row small" },
        h("span", { class: "chip plain" }, `Клієнтів: ${r.clients_count}`), h("span", { class: "chip plain" }, `Послуг: ${r.services_count}`),
        h("span", { class: "chip plain" }, `Виручка: ${money(r.revenue)}`), r.cancellations ? h("span", { class: "chip st-pending" }, `Скасувань: ${r.cancellations}`) : null),
      r.cash ? h("div", { class: "row small" },
        h("span", { class: "chip st-completed" }, `Заробіток: ${money(r.master_earning)}`),
        r.cash.master_takes ? h("span", { class: "chip st-confirmed" }, `Взяв з каси: ${money(r.cash.master_takes)}`) : null,
        h("span", { class: "chip st-in_progress" }, `У касі лишилось: ${money(r.cash.leave_in_till)}`)) : null,
      h("div", { class: "row" },
        h("button", { class: "btn grow", onclick: () => openShift(s.id) }, icon("receipt", "sm"), "Звіт"),
        h("button", { class: "btn primary grow", onclick: () => payoutForm(s) }, icon("banknote", "sm"), "Виплатити"))));
  }
  main.append(h("h2", { style: { marginTop: "22px" } }, "Усі зміни"));
  main.append(h("div", { class: "card table-wrap" }, h("table", {},
    h("tr", {}, h("th", {}, "Дата"), h("th", {}, "Майстер"), h("th", { class: "num" }, "Виручка"), h("th", { class: "num" }, "До виплати"), h("th", {}, "Статус")),
    all.map((s) => h("tr", { class: "click", onclick: () => openShift(s.id) }, h("td", {}, dmy(s.opened_at)), h("td", {}, s.master_name),
      h("td", { class: "num" }, money(s.report.revenue)), h("td", { class: "num" }, money(s.payout_due)), h("td", {}, chip(s.status, SHIFT_STATUS)))))));
  main.append(h("h2", { style: { marginTop: "22px" } }, "Історія виплат"));
  main.append(payouts.length ? h("div", { class: "card table-wrap" }, h("table", {},
    payouts.map((p) => h("tr", {}, h("td", {}, dmy(p.paid_at), " ", hm(p.paid_at)), h("td", {}, p.master_name), h("td", {}, PAY[p.method]),
      h("td", { class: "muted" }, p.shift_id ? "Зміна #" + p.shift_id : p.note || "Аванс"), h("td", { class: "num" }, money(p.amount)))))) : h("div", { class: "card" }, emptyState("Виплат ще не було")));

  async function openShift(id) {
    const s = await run(() => api.get("/api/shifts/" + id));
    const changed = s.status !== "open" && s.current.payout_due !== s.payout_due;
    openSheet(`Зміна: ${s.master_name}, ${dmy(s.opened_at)}`, shiftReport(s.report),
      changed ? h("p", { class: "muted" }, "Після закриття замовлення виправлялись. Актуальна сума: ", h("b", {}, money(s.current.payout_due))) : null,
      s.payout ? h("p", { class: "pos" }, `Виплачено ${money(s.payout.amount)} — ${dmy(s.payout.paid_at)}`) : null);
  }
  function payoutForm(shift) {
    const mst = select(state.masters.map((m) => [m.id, m.full_name]), shift?.master_id);
    if (shift) mst.disabled = true;
    const amount = h("input", { inputmode: "decimal", value: shift ? kopToInput(shift.payout_due) : "" });
    const method = select(Object.entries(PAY_CHOICES), shift?.report?.cash?.mode === "take_from_till" ? "card" : "cash");
    const note = h("input", { placeholder: shift ? "" : "Аванс, премія…" });
    const btn = h("button", { class: "btn gold big", onclick: async () => {
      btn.disabled = true;
      try {
        await run(() => api.post("/api/payouts", { master_id: +mst.value, amount: toKop(amount.value), shift_id: shift ? shift.id : null, method: method.value, note: note.value || null }), "Виплату підтверджено");
        closeSheet(); reload();
      } catch (_) { btn.disabled = false; }
    } }, "Підтвердити виплату");
    openSheet(shift ? "Виплата за зміну" : "Виплата майстру", h("div", { class: "stack" }, field("Майстер", mst), field("Сума, ₴", amount), field("Спосіб", method), field("Примітка", note), btn));
  }
};

// ---------- Замовлення (директор)
VIEWS.orders = async (main) => {
  const body = h("div");
  const mst = select([["", "Усі майстри"], ...state.masters.map((m) => [m.id, m.full_name])], "", { style: { width: "auto" } });
  const search = h("input", { type: "search", placeholder: "Клієнт, послуга або № замовлення", "aria-label": "Пошук", oninput: () => render() });
  let q = "period=month", pay = "", list = [];
  const paySeg = seg([["", "Усі"], ["cash", "Готівка"], ["card", "Картка"]], "", (v) => { pay = v; render(); });
  const payIco = (m) => (m === "cash" ? "banknote" : m === "card" ? "card" : "transfer");
  const load = async () => {
    body.replaceChildren(h("div", { class: "skeleton" }, h("i"), h("i"), h("i")));
    list = await api.get(`/api/orders?${q}${mst.value ? "&master_id=" + mst.value : ""}`);
    render();
  };
  const render = () => {
    const t = search.value.trim().toLowerCase();
    const rows = list.filter((o) => (!pay || (pay === "cash" ? o.payment_method === "cash" : o.payment_method !== "cash"))
      && (!t || `#${o.id} ${o.id} ${o.client_name} ${o.service_name} ${o.master_name}`.toLowerCase().includes(t)));
    const sum = (f) => rows.reduce((a, o) => a + f(o), 0);
    const cash = sum((o) => (o.payment_method === "cash" ? o.amount : 0));
    const total = sum((o) => o.amount);
    const kpis = h("div", { class: "grid kpi kpi-6" },
      kpi("Замовлень", rows.length, "", { ico: "receipt", raw: true, sub: rows.length ? `середній чек ${money(Math.round(total / rows.length))}` : null }),
      kpi("Виручка", total, "dark", { ico: "trend" }),
      kpi("Готівка", cash, "", { ico: "banknote" }),
      kpi("Картка", total - cash, "", { ico: "card" }),
      kpi("Майстрам", sum((o) => o.master_earning), "", { ico: "coins" }),
      kpi("Салону", sum((o) => o.salon_earning), "accent", { ico: "home" }));
    if (!rows.length) { body.replaceChildren(kpis, h("div", { class: "card" }, emptyState(list.length ? "Нічого не знайдено — змініть пошук чи фільтр" : "Немає замовлень за період", "receipt"))); return; }
    // групуємо за днями (список уже від нових до старих)
    const days = [];
    for (const o of rows) {
      const d = o.closed_at.slice(0, 10);
      if (!days.length || days[days.length - 1].d !== d) days.push({ d, items: [] });
      days[days.length - 1].items.push(o);
    }
    const dayTitle = (d) => `${WD_LONG[(new Date(d + "T12:00").getDay() + 6) % 7]}, ${Number(d.slice(8, 10))} ${MONTHS_GEN[+d.slice(5, 7) - 1]}`;
    const dayTotal = (g) => g.items.reduce((a, o) => a + o.amount, 0);
    // комп'ютер: таблиця з підсумком дня; телефон: картки
    const table = h("div", { class: "card table-wrap only-desk" }, h("table", { class: "orders-table" },
      h("tr", {}, h("th", {}, "Час"), h("th", {}, "Клієнт і послуга"), h("th", {}, "Майстер"), h("th", {}, "Оплата"),
        h("th", { class: "num" }, "Сума"), h("th", { class: "num" }, "Матеріали"), h("th", { class: "num" }, "Майстру"), h("th", { class: "num" }, "Салону")),
      days.map((g) => [
        h("tr", { class: "day-row" }, h("td", { colspan: 4 }, h("b", {}, dayTitle(g.d)), h("span", { class: "muted" }, ` · ${g.items.length} ${plural(g.items.length, "замовлення", "замовлення", "замовлень")}`)),
          h("td", { class: "num" }, h("b", {}, money(dayTotal(g)))), h("td", { colspan: 3 })),
        g.items.map((o) => h("tr", { class: "click", onclick: () => orderSheet(o.id, load) },
          h("td", {}, h("b", {}, hm(o.closed_at)), h("div", { class: "muted small" }, "#", o.id, o.revision > 1 ? " · виправлено" : "")),
          h("td", {}, h("div", {}, o.client_name), h("div", { class: "muted small" }, o.service_name)),
          h("td", {}, h("span", { class: "row", style: { gap: "8px" } }, avatar(o.master_name, "xs"), o.master_name)),
          h("td", {}, h("span", { class: "row", style: { gap: "6px" } }, icon(payIco(o.payment_method), "sm"), PAY[o.payment_method])),
          h("td", { class: "num" }, h("b", {}, money(o.amount))), h("td", { class: "num muted" }, money(o.materials_cost)),
          h("td", { class: "num" }, money(o.master_earning)), h("td", { class: "num" }, money(o.salon_earning))))]),
      h("tr", { class: "total-row" }, h("td", { colspan: 4 }, h("b", {}, `Разом: ${rows.length}`)), h("td", { class: "num" }, h("b", {}, money(total))),
        h("td", { class: "num" }, money(sum((o) => o.materials_cost))), h("td", { class: "num" }, money(sum((o) => o.master_earning))), h("td", { class: "num" }, money(sum((o) => o.salon_earning))))));
    const cards = h("div", { class: "only-mob anim-in" }, days.map((g) => h("div", { class: "ord-day" },
      h("div", { class: "ord-day-head" }, h("span", {}, dayTitle(g.d)), h("b", {}, money(dayTotal(g)))),
      h("div", { class: "card ord-list" }, g.items.map((o) => h("button", { class: "ord-item", onclick: () => orderSheet(o.id, load) },
        h("div", { class: "ord-time" }, hm(o.closed_at)),
        h("div", { class: "grow ord-main" }, h("b", {}, o.client_name), h("div", { class: "muted small" }, o.service_name),
          h("div", { class: "muted small ord-master" }, avatar(o.master_name, "xs"), h("span", {}, o.master_name + (o.revision > 1 ? " · виправлено" : "")))),
        h("div", { class: "ord-amt" }, h("b", {}, money(o.amount)), h("span", { class: "small muted row", style: { gap: "4px" } }, icon(payIco(o.payment_method), "sm"), PAY[o.payment_method]))))))));
    body.replaceChildren(kpis, table, cards);
  };
  mst.addEventListener("change", load);
  main.append(topbar("Замовлення", "Закриті замовлення: оплата, частка майстра і салону"),
    h("div", { class: "filters" }, periodPicker((x) => { q = x; load(); }), h("div", { class: "row filters-2" }, mst, paySeg, h("div", { class: "search-box grow" }, icon("search", "sm"), search))), body);
  await load();
};

async function orderSheet(id, onChange) {
  const o = await run(() => api.get("/api/orders/" + id));
  const amount = h("input", { inputmode: "decimal", value: kopToInput(o.amount) });
  const method = select(Object.entries(o.payment_method === "transfer" ? PAY : PAY_CHOICES), o.payment_method);
  const share = h("input", { type: "number", min: 0, max: 100, step: 0.5, value: o.share_bp / 100 });
  const mode = select(Object.entries(MAT_MODES), o.materials_mode);
  const reason = h("input", { placeholder: "Обов'язково: чому виправляємо" });
  openSheet(`Замовлення #${o.id}`,
    h("div", { class: "stack" },
      h("div", {}, h("b", {}, o.service_name), " · ", o.client_name, " · ", o.master_name),
      h("div", { class: "muted small" }, dmy(o.closed_at), " ", hm(o.closed_at), " · редакція ", o.revision),
      h("div", { class: "card" },
        h("div", { class: "sum-line" }, h("span", {}, "Сума"), h("b", {}, money(o.amount))),
        h("div", { class: "sum-line" }, h("span", {}, "Матеріали"), h("span", {}, money(o.materials_cost))),
        o.materials.map((m) => h("div", { class: "sum-line muted small" }, h("span", {}, "· ", m.name, " × ", m.qty), h("span", {}, money(m.total)))),
        h("div", { class: "sum-line" }, h("span", {}, `Майстру (${pct(o.share_bp)}, ${MAT_MODES[o.materials_mode].toLowerCase()})`), h("b", {}, money(o.master_earning))),
        h("div", { class: "sum-line total" }, h("span", {}, "Салону (після матеріалів)"), h("span", {}, money(o.salon_earning)))),
      h("details", {}, h("summary", { class: "muted", style: { cursor: "pointer" } }, "Виправити замовлення (з записом у журнал)"),
        h("div", { class: "stack", style: { marginTop: "10px" } },
          h("div", { class: "form-grid" }, field("Сума, ₴", amount), field("Оплата", method), field("Відсоток майстра", share), field("Матеріали", mode)),
          field("Причина", reason),
          h("button", { class: "btn gold", onclick: async () => {
            if (!reason.value.trim()) return toast("Вкажіть причину", true);
            await run(() => api.patch("/api/orders/" + id, { amount: toKop(amount.value), payment_method: method.value, share_bp: Math.round(parseFloat(share.value) * 100), materials_mode: mode.value, reason: reason.value.trim() }), "Замовлення виправлено");
            closeSheet(); onChange && onChange();
          } }, "Зберегти виправлення"))),
      h("h3", {}, "Історія змін"),
      o.history.map((x) => h("div", { class: "small", style: { padding: "6px 0", borderBottom: "1px solid var(--line)" } },
        h("b", {}, x.action === "close" ? "Закрито" : "Виправлено"), " · ", dmy(x.ts), " ", hm(x.ts), " · ", x.actor,
        x.reason ? h("div", { class: "muted" }, "Причина: ", x.reason) : null,
        x.before ? h("div", { class: "muted" }, `Було: ${money(x.before.amount)} / майстру ${money(x.before.master_earning)} → Стало: ${money(x.after.amount)} / майстру ${money(x.after.master_earning)}`) : null))));
}

// ---------- Витрати
VIEWS.expenses = async (main) => {
  const body = h("div", { style: { marginTop: "12px" } });
  let q = "period=month";
  const load = async () => {
    const list = await api.get("/api/expenses?" + q);
    const sums = {};
    list.forEach((e) => { sums[e.category] = (sums[e.category] || 0) + e.amount; });
    body.replaceChildren(
      h("div", { class: "grid kpi" }, Object.keys(EXP_CAT).map((k) => kpi(EXP_CAT[k], sums[k] || 0))),
      list.length ? h("div", { class: "card table-wrap", style: { marginTop: "12px" } }, h("table", {},
        h("tr", {}, h("th", {}, "Дата"), h("th", {}, "Категорія"), h("th", {}, "Опис"), h("th", { class: "num" }, "Сума"), h("th", {})),
        list.map((e) => h("tr", {}, h("td", {}, dmy(e.spent_on)), h("td", {}, expCat(e.category)), h("td", {}, e.description || ""), h("td", { class: "num" }, money(e.amount)),
          h("td", { class: "right" }, h("button", { class: "btn sm danger", onclick: () => confirmSheet("Видалити витрату?", `${expCat(e.category)} ${money(e.amount)}`, "Видалити",
            async (reason) => { await run(() => api.del(`/api/expenses/${e.id}?reason=${encodeURIComponent(reason)}`), "Видалено"); load(); }, { danger: true, withReason: true }) }, "×"))))))
        : h("div", { class: "card", style: { marginTop: "12px" } }, emptyState("Витрат за період немає")));
  };
  const add = () => {
    const cat = select(Object.entries(EXP_CAT), "utilities");
    const amount = h("input", { inputmode: "decimal" });
    const date = h("input", { type: "date", value: today() });
    const desc = h("input");
    openSheet("Нова витрата", h("div", { class: "stack" }, field("Категорія", cat), field("Сума, ₴", amount), field("Дата", date), field("Опис", desc),
      h("p", { class: "muted small" }, "Закупівля матеріалів показується окремо як грошовий потік: собівартість матеріалів уже враховується в замовленнях."),
      h("button", { class: "btn gold big", onclick: async () => {
        await run(() => api.post("/api/expenses", { category: cat.value, amount: toKop(amount.value), spent_on: date.value, description: desc.value || null }), "Додано");
        closeSheet(); load();
      } }, "Додати")));
  };
  main.append(topbar("Витрати", "Комунальні, реклама, закупівлі та інше — за період", h("button", { class: "btn primary", onclick: add }, icon("plus", "sm"), "Витрата")), periodPicker((x) => { q = x; load(); }), body);
  await load();
};

// Вибір послуг майстра групами: розділ → тип (→ вид). Галочка групи вмикає всі її послуги.
// Нічого не обрано = майстер виконує всі послуги.
function servicesPicker(selected) {
  const services = state.services.filter((s) => s.is_active);
  const groups = new Map();
  for (const s of services) {
    const key = [s.section || "Послуги", s.category || "Інше"].join(" · ");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(s);
  }
  const summary = h("div", { class: "small muted" });
  const refreshSummary = () => {
    summary.textContent = selected.size ? `Обрано послуг: ${selected.size} з ${services.length}` : "Нічого не обрано — майстер виконує всі послуги";
  };
  const blocks = [];
  for (const [key, items] of groups) {
    const groupBox = h("input", { type: "checkbox" });
    const childBoxes = [];
    const syncGroup = () => {
      const n = items.filter((x) => selected.has(x.id)).length;
      groupBox.checked = n === items.length;
      groupBox.indeterminate = n > 0 && n < items.length;
      count.textContent = n ? `${n}/${items.length}` : `${items.length}`;
    };
    const count = h("span", { class: "chip plain" });
    groupBox.addEventListener("change", () => {
      items.forEach((x) => (groupBox.checked ? selected.add(x.id) : selected.delete(x.id)));
      childBoxes.forEach((b) => { b.checked = groupBox.checked; });
      syncGroup(); refreshSummary();
    });
    // підгрупи за видом (напр. Фарбування → Мелірування)
    const bySub = new Map();
    items.forEach((x) => { const k = x.subcategory || ""; if (!bySub.has(k)) bySub.set(k, []); bySub.get(k).push(x); });
    const list = h("div", { style: { padding: "4px 0 6px 28px", display: "grid", gap: "4px" } });
    for (const [sub, xs] of bySub) {
      if (sub) list.append(h("div", { class: "small muted", style: { marginTop: "4px", fontWeight: 700 } }, sub));
      xs.forEach((x) => {
        const b = h("input", { type: "checkbox", checked: selected.has(x.id), onchange: (e) => { e.target.checked ? selected.add(x.id) : selected.delete(x.id); syncGroup(); refreshSummary(); } });
        childBoxes.push(b);
        list.append(h("label", { class: "row small", style: { gap: "8px" } }, b, h("span", { class: "grow" }, x.name), h("span", { class: "muted" }, priceText(x))));
      });
    }
    const det = h("details", { style: { borderBottom: "1px solid var(--line)" } },
      h("summary", { class: "row", style: { padding: "9px 4px", cursor: "pointer", listStyle: "none" } },
        h("span", { onclick: (e) => e.stopPropagation() }, groupBox), h("b", { class: "grow" }, key), count, icon("right", "sm")),
      list);
    syncGroup();
    blocks.push(det);
  }
  refreshSummary();
  return h("div", { class: "card", style: { padding: "4px 12px" } },
    blocks.length ? blocks : emptyState("Спершу додайте послуги", "scissors"),
    h("div", { class: "row", style: { padding: "8px 4px" } }, summary));
}

// ---------- Персонал
VIEWS.staff = async (main) => {
  const users = await api.get("/api/users");
  main.append(topbar("Персонал", "Співробітники, ролі, частка майстра і доступ", h("button", { class: "btn primary", onclick: () => userForm(null) }, icon("plus", "sm"), "Співробітник")));
  const def = state.settings;
  main.append(h("div", { class: "card table-wrap" }, h("table", {},
    h("tr", {}, h("th", {}, "Ім'я"), h("th", {}, "Логін"), h("th", {}, "Роль"), h("th", {}, "Відсоток"), h("th", {}, "Матеріали"), h("th", {}, "Telegram"), h("th", {}, "PIN бота"), h("th", {}, "")),
    users.map((u) => h("tr", { class: "click", onclick: () => userForm(u) }, h("td", {}, u.full_name), h("td", {}, u.username), h("td", {}, ROLES[u.role]),
      h("td", {}, u.role === "master" ? (u.share_bp !== null ? pct(u.share_bp) : pct(def.default_share_bp) + " (стандарт)") : "—"),
      h("td", { class: "small" }, u.role === "master" ? MAT_MODES[u.materials_mode || def.default_materials_mode] : "—"),
      h("td", {}, u.telegram_linked ? "✓" : ""), h("td", {}, u.bot_pin_set ? "✓" : ""), h("td", {}, u.is_active ? "" : h("span", { class: "chip" }, "неактивний")))))));

  function userForm(u) {
    const isNew = !u;
    const username = h("input", { value: u?.username || "", disabled: !isNew, autocapitalize: "none" });
    const password = h("input", { type: "password", autocomplete: "new-password", placeholder: isNew ? "" : "залиште порожнім" });
    const fullName = h("input", { value: u?.full_name || "" });
    const phone = h("input", { value: u?.phone || "", type: "tel" });
    const role = select(Object.entries(ROLES), u?.role || "master");
    const share = h("input", { type: "number", min: 0, max: 100, step: 0.5, value: u?.share_bp != null ? u.share_bp / 100 : "", placeholder: `стандарт ${pct(def.default_share_bp)}` });
    const mode = select([["", "Стандарт салону"], ...Object.entries(MAT_MODES)], u?.materials_mode || "");
    const active = h("input", { type: "checkbox", checked: u ? u.is_active : true });
    const pin = h("input", { inputmode: "numeric", autocomplete: "off", maxlength: 8, placeholder: u?.bot_pin_set ? "задано — введіть новий, щоб змінити" : "4–8 цифр" });
    const svcIds = new Set(u?.service_ids || []);
    const svcBox = servicesPicker(svcIds);
    const useOwn = h("input", { type: "checkbox", checked: !!u?.schedule });
    const sched = u?.schedule || def.schedule || {};
    const days = WD_LONG.map((name, i) => {
      const sp = sched[String(i)];
      const on = h("input", { type: "checkbox", checked: !!sp });
      const a = h("input", { type: "time", value: sp ? sp[0] : "09:00" });
      const b = h("input", { type: "time", value: sp ? sp[1] : "18:00" });
      return { i, on, a, b, row: h("div", { class: "row small" }, h("label", { class: "row", style: { width: "130px", gap: "6px" } }, on, name), h("div", { style: { width: "110px" } }, a), "—", h("div", { style: { width: "110px" } }, b)) };
    });
    const schedBox = h("div", { class: "stack" + (u?.schedule ? "" : " hidden") }, days.map((d) => d.row));
    useOwn.addEventListener("change", () => schedBox.classList.toggle("hidden", !useOwn.checked));
    const code = h("div", { class: "muted" });

    const save = h("button", { class: "btn gold big", onclick: async () => {
      const body = {
        full_name: fullName.value.trim(), phone: phone.value.trim() || null, role: role.value,
        share_bp: share.value === "" ? null : Math.round(parseFloat(share.value) * 100),
        materials_mode: mode.value || null, service_ids: [...svcIds],
        ...(pin.value.trim() ? { bot_pin: pin.value.trim() } : {}),
        schedule: useOwn.checked ? Object.fromEntries(days.map((d) => [String(d.i), d.on.checked ? [d.a.value, d.b.value] : null])) : null,
      };
      save.disabled = true;
      try {
        if (isNew) await run(() => api.post("/api/users", { ...body, username: username.value.trim(), password: password.value }), "Створено");
        else {
          await run(() => api.patch("/api/users/" + u.id, { ...body, is_active: active.checked }));
          if (password.value) await run(() => api.post(`/api/users/${u.id}/reset-password`, { password: password.value }));
          toast("Збережено");
        }
        closeSheet(); await loadRefs(); reload();
      } catch (_) { save.disabled = false; }
    } }, isNew ? "Створити" : "Зберегти");

    openSheet(isNew ? "Новий співробітник" : u.full_name, h("div", { class: "stack" },
      h("div", { class: "form-grid" }, field("Логін", username), field(isNew ? "Пароль" : "Новий пароль", password)),
      h("div", { class: "form-grid" }, field("Ім'я", fullName), field("Телефон", phone)),
      h("div", { class: "form-grid" }, field("Роль", role), field("PIN для Telegram-бота", pin)),
      h("div", { class: "form-grid" }, field("Відсоток майстра, %", share), field("Облік матеріалів", mode)),
      h("div", {}, h("div", { class: "kpi-label", style: { marginBottom: "6px" } }, "Послуги майстра"), svcBox),
      h("details", { class: "card", style: { padding: "4px 12px" } }, h("summary", { class: "row", style: { padding: "8px 0", cursor: "pointer", listStyle: "none" } }, icon("clock", "sm"), h("b", { class: "grow" }, "Тижневий шаблон графіка"), icon("right", "sm")),
        h("p", { class: "muted small", style: { margin: "4px 0 8px" } }, "Звичайний тиждень майстра. Вихідні й інші години на конкретні дати — у розділі «Графік роботи»."),
        h("label", { class: "row small", style: { margin: "8px 0" } }, useOwn, "Свій графік (інакше — як графік салону)"), schedBox),
      isNew ? null : h("label", { class: "row" }, active, "Активний (може входити в систему)"),
      isNew ? null : h("div", { class: "row" }, h("button", { class: "btn sm", onclick: async () => {
        const r = await run(() => api.post(`/api/users/${u.id}/telegram-code`)); code.textContent = `Код для Telegram: /link ${r.code}`;
      } }, "Код для Telegram"), code),
      save));
  }
};

// ---------- Послуги та матеріали
VIEWS.catalog = async (main) => {
  const services = await api.get("/api/services?all=true");
  main.append(topbar("Послуги", "Прайс салону. Матеріали й фарби — у розділі «Склад»", h("button", { class: "btn primary", onclick: () => svcForm(null) }, icon("plus", "sm"), "Послуга")));
  main.append(h("div", { class: "card table-wrap" }, h("table", {},
    h("tr", {}, h("th", {}, "Назва"), h("th", {}, "Розділ"), h("th", {}, "Тип"), h("th", {}, "Вид"), h("th", { class: "num" }, "Тривалість"), h("th", { class: "num" }, "Ціна"), h("th", {})),
    services.map((s) => h("tr", { class: "click", onclick: () => svcForm(s) }, h("td", {}, s.name), h("td", {}, s.section || ""), h("td", {}, s.category || ""), h("td", {}, s.subcategory || ""), h("td", { class: "num" }, s.duration_min, " хв"),
      h("td", { class: "num" }, priceText(s)), h("td", {}, s.is_active ? "" : h("span", { class: "chip" }, "прихована")))))));
  function svcForm(s) {
    const name = h("input", { value: s?.name || "" });
    const uniq = (k) => [...new Set(services.map((x) => x[k]).filter(Boolean))];
    const sec = h("input", { value: s?.section || "", placeholder: "Чоловічі / Жіночі", list: "dl-sec" });
    const cat = h("input", { value: s?.category || "", placeholder: "Стрижка, Фарбування…", list: "dl-cat" });
    const dls = [h("datalist", { id: "dl-sec" }, uniq("section").map((x) => h("option", { value: x }))),
      h("datalist", { id: "dl-cat" }, uniq("category").map((x) => h("option", { value: x }))),
      h("datalist", { id: "dl-sub" }, uniq("subcategory").map((x) => h("option", { value: x })))];
    const sub = h("input", { value: s?.subcategory || "", placeholder: "Мелірування… (необов'язково)", list: "dl-sub" });
    const dur = h("input", { type: "number", min: 5, step: 5, value: s?.duration_min || 60 });
    const price = h("input", { inputmode: "decimal", value: s ? kopToInput(s.price) : "" });
    const order = h("input", { type: "number", value: s?.sort_order || 0 });
    const active = h("input", { type: "checkbox", checked: s ? s.is_active : true });
    const from = h("input", { type: "checkbox", checked: s ? s.price_from : false });
    openSheet(s ? s.name : "Нова послуга", h("div", { class: "stack" }, dls, field("Назва", name),
      h("div", { class: "form-grid" }, field("Розділ (крок 1 у боті)", sec), field("Тип послуги (крок 2)", cat), field("Вид (крок 3)", sub)),
      h("div", { class: "form-grid" }, field("Тривалість, хв (для календаря)", dur), field("Ціна, ₴", price), field("Порядок", order)),
      h("label", { class: "row" }, from, "Ціна «від» (остаточна сума — після послуги)"),
      h("label", { class: "row" }, active, "Доступна для запису"),
      h("button", { class: "btn gold big", onclick: async () => {
        const body = { name: name.value, section: sec.value.trim() || null, category: cat.value.trim() || null, subcategory: sub.value.trim() || null, duration_min: +dur.value, price: toKop(price.value), price_from: from.checked, sort_order: +order.value, is_active: active.checked };
        await run(() => (s ? api.patch("/api/services/" + s.id, body) : api.post("/api/services", body)), "Збережено");
        closeSheet(); await loadRefs(); reload();
      } }, "Зберегти")));
  }
};

// ---------- Склад
const MOVE_KIND = { receipt: "Прихід", order: "Списано на замовлення", order_return: "Повернуто (виправлення)", adjust: "Інвентаризація", writeoff: "Списання" };

// ---------- Склад: одиниці, упаковки, форми
const UNIT_NAMES = { г: "грами (г)", мл: "мілілітри (мл)", шт: "штуки (шт)" };
// упаковки: [1, 2–4 і дробові, 5+]
const PACK_FORMS = {
  шт: ["шт", "шт", "шт"], туба: ["туба", "туби", "туб"], тюбик: ["тюбик", "тюбики", "тюбиків"], банка: ["банка", "банки", "банок"],
  флакон: ["флакон", "флакони", "флаконів"], пляшка: ["пляшка", "пляшки", "пляшок"], каністра: ["каністра", "каністри", "каністр"],
  упаковка: ["упаковка", "упаковки", "упаковок"], пачка: ["пачка", "пачки", "пачок"], набір: ["набір", "набори", "наборів"],
  коробка: ["коробка", "коробки", "коробок"], рулон: ["рулон", "рулони", "рулонів"], пара: ["пара", "пари", "пар"],
  ампула: ["ампула", "ампули", "ампул"], саше: ["саше", "саше", "саше"], балон: ["балон", "балони", "балонів"],
};
const PACK_LIST = Object.keys(PACK_FORMS);
// «1 туба», «3 туби», «2,5 туби», «7 туб»
function packWord(name, n) {
  const f = PACK_FORMS[name];
  if (!f) return name;
  if (!Number.isInteger(Math.round(n * 100) / 100)) return f[1];
  return plural(Math.abs(Math.round(n)), f[0], f[1], f[2]);
}
// кількість матеріалу: «120 г · 2 туби», «2 рулони», «485 шт · 9,7 упаковки»
const fmtNum = (x) => (Math.round(x * 100) / 100).toLocaleString("uk-UA");
function fmtQty(m, q) {
  if (m.pack_size > 1) { const p = q / m.pack_size; return `${fmtNum(q)} ${m.unit} · ${fmtNum(p)} ${packWord(m.pack_name, p)}`; }
  if (m.unit === "шт" && m.pack_name && m.pack_name !== "шт") return `${fmtNum(q)} ${packWord(m.pack_name, q)}`;
  return `${fmtNum(q)} ${m.unit}`;
}
const matCost = (m, q) => Math.round((m.unit_cost * q) / (m.pack_size || 1));
const parseNum = (v) => parseFloat(String(v).replace(",", ".").replace(/\s/g, ""));
// поле кількості з вибором, у чому вводити: упаковками (туби, флакони…) або базовою одиницею (г / мл / шт)
function qtyInput(getM, { value = null, placeholder = "0" } = {}) {
  const inp = h("input", { inputmode: "decimal", placeholder });
  const sel = h("select", { class: "qty-unit", "aria-label": "Одиниця" });
  const fill = () => {
    const m = getM();
    const cur = sel.value;
    const opts = m.pack_size > 1 ? [["pack", PACK_FORMS[m.pack_name]?.[2] || m.pack_name], ["base", m.unit]] : [["base", m.unit === "шт" && m.pack_name !== "шт" ? (PACK_FORMS[m.pack_name]?.[2] || m.pack_name) : m.unit]];
    sel.replaceChildren(...opts.map(([v, t]) => h("option", { value: v }, t)));
    if (opts.some(([v]) => v === cur)) sel.value = cur;
  };
  fill();
  if (value !== null) { const m = getM(); inp.value = m.pack_size > 1 ? fmtNum(value / m.pack_size).replace(/\s/g, "") : fmtNum(value).replace(/\s/g, ""); }
  const wrap = h("div", { class: "qty-field" }, inp, sel);
  wrap.base = () => { const v = parseNum(inp.value); if (!Number.isFinite(v)) return NaN; return sel.value === "pack" ? Math.round(v * getM().pack_size * 1000) / 1000 : v; };
  wrap.refresh = fill;
  wrap.input = inp;
  wrap.sel = sel;
  return wrap;
}

function materialForm(m, list, onSaved) {
  const uniq = (k) => [...new Set(list.map((x) => x[k]).filter(Boolean))];
  const name = h("input", { value: m?.name || "", placeholder: "Напр. Igora Royal", list: "dl-mname" });
  const tone = h("input", { value: m?.tone || "", placeholder: "Напр. 7-1 (для фарб)" });
  const cat = h("input", { value: m?.category || "", placeholder: "Фарби, Окисники, Догляд…", list: "dl-mcat" });
  const unit = select(Object.entries(UNIT_NAMES), m?.unit && UNIT_NAMES[m.unit] ? m.unit : "г");
  const locked = !!m && (m.stock !== 0);
  if (locked) unit.disabled = true;
  const packOpts = [...PACK_LIST.map((p) => [p, p]), ["__other", "інша…"]];
  const isOther = m && m.pack_name && !PACK_FORMS[m.pack_name];
  const pack = select(packOpts, isOther ? "__other" : (m?.pack_name || "туба"));
  const packOther = h("input", { value: isOther ? m.pack_name : "", placeholder: "назва упаковки", maxlength: 16 });
  const otherBox = h("div", { class: isOther ? "" : "hidden" }, field("Своя назва упаковки", packOther));
  const packSize = h("input", { inputmode: "decimal", value: m?.pack_size || 60 });
  const sizeLbl = h("span", {}, "");
  const cost = h("input", { inputmode: "decimal", value: m ? kopToInput(m.unit_cost) : "", placeholder: "можна пізніше" });
  const active = h("input", { type: "checkbox", checked: m ? m.is_active : true });
  const preview = h("div", { class: "muted small" });
  const cur = () => ({ unit: unit.value, pack_name: pack.value === "__other" ? (packOther.value.trim() || "упаковка") : pack.value,
    pack_size: Math.max(1, Math.round(parseNum(packSize.value) || 1)) });
  const initial = qtyInput(cur, { placeholder: "0" });
  const min = qtyInput(cur, { value: m?.min_stock ?? null, placeholder: "напр. 1" });
  const sync = () => {
    const c = cur();
    sizeLbl.textContent = `В 1 ${c.pack_name === "шт" ? "шт" : c.pack_name} — скільки ${c.unit}`;
    otherBox.classList.toggle("hidden", pack.value !== "__other");
    const price = toKop(cost.value);
    preview.textContent = c.pack_size > 1 && price > 0 ? `1 ${c.unit} коштує ${money(Math.round(price / c.pack_size))}` +
      ` · 1 ${packWord(c.pack_name, 1)} = ${fmtNum(c.pack_size)} ${c.unit}` : "";
    initial.refresh(); min.refresh();
  };
  unit.addEventListener("change", () => {
    if (unit.value === "шт" && ["туба", "банка", "флакон", "пляшка", "каністра", "тюбик", "балон"].includes(pack.value)) { pack.value = "шт"; packSize.value = 1; }
    else if (unit.value !== "шт" && pack.value === "шт") { pack.value = unit.value === "мл" ? "флакон" : "туба"; if (+packSize.value <= 1) packSize.value = unit.value === "мл" ? 1000 : 60; }
    sync();
  });
  pack.addEventListener("change", () => { if (pack.value === "шт") packSize.value = 1; sync(); });
  [packSize, cost, packOther].forEach((x) => x.addEventListener("input", sync));
  sync();
  openSheet(m ? m.display_name : "Новий матеріал", h("div", { class: "stack" },
    h("datalist", { id: "dl-mname" }, uniq("name").map((x) => h("option", { value: x }))),
    h("datalist", { id: "dl-mcat" }, uniq("category").map((x) => h("option", { value: x }))),
    h("div", { class: "form-grid" }, field("Назва / лінійка", name), field("Тон", tone)),
    field("Категорія", cat),
    h("div", { class: "form-grid" }, field("Облік у", unit), field("Упаковка", pack), h("label", { class: "f" }, sizeLbl, packSize)),
    otherBox,
    locked ? h("p", { class: "muted small", style: { margin: 0 } }, "Одиницю обліку змінити не можна — вже є залишок. Упаковку й ціну — можна.") : null,
    h("div", { class: "form-grid" }, field("Ціна 1 упаковки, ₴", cost), field("Мінімальний залишок", min), m ? null : field("Є зараз на складі", initial)),
    preview,
    m ? h("label", { class: "row" }, active, "Використовується") : null,
    h("button", { class: "btn primary big", onclick: async () => {
      if (!name.value.trim()) return toast("Вкажіть назву матеріалу", true);
      const price = cost.value.trim() ? toKop(cost.value) : 0;
      if (!Number.isFinite(price) || price < 0) return toast("Ціна упаковки — число, напр. 320", true);
      const c = cur();
      const mn = min.input.value.trim() ? min.base() : null;
      if (mn !== null && !(mn >= 0)) return toast("Мінімальний залишок — число", true);
      const body = { name: name.value.trim(), tone: tone.value.trim() || null, category: cat.value.trim() || null, unit: c.unit,
        pack_name: c.pack_name, pack_size: c.pack_size, unit_cost: price, min_stock: mn };
      if (locked) delete body.unit;
      if (!m && initial.input.value.trim()) { const v = initial.base(); if (!(v >= 0)) return toast("Залишок — число", true); body.stock = v; }
      if (m) body.is_active = active.checked;
      await run(() => (m ? api.patch("/api/materials/" + m.id, body) : api.post("/api/materials", body)), "Збережено");
      closeSheet(); onSaved();
    } }, "Зберегти")));
}

function stockMoveForm(m, kind, onSaved) {
  const qty = qtyInput(() => m, { value: kind === "adjust" ? Math.max(0, m.stock) : null });
  const cost = h("input", { inputmode: "decimal", placeholder: "за накладною, ₴" });
  const asExp = h("input", { type: "checkbox", checked: state.user.role === "director" });
  const note = h("input", { placeholder: kind === "writeoff" ? "Обов'язково: чому списуємо" : "Примітка" });
  const after = h("div", { class: "sum-line total" });
  const calc = () => {
    const v = qty.base();
    if (!Number.isFinite(v)) { after.replaceChildren(h("span", { class: "muted" }, "Після операції"), h("span", {}, "—")); return; }
    const res = kind === "adjust" ? v : kind === "receipt" ? m.stock + v : m.stock - v;
    after.replaceChildren(h("span", {}, kind === "adjust" ? `Різниця ${v - m.stock >= 0 ? "+" : ""}${fmtNum(v - m.stock)} ${m.unit}` : "Буде на складі"),
      h("b", { class: res < 0 ? "neg" : "" }, fmtQty(m, res)));
    if (kind === "receipt") cost.placeholder = `за ціною упаковки: ${money(matCost(m, v))}`;
  };
  qty.input.addEventListener("input", calc); qty.sel.addEventListener("change", calc); calc();
  const title = { receipt: "Прихід", adjust: "Інвентаризація", writeoff: "Списання" }[kind];
  openSheet(`${title}: ${m.display_name}`, h("div", { class: "stack" },
    h("div", { class: "muted" }, "Зараз на складі: ", h("b", {}, fmtQty(m, m.stock))),
    field(kind === "adjust" ? "Фактично є" : kind === "receipt" ? "Скільки надійшло" : "Скільки списати", qty),
    after,
    kind === "receipt" ? field("Сума закупівлі, ₴ (порожньо — за ціною упаковки)", cost) : null,
    kind === "receipt" && state.user.role === "director" ? h("label", { class: "row" }, asExp, "Записати у витрати «Закупівля матеріалів»") : null,
    field("Примітка", note),
    h("button", { class: "btn primary big", onclick: async () => {
      const v = qty.base();
      if (!(v >= 0)) return toast("Вкажіть кількість", true);
      if (kind !== "adjust" && !(v > 0)) return toast("Кількість має бути більшою за 0", true);
      if (kind === "writeoff" && !note.value.trim()) return toast("Вкажіть причину списання", true);
      const url = `/api/stock/${m.id}/` + { adjust: "adjust", writeoff: "writeoff", receipt: "receive" }[kind];
      const body = kind === "adjust" ? { actual: v, note: note.value || null }
        : kind === "writeoff" ? { qty: v, note: note.value }
          : { qty: v, cost: cost.value ? toKop(cost.value) : null, as_expense: asExp.checked, note: note.value || null };
      await run(() => api.post(url, body), "Склад оновлено");
      closeSheet(); onSaved();
    } }, title)));
  autoFocus(qty.input, true);
}

// дії з матеріалом (телефон: натискання на картку)
function materialActions(m, list, refresh, canEdit) {
  openSheet(m.display_name, h("div", { class: "stack" },
    h("div", { class: "mat-sum" },
      h("div", {}, h("span", {}, "Залишок"), h("b", { class: m.stock < 0 ? "neg" : "" }, fmtQty(m, m.stock))),
      h("div", {}, h("span", {}, "Ціна"), h("b", {}, `${money(m.unit_cost)} / ${m.pack_name}`)),
      m.min_stock !== null ? h("div", {}, h("span", {}, "Мінімум"), h("b", {}, fmtQty(m, m.min_stock))) : null),
    canEdit ? h("div", { class: "grid", style: { gridTemplateColumns: "1fr 1fr", gap: "8px" } },
      h("button", { class: "btn primary", onclick: () => stockMoveForm(m, "receipt", refresh) }, icon("plus", "sm"), "Прихід"),
      h("button", { class: "btn", onclick: () => stockMoveForm(m, "adjust", refresh) }, icon("check", "sm"), "Інвентаризація"),
      h("button", { class: "btn", onclick: () => stockMoveForm(m, "writeoff", refresh) }, icon("trash", "sm"), "Списати"),
      h("button", { class: "btn", onclick: () => materialForm(m, list, refresh) }, icon("edit", "sm"), "Редагувати")) : null,
    h("button", { class: "btn ghost", onclick: () => { closeSheet(); location.hash = "#/stock?moves=" + m.id; } }, icon("history", "sm"), "Історія руху")));
}

VIEWS.stock = async (main) => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  let list = await api.get("/api/stock?all=true");
  const canEdit = state.user.role !== "master";
  let movesFor = params.get("moves") ? Number(params.get("moves")) : null;
  let tab = movesFor ? "moves" : "left", cat = "", q = "";
  const body = h("div");
  const refresh = async () => { list = await api.get("/api/stock?all=true"); state.materials = list.filter((m) => m.is_active); closeSheet(); draw(); };
  main.append(topbar("Склад", "Залишки матеріалів і фарб. Списуються автоматично при закритті замовлення",
    canEdit ? h("button", { class: "btn primary", onclick: () => materialForm(null, list, refresh) }, icon("plus", "sm"), "Матеріал") : null));
  const search = h("input", { type: "search", placeholder: "Пошук: назва або тон", id: "stock-search", oninput: (e) => { q = e.target.value.trim().toLowerCase(); draw(); } });
  const tabs = seg([["left", "Залишки"], ["moves", "Рух складу"]], tab, (v) => { tab = v; if (v === "left") movesFor = null; draw(); });
  main.append(h("div", { class: "row stock-bar" }, tabs, h("div", { class: "search-box grow" }, icon("search", "sm"), search)), body);

  async function draw() {
    if (tab === "moves") {
      const mv = await api.get("/api/stock/moves" + (movesFor ? "?material_id=" + movesFor : ""));
      const mat = movesFor ? list.find((x) => x.id === movesFor) : null;
      const rows = mv.filter((x) => !q || x.material.toLowerCase().includes(q));
      const mm = (x) => list.find((y) => y.id === x.material_id) || { unit: x.unit, pack_size: 1, pack_name: x.unit };
      body.replaceChildren(
        mat ? h("div", { class: "callout", style: { alignItems: "center" } }, icon("history"), h("div", { class: "grow" }, "Рух: ", h("b", {}, mat.display_name), " · зараз ", fmtQty(mat, mat.stock)),
          h("button", { class: "btn sm", onclick: () => { movesFor = null; history.replaceState(null, "", "#/stock"); draw(); } }, icon("x", "sm"), "Усі матеріали")) : null,
        rows.length ? h("div", { class: "card table-wrap" }, h("table", {},
          h("tr", {}, h("th", {}, "Дата"), mat ? null : h("th", {}, "Матеріал"), h("th", {}, "Операція"), h("th", { class: "num" }, "Кількість"), h("th", { class: "num" }, "Сума"), h("th", {}, "Примітка")),
          rows.map((x) => h("tr", {},
            h("td", {}, dm(x.ts), " ", h("span", { class: "muted" }, hm(x.ts))), mat ? null : h("td", {}, x.material), h("td", {}, h("span", { class: "chip mv-" + x.kind }, MOVE_KIND[x.kind] || x.kind)),
            h("td", { class: "num " + (x.qty < 0 ? "neg" : "pos") }, (x.qty > 0 ? "+" : "") + fmtQty(mm(x), x.qty)),
            h("td", { class: "num" }, x.cost ? money(x.cost) : ""), h("td", { class: "muted small" }, x.order_id ? `Замовлення #${x.order_id}` : (x.note || "")))))) :
          h("div", { class: "card" }, emptyState("Рухів ще не було", "box")));
      return;
    }
    const cats = [...new Set(list.map((m) => m.category || "Інше"))].sort();
    const shown = list.filter((m) => (!cat || (m.category || "Інше") === cat) && (!q || (m.display_name + " " + (m.category || "")).toLowerCase().includes(q)));
    const low = list.filter((m) => m.is_active && m.low);
    const value = list.filter((m) => m.stock > 0).reduce((a, m) => a + matCost(m, m.stock), 0);
    // групуємо тони однієї лінійки: «Igora Royal» → 7-1, 8-0…
    const groups = new Map();
    shown.forEach((m) => { const k = m.name; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(m); });
    const lowChip = (m) => (m.stock < 0 ? h("span", { class: "chip st-overdue" }, "мінус") : m.low ? h("span", { class: "chip st-pending" }, "мало") : null);
    const table = h("div", { class: "card table-wrap only-desk" }, h("table", { class: "stock-table" },
      h("tr", {}, h("th", {}, "Матеріал / тон"), h("th", {}, "Категорія"), h("th", { class: "num" }, "Залишок"), h("th", { class: "num" }, "Ціна упаковки"), canEdit ? h("th", { class: "right" }, "") : null),
      [...groups.entries()].map(([name, items]) => [
        items.length > 1 || items[0].tone ? h("tr", { class: "grp-row" }, h("td", { colspan: canEdit ? 5 : 4 }, name, h("span", { class: "muted small" }, `  · тонів: ${items.length}`))) : null,
        items.map((m) => h("tr", { class: "click" + (m.is_active ? "" : " inactive"), onclick: () => materialActions(m, list, refresh, canEdit) },
          h("td", {}, h("b", {}, m.tone || m.name), !m.is_active ? h("span", { class: "chip plain", style: { marginLeft: "6px" } }, "не використовується") : null),
          h("td", { class: "muted" }, m.category || ""),
          h("td", { class: "num" }, h("span", { class: "row", style: { justifyContent: "flex-end", gap: "8px" } }, lowChip(m), h("b", { class: m.stock < 0 ? "neg" : "" }, fmtQty(m, m.stock)))),
          h("td", { class: "num" }, money(m.unit_cost), h("span", { class: "muted small" }, " / " + m.pack_name)),
          canEdit ? h("td", { class: "right", onclick: (e) => e.stopPropagation() }, h("div", { class: "stock-actions" },
            h("button", { class: "btn sm", onclick: () => stockMoveForm(m, "receipt", refresh) }, icon("plus", "sm"), "Прихід"),
            h("button", { class: "btn sm ghost", title: "Інші дії", "aria-label": "Інші дії", onclick: () => materialActions(m, list, refresh, canEdit) }, icon("more", "sm")))) : null)),
      ])));
    const cards = h("div", { class: "only-mob anim-in" }, [...groups.entries()].map(([name, items]) => h("div", { class: "mat-group" },
      items.length > 1 || items[0].tone ? h("div", { class: "ord-day-head" }, h("span", {}, name), h("span", {}, `тонів: ${items.length}`)) : null,
      h("div", { class: "card ord-list" }, items.map((m) => h("button", { class: "mat-item" + (m.is_active ? "" : " inactive"), onclick: () => materialActions(m, list, refresh, canEdit) },
        h("div", { class: "grow ord-main" }, h("b", {}, items.length > 1 || items[0].tone ? (m.tone || m.name) : m.name),
          h("div", { class: "muted small" }, [m.category, `${money(m.unit_cost)} / ${m.pack_name}`].filter(Boolean).join(" · "))),
        h("div", { class: "ord-amt" }, h("b", { class: m.stock < 0 ? "neg" : "" }, fmtQty(m, m.stock).split(" · ").pop()),
          m.pack_size > 1 ? h("span", { class: "muted small" }, `${fmtNum(m.stock)} ${m.unit}`) : null, lowChip(m))))))));
    body.replaceChildren(...[
      h("div", { class: "grid kpi kpi-6", style: { marginBottom: "12px" } },
        kpi("Позицій", list.filter((m) => m.is_active).length, "", { ico: "box", raw: true }),
        kpi("Закінчується", low.length, low.length ? "accent" : "", { ico: "alert", raw: true, sub: low.slice(0, 3).map((m) => m.display_name).join(", ") }),
        kpi("Вартість залишку", value, "", { ico: "banknote" })),
      cats.length > 1 ? h("div", { class: "chips-row" },
        h("button", { class: "chip-btn" + (cat ? "" : " on"), onclick: () => { cat = ""; draw(); } }, "Усі"),
        cats.map((c) => h("button", { class: "chip-btn" + (cat === c ? " on" : ""), onclick: () => { cat = c; draw(); } }, c))) : null,
      ...(list.length ? (shown.length ? [table, cards] : [h("div", { class: "card" }, emptyState("Нічого не знайдено", "search"))])
        : [h("div", { class: "card" }, emptyState("Склад порожній. Додайте матеріали й фарби кнопкою «+ Матеріал»", "box"))])].filter(Boolean));
  }
  draw();
};

// ---------- Графік роботи (по датах)
VIEWS.schedule = async (main) => {
  // Графік — сітка-календар на 4 тижні: квадратик = день, ✓ — робочий. Натискання перемикає
  // «робочий ↔ вихідний»; режим «Години» — змінити години конкретного дня.
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const monday = (iso) => { const d = new Date(iso + "T12:00"); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return isoDate(d); };
  const start = params.get("w") || monday(today());
  const end = addDays(start, 27);
  const go = (w) => { location.hash = "#/schedule?w=" + w; };
  const canEdit = state.user.role !== "master";
  const url = `/api/schedule?date_from=${start}&date_to=${end}`;
  let mode = "days";
  main.append(topbar("Графік роботи", `${dmy(start)} — ${dmy(end)}`,
    h("button", { class: "btn", "aria-label": "Раніше", onclick: () => go(addDays(start, -28)) }, icon("left", "sm")),
    h("button", { class: "btn", onclick: () => go(monday(today())) }, "Сьогодні"),
    h("button", { class: "btn", "aria-label": "Пізніше", onclick: () => go(addDays(start, 28)) }, icon("right", "sm"))));
  let g = await api.get(url);
  if (!g.masters.length) { main.append(h("div", { class: "card" }, emptyState("Додайте майстрів у розділі «Персонал»", "users"))); return; }

  const short = (x) => (x.endsWith(":00") ? String(+x.slice(0, 2)) : x);
  const hint = h("span", { class: "muted small" });
  const drawHint = () => { hint.textContent = mode === "days" ? "Натисніть на день: робочий ✓ ↔ вихідний" : "Натисніть на день, щоб змінити його години"; };
  drawHint();
  if (canEdit) {
    main.append(h("div", { class: "row", style: { marginBottom: "12px", flexWrap: "wrap" } },
      seg([["days", "✓ Робочі дні"], ["hours", "Години"]], mode, (v) => { mode = v; drawHint(); }), hint));
  }
  const body = h("div", { class: "grid two" });
  main.append(body, h("div", { class: "legend" },
    h("span", { class: "chip", style: { background: "var(--ok)", color: "#fff" } }, "✓ робочий"), h("span", { class: "chip st-cancelled" }, "сірий — вихідний"),
    h("span", {}, "Золота рамка — змінено на цю дату. Години замість галочки — якщо не звичайні.")));

  const card = (m) => {
    const cells = g.cells[m.id];
    const usual = m.usual_hours;
    const work = g.days.filter((d) => !cells[d].off).length;
    return h("div", { class: "card stack" },
      h("div", { class: "row" }, avatar(m.full_name, "sm"),
        h("div", { class: "grow" }, h("b", {}, m.full_name),
          h("div", { class: "muted small" }, `робочих днів: ${work} · зазвичай ${short(usual[0])}–${short(usual[1])}`)),
        canEdit ? h("button", { class: "btn sm ghost", onclick: () => copyForm(m) }, "Копіювати тиждень…") : null),
      h("div", { class: "sched-grid" },
        ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"].map((w) => h("div", { class: "sched-wd" }, w)),
        g.days.map((d) => {
          const c = cells[d];
          const unusual = !c.off && (c.start !== usual[0] || c.end !== usual[1]);
          return h("button", {
            type: "button", disabled: !canEdit || null, "aria-pressed": String(!c.off),
            class: "sched-day " + (c.off ? "off" : "on") + (c.source === "day" ? " changed" : "") + (d === today() ? " today" : ""),
            "aria-label": `${weekday(d)} ${dm(d)}: ${c.off ? "вихідний" : `${c.start}–${c.end}`}`,
            onclick: () => (mode === "hours" ? dayForm(m, d, c) : toggle(m, d, c)),
          },
          h("span", { class: "num" }, String(+d.slice(8, 10))),
          c.off ? null : unusual ? h("span", { class: "hrs" }, `${short(c.start)}–${short(c.end)}`) : icon("ok"));
        })));
  };
  const draw = () => body.replaceChildren(...g.masters.map(card));
  draw();

  let busy = Promise.resolve();
  function toggle(m, d, c) {
    const api_url = `/api/schedule/${m.id}/${d}`;
    const tpl = c.template; // тижневий шаблон цього дня (null — за шаблоном вихідний)
    let req;
    if (!c.off) {
      // робочий → вихідний; якщо за шаблоном і так вихідний — просто прибрати зміну
      req = !tpl && c.source === "day" ? () => api.del(api_url) : () => api.put(api_url, { off: true });
      Object.assign(c, { off: true, start: null, end: null });
    } else {
      const [s1, e1] = tpl || m.usual_hours;
      req = tpl && c.source === "day" ? () => api.del(api_url) : () => api.put(api_url, { start: s1, end: e1 });
      Object.assign(c, { off: false, start: s1, end: e1 });
    }
    c.source = "day";
    draw(); // одразу показуємо галочку, сервер підтверджує
    busy = busy.then(async () => {
      try { await req(); } catch (e) { toast(e.message, true); }
      g = await api.get(url); draw();
    });
  }

  function dayForm(m, d, c) {
    let off = c.off;
    const from = h("input", { type: "time", value: c.start || "09:00" });
    const to = h("input", { type: "time", value: c.end || "18:00" });
    const times = h("div", { class: "form-grid" + (off ? " hidden" : "") }, field("Початок", from), field("Кінець", to));
    const mode = seg([["work", "Робочий день"], ["off", "Вихідний"]], off ? "off" : "work", (v) => { off = v === "off"; times.classList.toggle("hidden", off); });
    openSheet(`${m.full_name} · ${weekday(d)}, ${dmy(d)}`, h("div", { class: "stack" }, mode, times,
      h("button", { class: "btn primary big", onclick: async () => {
        await run(() => api.put(`/api/schedule/${m.id}/${d}`, off ? { off: true } : { start: from.value, end: to.value }), "Графік збережено");
        closeSheet(); reload();
      } }, "Зберегти"),
      c.source === "day" ? h("button", { class: "btn", onclick: async () => {
        await run(() => api.del(`/api/schedule/${m.id}/${d}`), "Повернуто до шаблону"); closeSheet(); reload();
      } }, "Як у шаблоні (скасувати зміну)") : null,
      h("p", { class: "muted small" }, "Записи, що вже є на цей день, не скасовуються автоматично — перевірте календар.")));
  }
  function copyForm(m) {
    const weeks = select([[1, "на 1 тиждень"], [2, "на 2 тижні"], [4, "на 4 тижні"], [8, "на 8 тижнів"]], 4);
    openSheet(`Копіювати графік: ${m.full_name}`, h("div", { class: "stack" },
      h("p", {}, `Графік тижня з ${dmy(start)} буде скопійовано на наступні тижні (зручно для змінного графіка).`),
      field("Скопіювати", weeks),
      h("button", { class: "btn primary big", onclick: async () => {
        await run(() => api.post(`/api/schedule/${m.id}/copy-week`, { from_monday: start, weeks: +weeks.value }), "Графік скопійовано");
        closeSheet(); reload();
      } }, "Копіювати")));
  }
};

// ---------- Налаштування
VIEWS.settings = async (main) => {
  const s = await api.get("/api/settings");
  const backups = await api.get("/api/backups").catch(() => []);
  const drive = await api.get("/api/gdrive").catch(() => null);
  const gq = new URLSearchParams(location.hash.split("?")[1] || "").get("gdrive");
  if (gq) {
    const msg = { ok: "Google Drive підключено — копії зберігатимуться автоматично", cancelled: "Підключення Google Drive скасовано",
      denied: "Посилання застаріло — натисніть «Підключити» ще раз", error: "Google не дав доступ. Спробуйте ще раз" }[gq];
    if (msg) toast(msg, gq !== "ok");
    history.replaceState(null, "", "#/settings");
  }
  const name = h("input", { value: s.salon_name });
  const share = h("input", { type: "number", min: 0, max: 100, step: 0.5, value: s.default_share_bp / 100 });
  const mode = select(Object.entries(MAT_MODES), s.default_materials_mode);
  const step = h("input", { type: "number", min: 5, step: 5, value: s.slot_step_min });
  const horizon = h("input", { type: "number", min: 1, value: s.booking_horizon_days });
  const lead = h("input", { type: "number", min: 0, value: s.booking_min_lead_min });
  const auto = h("input", { type: "checkbox", checked: s.telegram_auto_confirm });
  const takesCash = h("input", { type: "checkbox", checked: s.master_takes_cash !== false });
  const favVisits = h("input", { type: "number", min: 1, max: 50, value: s.favorite_master_visits || 3 });
  const remind = h("input", { type: "checkbox", checked: s.reminders_enabled !== false });
  const termMode = select([["off", "Вимкнено"], ["mock", "Імітація (без списання грошей)"], ["live", "Реальні оплати (ключі на сервері)"]], s.card_terminal_mode || "off");
  const staffCode = h("input", { inputmode: "numeric", maxlength: 8, value: s.bot_staff_code || "" });
  const addr = h("input", { value: s.salon_address || "" });
  const phone = h("input", { value: s.salon_phone || "", type: "tel" });
  const insta = h("input", { value: s.salon_instagram || "", placeholder: "@md.salon" });
  const days = WD_LONG.map((n, i) => {
    const sp = (s.schedule || {})[String(i)];
    const on = h("input", { type: "checkbox", checked: !!sp });
    const a = h("input", { type: "time", value: sp ? sp[0] : "09:00" });
    const b = h("input", { type: "time", value: sp ? sp[1] : "20:00" });
    return { i, on, a, b, row: h("div", { class: "row small" }, h("label", { class: "row", style: { width: "130px", gap: "6px" } }, on, n), h("div", { style: { width: "110px" } }, a), "—", h("div", { style: { width: "110px" } }, b)) };
  });
  main.append(topbar("Налаштування", "Дані салону, робочі години, запис через бот і безпека"), h("div", { class: "grid two" },
    h("div", { class: "card stack" }, h("h3", {}, "Оплата майстрів"),
      field("Стандартний відсоток майстра, %", share), field("Облік матеріалів за замовчуванням", mode),
      h("label", { class: "row" }, takesCash, "Після зміни майстер бере заробіток з готівки в касі (решту доплачує директор)"),
      h("p", { class: "muted small" }, "Індивідуальні умови задаються в картці майстра (розділ «Персонал»). Зміни діють для нових замовлень; закриті замовлення зберігають умови на момент закриття.")),
    twofaSettingsCard(s),
    generatorSettingsCard(s),
    h("div", { class: "card stack" }, h("h3", {}, "Запис клієнтів"), field("Назва салону", name),
      h("div", { class: "form-grid" }, field("Крок слотів, хв", step), field("Запис наперед, днів", horizon), field("Мін. хв до запису (бот)", lead)),
      h("label", { class: "row" }, auto, "Записи з Telegram одразу підтверджені"),
      field("Улюблений майстер клієнта — після скількох візитів", favVisits),
      h("label", { class: "row" }, remind, "Нагадування клієнтам у Telegram за добу і за 2 години (бот питає згоду)")),
    h("div", { class: "card stack" }, h("h3", {}, "Оплата карткою — «Термінал» ПриватБанку (NFC)"),
      field("Режим", termMode),
      h("p", { class: "muted small" }, "Планшет з NFC і застосунком «Термінал» ПриватБанку стає платіжним терміналом: у закритті замовлення обираєте «Термінал», " +
        "клієнт прикладає картку, замовлення закривається саме після підтвердження банку. Для реальних оплат ключі інтеграції задаються лише на сервері.")),
    h("div", { class: "card stack" }, h("h3", {}, "Telegram-бот"),
      field("Код входу в розділ «Для майстрів»", staffCode),
      h("p", { class: "muted small" }, "Після коду кожен обирає себе й вводить особистий PIN (задається в розділі «Персонал»)."),
      field("Адреса салону", addr), h("div", { class: "form-grid" }, field("Телефон", phone), field("Instagram", insta))),
    h("div", { class: "card stack" }, h("h3", {}, "Графік салону"), days.map((d) => d.row)),
    h("div", { class: "card stack" }, h("h3", {}, "Резервні копії"),
      driveBlock(drive),
      h("p", { class: "muted small" }, "Автоматично кожні 6 годин. Останні копії на сервері:"),
      backups.slice(0, 5).map((b) => h("div", { class: "sum-line small" }, h("span", {}, b.name), h("span", { class: "muted" }, (b.size / 1024).toFixed(0), " КБ"))),
      h("button", { class: "btn", onclick: async () => { await run(() => api.post("/api/backups"), "Копію створено"); reload(); } }, "Створити копію зараз"))),
    h("button", { class: "btn gold big", style: { marginTop: "14px" }, onclick: async () => {
      const body = {
        salon_name: name.value, default_share_bp: Math.round(parseFloat(share.value) * 100), default_materials_mode: mode.value,
        slot_step_min: +step.value, booking_horizon_days: +horizon.value, booking_min_lead_min: +lead.value, telegram_auto_confirm: auto.checked,
        master_takes_cash: takesCash.checked, reminders_enabled: remind.checked, card_terminal_mode: termMode.value, favorite_master_visits: Math.max(1, parseInt(favVisits.value || "3", 10)),
        bot_staff_code: staffCode.value.trim(), salon_address: addr.value.trim(), salon_phone: phone.value.trim(), salon_instagram: insta.value.trim(),
        schedule: Object.fromEntries(days.map((d) => [String(d.i), d.on.checked ? [d.a.value, d.b.value] : null])),
      };
      state.settings = await run(() => api.put("/api/settings", body), "Налаштування збережено");
    } }, "Зберегти налаштування"));
};

// Google Drive: стан, підключення, копія зараз
function driveBlock(d) {
  if (!d) return null;
  const dt = (iso) => (iso ? `${dm(iso)} о ${hm(iso)}` : "—");
  if (!d.configured) {
    return h("div", { class: "callout" }, icon("alert"), h("div", { class: "small" },
      "Google Drive ще не налаштовано на сервері. Інструкція — docs/OPERATIONS.md, розділ «Google Drive»."));
  }
  if (!d.connected) {
    return h("div", { class: "stack" },
      h("p", { class: "small" }, "Підключіть Google Drive — копії бази зберігатимуться ще й там (програма бачить лише свою папку)."),
      h("button", { class: "btn primary", onclick: async () => { const r = await run(() => api.post("/api/gdrive/connect")); location.href = r.url; } },
        icon("lock"), "Підключити Google Drive"));
  }
  return h("div", { class: "stack" },
    h("div", { class: "sum-line" }, h("span", {}, "Google Drive"), h("span", { class: "chip st-completed" }, "підключено")),
    d.account ? h("div", { class: "sum-line small" }, h("span", { class: "muted" }, "Акаунт"), h("span", {}, d.account)) : null,
    h("div", { class: "sum-line small" }, h("span", { class: "muted" }, "Остання копія на диску"), h("span", {}, dt(d.last_ok_at))),
    d.last_error ? h("div", { class: "callout", style: { background: "var(--warn-bg)" } }, icon("alert"),
      h("div", { class: "small" }, `Помилка ${dt(d.last_error_at)}: ${d.last_error}`)) : null,
    h("p", { class: "muted small" }, `На диску зберігаються останні ${d.keep} копій (≈ ${Math.round(d.keep / 4)} днів).`),
    h("div", { class: "row" },
      h("button", { class: "btn", onclick: async () => { await run(() => api.post("/api/gdrive/backup-now"), "Копію збережено в Google Drive"); reload(); } }, "Зберегти на диск зараз"),
      h("button", { class: "btn ghost", onclick: () => confirmSheet("Відключити Google Drive?", "Нові копії перестануть зберігатися на диск. Уже збережені копії залишаться.",
        "Відключити", async () => { await run(() => api.post("/api/gdrive/disconnect"), "Google Drive відключено"); reload(); }) }, "Відключити")));
}

// ---------- Журнал: стрічка подій простими словами
// ============================================================ звіт про доходи для декларації ФОП
// Цифри для ручного введення в декларацію (кабінет ДПС / Приват24). Дохід = сума закритих замовлень.
async function downloadFile(path, fallbackName) {
  let res;
  try {
    res = await fetch(path, { headers: { Authorization: "Bearer " + api.token } });
  } catch (e) { throw new Error("Немає з'єднання з сервером"); }
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error((data && data.detail) || "Помилка " + res.status);
  }
  const cd = res.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename\*=UTF-8''([^;]+)/);
  const name = m ? decodeURIComponent(m[1]) : fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = h("a", { href: url, download: name, style: { display: "none" } });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
}

// ---- XML-декларація ФОП 1 групи для Електронного кабінету (F0103407 + Додаток 1)
async function declarationCard(year) {
  const d = await api.get("/api/reports/declaration?year=" + year);
  const st = state.settings || await api.get("/api/settings");
  const F = [ // ключ, назва, тип: t — текст, i — ціле, m — гроші (грн)
    ["fop_name", "ПІБ ФОП", "t"], ["fop_tin", "РНОКПП (10 цифр)", "t"], ["fop_address", "Податкова адреса", "t"],
    ["fop_sti_name", "Податкова, до якої подаєте", "t"], ["fop_c_reg", "Код області (C_REG)", "i"],
    ["fop_c_raj", "Код району (C_RAJ)", "i"], ["fop_c_sti", "Код податкової (C_STI_ORIG)", "i"],
    ["fop_kved", "КВЕД", "t"], ["fop_kved_name", "Назва КВЕД", "t"],
    ["fop_single_tax_month", "Єдиний податок на місяць, грн", "m"], ["fop_min_wage", "Мінімальна зарплата на 1 січня, грн", "m"],
    ["fop_email", "E-mail (необов'язково)", "t"], ["fop_phone", "Телефон (необов'язково)", "t"]];
  const ins = {};
  const form = h("div", { class: "grid", style: { gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "8px" } },
    F.map(([k, label, typ]) => {
      const v = st[k];
      const shown = typ === "m" ? (v ? kopToInput(v) : "") : (v ? String(v) : "");
      ins[k] = h("input", { value: shown,
        inputmode: typ === "t" ? null : "decimal" });
      return field(label, ins[k]);
    }));
  const save = async () => {
    const body = {};
    for (const [k, , typ] of F) {
      const raw = ins[k].value.trim();
      if (typ === "m") { const v = raw ? toKop(raw) : 0; if (!Number.isFinite(v) || v < 0) { toast("Невірна сума: " + k, true); return; } body[k] = v; }
      else if (typ === "i") { const v = raw ? parseInt(raw, 10) : 0; if (!Number.isFinite(v)) { toast("Потрібне число", true); return; } body[k] = v; }
      else body[k] = raw;
    }
    state.settings = await run(() => api.put("/api/settings", body), "Дані ФОП збережено"); reload();
  };
  const line = (label, v) => h("div", { class: "sum-line" }, h("span", {}, label), h("b", {}, money(v)));
  return h("div", { class: "card stack" },
    h("div", { class: "card-head" }, h("h3", {}, `Річна декларація ФОП 1 групи за ${year} рік (XML, F0103407)`),
      h("span", { class: "chip " + (d.ready ? "st-completed" : "st-pending") }, d.ready ? "готово до завантаження" : "заповніть дані")),
    line("Дохід за рік (рядок 01)", d.income),
    line("Єдиний податок за рік (авансові внески)", d.single_tax_year),
    line("Військовий збір (рядок 22)", d.military_levy),
    line("ЄСВ за рік (Додаток 1)", d.esv_year),
    !d.ready ? h("p", { class: "muted small" }, "Не вистачає: " + d.missing.join(", ")) : null,
    st.fop_income_limit && d.income > st.fop_income_limit ? h("p", { style: { color: "var(--danger, #c0392b)", fontWeight: 600 } },
      `Дохід ${money(d.income)} перевищує ліміт групи ${money(st.fop_income_limit)} — зверніться до бухгалтера перед поданням.`) : null,
    h("div", { class: "row" },
      h("button", { class: "btn primary", disabled: !d.ready || null,
        onclick: () => run(() => downloadFile("/api/reports/declaration.zip?year=" + year, `deklaratsiia-fop-${year}.zip`)) },
        "Завантажити декларацію (XML)")),
    h("p", { class: "muted small" }, "В архіві два файли: річна декларація F0103407 і Додаток 1 F0134107 (ЄСВ) — для ФОП 1 групи без найманих працівників. Розпакуйте, у Електронному кабінеті ДПС оберіть " +
      "«Введення звітності → Завантажити файл», завантажте обидва, перевірте цифри, підпишіть Дія.Підписом і відправте. " +
      "Програма нічого не відправляє сама. Ставки й коди податкової звірте з бухгалтером або кабінетом."),
    h("details", {}, h("summary", {}, "Дані ФОП для декларації"), form,
      h("div", { class: "row", style: { marginTop: "8px" } }, h("button", { class: "btn", onclick: save }, "Зберегти дані ФОП"))));
}

VIEWS.taxreport = async (main) => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const nowY = Number(today().slice(0, 4));
  const year = Number(params.get("y")) || nowY;
  const quarter = params.get("q") ? Number(params.get("q")) : null;
  const go = (y, q) => { location.hash = "#/taxreport?y=" + y + (q ? "&q=" + q : ""); };
  const qs = "year=" + year + (quarter ? "&quarter=" + quarter : "");
  const r = await api.get("/api/reports/income?" + qs);
  const period = quarter ? `${quarter} квартал ${year}` : `${year} рік`;

  const qSel = h("select", { "aria-label": "Період", style: { width: "auto" }, onchange: (e) => go(year, e.target.value ? Number(e.target.value) : null) },
    h("option", { value: "" }, "Увесь рік"), [1, 2, 3, 4].map((q) => h("option", { value: q }, `${q} квартал`)));
  qSel.value = quarter ? String(quarter) : "";
  main.append(topbar("Звіт для податкової", period,
    h("button", { class: "btn", "aria-label": "Попередній рік", onclick: () => go(year - 1, quarter) }, icon("left", "sm")),
    h("b", {}, String(year)),
    h("button", { class: "btn", "aria-label": "Наступний рік", onclick: () => go(year + 1, quarter) }, icon("right", "sm")),
    qSel));

  const t = r.total;
  main.append(h("div", { class: "grid kpi" },
    kpi("Дохід за період", t.total, "dark", { ico: "wallet", sub: `${t.count} замовлень` }),
    kpi("Готівка", t.cash, "", { ico: "banknote" }),
    kpi("Картка", t.card, "", { ico: "receipt" }),
    t.transfer ? kpi("Переказ на рахунок", t.transfer, "", { ico: "wallet" }) : null,
    kpi("З початку року", r.year_to_date, "accent", { ico: "trend",
      sub: r.limit.amount ? `ліміт ${money(r.limit.amount)} · використано ${pct(r.limit.used_bp)}` : "ліміт групи не задано" })));

  if (r.limit.amount && r.limit.used_bp >= 8000) {
    main.append(h("div", { class: "card", style: { borderLeft: "4px solid var(--danger, #c0392b)" } },
      h("b", {}, r.limit.used_bp >= 10000 ? "Ліміт доходу групи перевищено!" : "Дохід наближається до ліміту групи"),
      h("p", { class: "muted small" }, `Використано ${pct(r.limit.used_bp)}, залишилось ${money(Math.max(0, r.limit.remaining))}. Зверніться до бухгалтера.`)));
  }

  const tr = !!t.transfer; // колонка «Переказ» — лише якщо такі оплати були
  const row = (label, b, strong) => h("tr", strong ? { class: "total" } : {}, h("td", {}, strong ? h("b", {}, label) : label),
    h("td", { class: "num" }, money(b.cash)), h("td", { class: "num" }, money(b.card)),
    tr ? h("td", { class: "num" }, money(b.transfer)) : null, h("td", { class: "num" }, h("b", {}, money(b.total))), h("td", { class: "num" }, String(b.count)));
  const head = () => h("tr", {}, h("th", {}, ""), h("th", {}, "Готівка"), h("th", {}, "Картка"), tr ? h("th", {}, "Переказ") : null, h("th", {}, "Разом"), h("th", {}, "Замовл."));
  // місяці, згруповані по кварталах; квартал розгортається (поточний — відкритий)
  const curQ = Math.ceil(Number(today().slice(5, 7)) / 3);
  const groups = [1, 2, 3, 4].map((q) => ({ q, months: r.months.filter((m) => Math.ceil(Number(m.month.slice(5, 7)) / 3) === q) }))
    .filter((g) => g.months.length);
  const qTotal = (g) => r.quarters?.find((x) => x.quarter === g.q) || g.months.reduce((a, m) => ({ cash: a.cash + m.cash, card: a.card + m.card,
    transfer: a.transfer + (m.transfer || 0), total: a.total + m.total, count: a.count + m.count }), { cash: 0, card: 0, transfer: 0, total: 0, count: 0 });
  const amounts = (b) => h("div", { class: "q-amounts" },
    h("span", {}, h("small", {}, "Готівка"), money(b.cash)), h("span", {}, h("small", {}, "Картка"), money(b.card)),
    tr ? h("span", {}, h("small", {}, "Переказ"), money(b.transfer)) : null,
    h("span", { class: "q-total" }, h("small", {}, `${b.count} замовл.`), h("b", {}, money(b.total))));
  main.append(h("div", { class: "card q-card" }, h("div", { class: "card-head" }, h("h3", {}, "Дохід по кварталах і місяцях"),
      h("span", { class: "muted small" }, "натисніть на квартал, щоб побачити місяці")),
    groups.map((g) => h("details", { class: "q-group", open: quarter === g.q || (!quarter && year === nowY && g.q === curQ) || null },
      h("summary", {}, h("span", { class: "q-name" }, icon("right", "sm"), `${g.q} квартал`), amounts(qTotal(g))),
      h("div", { class: "q-months" }, g.months.map((m) => h("div", { class: "q-month" }, h("span", { class: "q-name" }, MONTHS_UA[Number(m.month.slice(5)) - 1]), amounts(m)))))),
    h("div", { class: "q-sum" }, h("span", { class: "q-name" }, h("b", {}, quarter ? `${quarter} квартал ${year}` : `${year} рік`)), amounts(t))));

  main.append(h("div", { class: "card stack" }, h("div", { class: "card-head" }, h("h3", {}, "Завантажити для Excel")),
    h("p", { class: "muted small" }, "По днях — як Книга обліку доходів. По замовленнях — для перевірки бухгалтером."),
    h("div", { class: "row" },
      h("button", { class: "btn primary", onclick: () => run(() => downloadFile("/api/reports/income.csv?" + qs + "&detail=day", "dohody.csv")) }, "По днях (CSV)"),
      h("button", { class: "btn", onclick: () => run(() => downloadFile("/api/reports/income.csv?" + qs + "&detail=order", "zamovlennia.csv")) }, "По замовленнях (CSV)"))));

  if (!quarter) main.append(await declarationCard(year));

  const limIn = h("input", { inputmode: "decimal", value: r.limit.amount ? kopToInput(r.limit.amount) : "", placeholder: "напр. 1444049" });
  main.append(h("div", { class: "card stack" }, h("div", { class: "card-head" }, h("h3", {}, "Ліміт доходу групи на рік")),
    h("p", { class: "muted small" }, "Значення на поточний рік вносить бухгалтер (наприклад, для 1 групи у 2026 — 1 444 049 грн). 0 — не стежити."),
    h("div", { class: "row" }, field("Ліміт, грн", limIn),
      h("button", { class: "btn", onclick: async () => {
        const v = limIn.value.trim() ? toKop(limIn.value) : 0;
        if (!Number.isFinite(v) || v < 0) { toast("Вкажіть суму в гривнях", true); return; }
        state.settings = await run(() => api.put("/api/settings", { fop_income_limit: v }), "Ліміт збережено"); reload();
      } }, "Зберегти"))));

  main.append(h("div", { class: "card" }, h("p", { class: "muted small" },
    "Як користуватись: оберіть період декларації, перенесіть «Дохід за період» у кабінет ДПС або Приват24 («Податкові декларації»). " +
    "Дохід — усе, що заплатили клієнти (готівка, картка, переказ), на дату закриття замовлення; комісія банку його не зменшує. " +
    "Податки й звіти подаєте ви або бухгалтер — програма лише рахує.")));
};

const TAX_KEYS = { min_wage: "Мінімальна зарплата", single_tax: "Єдиний податок", military_fop: "Військовий збір (ФОП)", esv_fop: "ЄСВ (ФОП)",
  pdfo: "ПДФО", military_payroll: "Військовий збір (зарплата)", esv_employer: "ЄСВ роботодавця" };
const TAX_BASES = { min_wage: "від мінімальної зарплати", income: "від доходу", fixed: "фіксована сума", salary: "від зарплати", salary_min_wage: "від зарплати, не менше мінімальної" };
VIEWS.taxes = async (main) => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const tab = params.get("t") || "payroll";
  const month = params.get("m") || today().slice(0, 7) + "-01";
  main.append(topbar("Податки і зарплата", "Ставки задаються з датами дії — у коді немає «вічних» значень. Перевіряйте актуальність з бухгалтером."),
    seg([["payroll", "Зарплата"], ["employees", "Працівники"], ["quarter", "Квартальна відомість"], ["fop", "Податки ФОП"], ["rates", "Ставки"]], tab, (v) => { location.hash = `#/taxes?t=${v}&m=${month}`; }));
  const body = h("div", { style: { marginTop: "12px" } });
  main.append(body);
  const pctTxt = (bp) => (bp === null || bp === undefined ? "—" : (bp / 100).toLocaleString("uk-UA") + "%");

  if (tab === "rates") {
    const rates = await api.get("/api/taxes/rates");
    const key = select(Object.entries(TAX_KEYS), "single_tax");
    const from = h("input", { type: "date", value: today() });
    const rate = h("input", { inputmode: "decimal", placeholder: "%" });
    const amount = h("input", { inputmode: "decimal", placeholder: "₴ (для мінімальної зарплати / фіксованої)" });
    const base = select(Object.entries(TAX_BASES), "min_wage");
    const period = select([["month", "щомісяця"], ["quarter", "щокварталу"]], "month");
    const dueDay = h("input", { type: "number", min: 1, max: 31, placeholder: "напр. 20" });
    const dueFrom = select([["start", "від початку періоду"], ["end", "від кінця періоду"]], "end");
    const dueOff = h("input", { type: "number", min: 0, max: 12, value: 0 });
    body.append(h("div", { class: "card stack" }, h("h3", {}, "Нова ставка (діє з дати; попередня завершиться автоматично)"),
      h("div", { class: "form-grid" }, field("Вид", key), field("Діє з", from), field("Ставка, %", rate), field("Сума, ₴", amount)),
      h("div", { class: "form-grid" }, field("База", base), field("Період", period), field("День сплати", dueDay), field("Строк рахується", dueFrom), field("+ місяців", dueOff)),
      h("button", { class: "btn primary", onclick: async () => {
        const b = { key: key.value, valid_from: from.value, base: base.value, period: period.value, due_from: dueFrom.value, due_offset_months: +dueOff.value || 0 };
        if (rate.value.trim()) b.rate_bp = Math.round(parseFloat(rate.value.replace(",", ".")) * 100);
        if (amount.value.trim()) b.amount = toKop(amount.value);
        if (dueDay.value) b.due_day = +dueDay.value;
        await run(() => api.post("/api/taxes/rates", b), "Ставку додано"); reload();
      } }, "Додати ставку")),
      h("div", { class: "card table-wrap", style: { marginTop: "12px" } }, rates.length ? h("table", {},
        h("tr", {}, h("th", {}, "Вид"), h("th", {}, "Значення"), h("th", {}, "База / період"), h("th", {}, "Строк"), h("th", {}, "Діє")),
        rates.map((r) => h("tr", {}, h("td", {}, r.title), h("td", {}, r.amount !== null ? money(r.amount) : pctTxt(r.rate_bp)),
          h("td", { class: "small" }, `${TAX_BASES[r.base] || r.base} · ${r.period === "quarter" ? "квартал" : "місяць"}`),
          h("td", { class: "small" }, r.due_day ? `${r.due_day}-го, ${r.due_from === "start" ? "від початку" : "від кінця"} +${r.due_offset_months} міс.` : "—"),
          h("td", { class: "small" }, `${dmy(r.valid_from)} — ${r.valid_to ? dmy(r.valid_to) : "дотепер"}`)))) : emptyState("Ставок ще немає — додайте мінімальну зарплату та податки", "percent")));
    return;
  }

  if (tab === "fop") {
    const year = +month.slice(0, 4);
    const rows = await api.get("/api/taxes/fop?year=" + year);
    const key = select([["single_tax", "Єдиний податок"], ["military_fop", "Військовий збір"], ["esv_fop", "ЄСВ"]], "single_tax");
    const on = h("input", { type: "date", value: today() });
    body.append(h("div", { class: "card stack" }, h("h3", {}, "Нарахувати за період"),
      h("div", { class: "form-grid" }, field("Податок", key), field("Будь-яка дата періоду", on)),
      h("button", { class: "btn primary", onclick: async () => { await run(() => api.post("/api/taxes/fop/accrue", { key: key.value, on: on.value }), "Нараховано"); reload(); } }, "Нарахувати")),
      h("div", { class: "card table-wrap", style: { marginTop: "12px" } }, rows.length ? h("table", {},
        h("tr", {}, h("th", {}, "Податок"), h("th", {}, "Період"), h("th", { class: "num" }, "Нараховано"), h("th", { class: "num" }, "Сплачено"), h("th", { class: "num" }, "Борг"), h("th", {}, "Строк"), h("th", {}, "")),
        rows.map((r) => h("tr", {}, h("td", {}, r.title), h("td", {}, `${dmy(r.period_start)}${r.period_end ? " — " + dmy(r.period_end) : ""}`),
          h("td", { class: "num" }, money(r.accrued)), h("td", { class: "num" }, money(r.paid)), h("td", { class: "num" }, h("b", { class: r.overdue ? "neg" : "" }, money(r.debt))),
          h("td", {}, r.due_date ? dmy(r.due_date) : "—", r.overdue ? h("span", { class: "chip st-cancelled", style: { marginLeft: "6px" } }, "прострочено") : null),
          h("td", { class: "right", style: { whiteSpace: "nowrap" } },
            r.debt > 0 ? h("button", { class: "btn sm", onclick: () => payForm(r) }, "Сплачено") : null,
            r.accrual_id ? h("button", { class: "btn sm ghost", onclick: () => correctForm(r) }, icon("edit", "sm")) : null)))) : emptyState(`Нарахувань за ${year} рік немає`, "percent")));
    function payForm(r) {
      const amt = h("input", { inputmode: "decimal", value: kopToInput(r.debt) });
      const paidOn = h("input", { type: "date", value: today() });
      openSheet(`Сплата: ${r.title}`, h("div", { class: "stack" }, h("div", { class: "form-grid" }, field("Сума, ₴", amt), field("Дата сплати", paidOn)),
        h("button", { class: "btn primary big", onclick: async () => { await run(() => api.post("/api/taxes/payments", { key: r.key, period_start: r.period_start, amount: toKop(amt.value), paid_on: paidOn.value }), "Сплату записано"); closeSheet(); reload(); } }, "Зберегти")));
    }
    function correctForm(r) {
      const amt = h("input", { inputmode: "decimal", placeholder: "порожньо — перерахувати за ставками" });
      const reason = h("input", { placeholder: "обов'язково" });
      openSheet(`Виправити: ${r.title}`, h("div", { class: "stack" }, h("p", { class: "muted small" }, "Старе нарахування сторнується, створюється нове."),
        h("div", { class: "form-grid" }, field("Нова сума, ₴", amt), field("Причина", reason)),
        h("button", { class: "btn primary big", onclick: async () => { await run(() => api.post(`/api/taxes/fop/${r.accrual_id}/correct`, { reason: reason.value.trim(), amount: amt.value.trim() ? toKop(amt.value) : null }), "Виправлено"); closeSheet(); reload(); } }, "Виправити")));
    }
    return;
  }

  if (tab === "employees") {
    const emps = await api.get("/api/employees");
    body.append(h("div", { class: "callout", style: { marginBottom: "12px" } }, icon("alert"), h("div", { class: "small" },
        "Найманий працівник — це людина з трудовим договором (ФОП 2–3 групи). Заповніть РНОКПП і дату прийому — без них зарплату не нарахувати. " +
        "Мінімальна база ЄСВ застосовується лише за основним місцем роботи і повний місяць.")),
      h("div", { class: "card table-wrap" }, h("table", {},
        h("tr", {}, h("th", {}, "Працівник"), h("th", {}, "РНОКПП"), h("th", {}, "Посада"), h("th", {}, "Прийнято"), h("th", {}, "Звільнено"), h("th", {}, "Місце роботи"), h("th", {}, "")),
        emps.map((e) => h("tr", {}, h("td", {}, e.full_name), h("td", {}, e.tin || "—"), h("td", {}, e.position || "—"),
          h("td", {}, e.hired_on ? dmy(e.hired_on) : "—"), h("td", {}, e.fired_on ? dmy(e.fired_on) : "—"),
          h("td", { class: "small" }, e.is_employee ? (e.main_job ? "основне" : "сумісництво") : h("span", { class: "muted" }, "не найманий")),
          h("td", { class: "right" }, h("button", { class: "btn sm", onclick: () => empForm(e) }, icon("edit", "sm"), "Кадрові дані")))))));
    function empForm(e) {
      const tin = h("input", { value: e.tin || "", inputmode: "numeric", maxlength: 10, placeholder: "10 цифр" });
      const pos = h("input", { value: e.position || "", placeholder: "напр. Перукар" });
      const hired = h("input", { type: "date", value: e.hired_on || "" });
      const fired = h("input", { type: "date", value: e.fired_on || "" });
      const main = h("input", { type: "checkbox", checked: e.main_job !== false });
      openSheet(`Кадрові дані: ${e.full_name}`, h("div", { class: "stack" },
        h("div", { class: "form-grid" }, field("РНОКПП", tin), field("Посада", pos), field("Дата прийому", hired), field("Дата звільнення", fired)),
        h("label", { class: "row" }, main, "Основне місце роботи (не сумісництво)"),
        h("button", { class: "btn primary big", onclick: async () => {
          await run(() => api.put(`/api/employees/${e.user_id}`, { tin: tin.value.trim() || null, position: pos.value.trim() || null,
            hired_on: hired.value || null, fired_on: fired.value || null, main_job: main.checked }), "Збережено");
          closeSheet(); reload();
        } }, "Зберегти")));
    }
    return;
  }

  if (tab === "quarter") {
    const y = +(params.get("y") || today().slice(0, 4));
    const q = +(params.get("q") || (Math.floor((+today().slice(5, 7) - 1) / 3) + 1));
    const go = (yy, qq) => { location.hash = `#/taxes?t=quarter&y=${yy}&q=${qq}`; };
    const qs = select([[1, "I квартал"], [2, "II квартал"], [3, "III квартал"], [4, "IV квартал"]], q);
    qs.addEventListener("change", () => go(y, +qs.value));
    const d = await api.get(`/api/payroll/quarter?year=${y}&quarter=${q}`);
    const t = d.totals;
    body.append(h("div", { class: "row", style: { marginBottom: "10px" } },
        h("button", { class: "btn sm", "aria-label": "Попередній рік", onclick: () => go(y - 1, q) }, icon("left", "sm")), h("b", {}, String(y)),
        h("button", { class: "btn sm", "aria-label": "Наступний рік", onclick: () => go(y + 1, q) }, icon("right", "sm")), qs,
        h("button", { class: "btn sm", onclick: () => run(() => downloadFile(`/api/payroll/quarter.csv?year=${y}&quarter=${q}`, "zarplata.csv")) }, "CSV для бухгалтера")),
      h("p", { class: "muted small" }, "Дані для Податкового розрахунку (ПДФО, військовий збір, ЄСВ): нарахування — за місяцем, виплати — за датою виплати."),
      h("div", { class: "card table-wrap" }, d.rows.length ? h("table", {},
        h("tr", {}, h("th", {}, "Працівник"), h("th", {}, "РНОКПП"), h("th", {}, "Місяць"), h("th", { class: "num" }, "Нараховано"), h("th", { class: "num" }, "ПДФО"),
          h("th", { class: "num" }, "ВЗ"), h("th", { class: "num" }, "База ЄСВ"), h("th", { class: "num" }, "ЄСВ"), h("th", { class: "num" }, "Виплачено")),
        d.rows.map((r) => h("tr", {}, h("td", {}, r.full_name), h("td", {}, r.tin || "—"), h("td", {}, MONTHS_UA[+r.month.slice(5, 7) - 1]),
          h("td", { class: "num" }, money(r.gross)), h("td", { class: "num" }, money(r.pdfo)), h("td", { class: "num" }, money(r.military)),
          h("td", { class: "num" }, money(r.esv_base)), h("td", { class: "num" }, money(r.esv_employer)), h("td", { class: "num" }, money(r.paid)))),
        h("tr", { class: "total" }, h("td", {}, h("b", {}, "Разом")), h("td", {}), h("td", {}), h("td", { class: "num" }, money(t.gross)), h("td", { class: "num" }, money(t.pdfo)),
          h("td", { class: "num" }, money(t.military)), h("td", { class: "num" }, money(t.esv_base)), h("td", { class: "num" }, money(t.esv_employer)), h("td", { class: "num" }, money(t.paid))))
        : emptyState("За цей квартал нарахувань зарплати немає", "users")),
      h("p", { class: "muted small" }, `Працівників: ${d.employees} · До бюджету (ПДФО + ВЗ + ЄСВ): ${money(t.to_budget)}`));
    return;
  }

  // офіційна зарплата
  const data = await api.get("/api/payroll?month=" + month);
  const staff = (await api.get("/api/employees")).filter((u) => u.is_employee).map((e) => ({ id: e.user_id, full_name: e.full_name }));
  if (!staff.length) {
    body.append(h("div", { class: "card" }, emptyState("Немає найманих працівників — заповніть кадрові дані у вкладці «Працівники»", "users")));
  }
  const userSel = select(staff.map((u) => [u.id, u.full_name]), staff[0]?.id);
  const gross = h("input", { inputmode: "decimal", placeholder: "нараховано (брутто), ₴" });
  const preview = h("div", { class: "muted small" });
  gross.addEventListener("input", async () => {
    const g = toKop(gross.value);
    if (!(g >= 0)) { preview.textContent = ""; return; }
    try {
      const c = await api.get(`/api/payroll/calc?gross=${g}&month=${month}${userSel.value ? "&user_id=" + userSel.value : ""}`);
      preview.textContent = `ПДФО ${money(c.pdfo)} · ВЗ ${money(c.military)} · на руки ${money(c.net)} · ЄСВ роботодавця ${money(c.esv_employer)}`;
    } catch (e) { preview.textContent = e.message; }
  });
  const t = data.totals;
  body.append(h("div", { class: "row", style: { marginBottom: "10px" } },
      h("button", { class: "btn sm", onclick: () => { location.hash = `#/taxes?t=payroll&m=${addMonths(month, -1)}`; } }, icon("left", "sm")),
      h("b", {}, monthTitle(month)),
      h("button", { class: "btn sm", onclick: () => { location.hash = `#/taxes?t=payroll&m=${addMonths(month, 1)}`; } }, icon("right", "sm"))),
    h("div", { class: "callout", style: { marginBottom: "12px" } }, icon("alert"), h("div", { class: "small" }, "Це юридичне нарахування зарплати. Управлінський поділ 50/50 (заробіток майстрів у системі) рахується окремо і не змінюється.")),
    h("div", { class: "card stack" }, h("h3", {}, "Нарахувати зарплату"), h("div", { class: "form-grid" }, field("Працівник", userSel), field("Сума (брутто), ₴", gross)), preview,
      h("button", { class: "btn primary", onclick: async () => { await run(() => api.post("/api/payroll", { user_id: +userSel.value, month, gross: toKop(gross.value) }), "Нараховано"); reload(); } }, "Нарахувати")),
    h("div", { class: "card table-wrap", style: { marginTop: "12px" } }, data.rows.length ? h("table", {},
      h("tr", {}, h("th", {}, "Працівник"), h("th", { class: "num" }, "Брутто"), h("th", { class: "num" }, "ПДФО"), h("th", { class: "num" }, "ВЗ"), h("th", { class: "num" }, "На руки"), h("th", { class: "num" }, "ЄСВ"), h("th", { class: "num" }, "Виплачено"), h("th", { class: "num" }, "Борг"), h("th", {}, "")),
      data.rows.map((r) => h("tr", {}, h("td", {}, r.user_name), h("td", { class: "num" }, money(r.gross)), h("td", { class: "num" }, money(r.pdfo)), h("td", { class: "num" }, money(r.military)),
        h("td", { class: "num" }, h("b", {}, money(r.net))), h("td", { class: "num" }, money(r.esv_employer)), h("td", { class: "num" }, money(r.paid)), h("td", { class: "num" }, h("b", {}, money(r.debt))),
        h("td", { class: "right", style: { whiteSpace: "nowrap" } },
          r.debt > 0 ? h("button", { class: "btn sm", onclick: () => salaryPay(r) }, "Виплатити") : null,
          r.accrual_id ? h("button", { class: "btn sm ghost", onclick: () => salaryFix(r) }, icon("edit", "sm")) : null))),
      h("tr", {}, h("td", {}, h("b", {}, "Разом")), h("td", { class: "num" }, money(t.gross)), h("td", { class: "num" }, money(t.pdfo)), h("td", { class: "num" }, money(t.military)),
        h("td", { class: "num" }, money(t.net)), h("td", { class: "num" }, money(t.esv_employer)), h("td", { class: "num" }, money(t.paid)), h("td", { class: "num" }, money(t.debt)), h("td", {})))
      : emptyState("За цей місяць нарахувань немає", "users")),
    h("p", { class: "muted small" }, `До бюджету: ${money(t.to_budget)} · Витрати роботодавця (брутто + ЄСВ): ${money(t.employer_cost)}`));
  function salaryPay(r) {
    const amt = h("input", { inputmode: "decimal", value: kopToInput(r.debt) });
    const paidOn = h("input", { type: "date", value: today() });
    openSheet(`Виплата: ${r.user_name}`, h("div", { class: "stack" }, h("div", { class: "form-grid" }, field("Сума, ₴", amt), field("Дата", paidOn)),
      h("button", { class: "btn primary big", onclick: async () => { await run(() => api.post("/api/payroll/payments", { user_id: r.user_id, month, amount: toKop(amt.value), paid_on: paidOn.value }), "Виплату записано"); closeSheet(); reload(); } }, "Зберегти")));
  }
  function salaryFix(r) {
    const g = h("input", { inputmode: "decimal", value: kopToInput(r.gross) });
    const reason = h("input", { placeholder: "обов'язково" });
    openSheet(`Виправити нарахування: ${r.user_name}`, h("div", { class: "stack" }, h("div", { class: "form-grid" }, field("Брутто, ₴", g), field("Причина", reason)),
      h("button", { class: "btn primary big", onclick: async () => { await run(() => api.post(`/api/payroll/${r.accrual_id}/correct`, { gross: toKop(g.value), reason: reason.value.trim() }), "Виправлено"); closeSheet(); reload(); } }, "Виправити")));
  }
};

VIEWS.journal = async (main) => {
  const CATS = [["", "Усе"], ["money", "Гроші"], ["records", "Записи"], ["clients", "Клієнти"], ["stock", "Склад"], ["staff", "Персонал і налаштування"]];
  const CAT_COLOR = { money: "var(--ok)", records: "var(--brand-2)", clients: "#7a4e6b", stock: "var(--gold)", staff: "var(--ink-3)" };
  const PERIODS = [["today", "Сьогодні"], ["week", "7 днів"], ["month", "30 днів"]];
  let cat = "", period = "week", q = "", timer = null;
  const range = () => {
    const to = today();
    return [period === "today" ? to : addDays(to, period === "week" ? -6 : -29), to];
  };
  const body = h("div", { class: "stack" });
  const catRow = h("div", { class: "row chips-scroll" });
  const drawCats = () => catRow.replaceChildren(...CATS.map(([id, label]) =>
    h("button", { class: "btn sm" + (cat === id ? " primary" : ""), onclick: () => { cat = id; drawCats(); load(); } }, label)));
  const search = h("input", { type: "search", placeholder: "Пошук: ім'я, послуга, сума, причина…", oninput: (e) => {
    clearTimeout(timer); timer = setTimeout(() => { q = e.target.value.trim(); load(); }, 300);
  } });
  main.append(topbar("Журнал", "Усе, що відбувалося в салоні: хто, що і коли зробив"),
    h("div", { class: "row", style: { marginBottom: "10px", flexWrap: "wrap" } },
      seg(PERIODS, period, (v) => { period = v; load(); }),
      h("div", { class: "search grow", style: { maxWidth: "380px", minWidth: "220px" } }, icon("search", "sm"), search)),
    catRow, body,
    h("details", { class: "card", style: { marginTop: "16px" } }, h("summary", { class: "small" }, "Для бухгалтера: фінансові проведення (технічний журнал)"),
      h("div", { id: "ledger-box", class: "table-wrap", style: { marginTop: "10px" } })));
  drawCats();

  const dayTitle = (iso) => {
    const d = iso.slice(0, 10);
    if (d === today()) return "Сьогодні";
    if (d === addDays(today(), -1)) return "Вчора";
    return `${WD_LONG[(new Date(d + "T12:00").getDay() + 6) % 7]}, ${dmy(d)}`;
  };
  const item = (x) => h("div", { class: "jr-item" },
    h("span", { class: "jr-ico", style: { color: CAT_COLOR[x.category] || "var(--ink-3)" } }, icon(x.icon || "history", "sm")),
    h("div", { class: "grow", style: { minWidth: 0 } },
      h("div", { class: "jr-head" }, h("b", {}, x.title), x.amount !== null && x.amount !== undefined ? h("b", { class: "jr-amount" }, money(x.amount)) : null),
      x.text ? h("div", { class: "jr-text" }, x.text) : null,
      x.changes.length ? h("div", { class: "jr-changes" }, x.changes.map((c) => h("div", {},
        h("span", { class: "muted" }, c.field + ": "), h("s", { class: "muted" }, c.before), " → ", h("b", {}, c.after)))) : null,
      x.reason ? h("div", { class: "jr-reason" }, "Причина: ", x.reason) : null,
      h("div", { class: "jr-meta" }, `${hm(x.ts)} · ${x.who}`)));

  async function load() {
    const [from, to] = range();
    body.replaceChildren(h("div", { class: "empty" }, "Завантаження…"));
    const params = new URLSearchParams({ date_from: from, date_to: to });
    if (cat) params.set("category", cat);
    if (q) params.set("q", q);
    const list = await api.get("/api/journal/feed?" + params);
    if (!list.length) { body.replaceChildren(h("div", { class: "card" }, emptyState(q ? "Нічого не знайдено" : "Подій за цей період немає", "history"))); return; }
    const groups = new Map();
    for (const x of list) { const k = x.ts.slice(0, 10); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(x); }
    body.replaceChildren(...[...groups.entries()].map(([d, items]) => h("div", {},
      h("div", { class: "jr-day" }, dayTitle(d), h("span", { class: "muted" }, ` · ${items.length}`)),
      h("div", { class: "card jr-list" }, items.map(item)))));
  }
  await load();

  // технічний фінансовий журнал — лише за потреби (згорнутий)
  const KIND = { order_closed: "Закрито замовлення", order_reversal: "Сторно (виправлення)", order_corrected: "Виправлене замовлення", payout: "Виплата майстру",
    shift_closed: "Зміну закрито", expense: "Витрата", expense_reversal: "Сторно витрати" };
  const userName = (id) => (state.allUsers.find((u) => u.id === id) || {}).full_name || (id ? "#" + id : "—");
  main.querySelector("details").addEventListener("toggle", async (e) => {
    if (!e.target.open || e.target.dataset.loaded) return;
    e.target.dataset.loaded = "1";
    const list = await api.get("/api/journal/ledger?limit=300");
    main.querySelector("#ledger-box").replaceChildren(h("table", {},
      h("tr", {}, h("th", {}, "Час"), h("th", {}, "Операція"), h("th", {}, "Майстер"), h("th", { class: "num" }, "Сума"), h("th", { class: "num" }, "Майстру"), h("th", { class: "num" }, "Салону"), h("th", {}, "Хто")),
      list.map((x) => h("tr", {}, h("td", {}, dm(x.ts), " ", hm(x.ts)), h("td", {}, KIND[x.kind] || x.kind), h("td", {}, x.master_id ? userName(x.master_id) : ""),
        h("td", { class: "num" }, money(x.amount)), h("td", { class: "num" }, money(x.master_amount)), h("td", { class: "num" }, money(x.salon_amount)), h("td", { class: "small muted" }, userName(x.user_id))))));
  });
};

boot();
