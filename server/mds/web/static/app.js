/* MDS Connect — веб-панель (PWA) для майстрів, адміністратора та директора.
 *
 * Без збірки та залежностей: один файл, ванільний JS.
 * Структура:  api → утиліти → каркас (навігація, модальні вікна) → екрани (VIEWS).
 * Усі гроші з API — цілі копійки; на екрані — гривні.
 * DOM будується через h() — без innerHTML для даних користувача (захист від XSS).
 */
"use strict";

// ============================================================ API
const api = {
  token: localStorage.getItem("mds_token"),
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
  const n = parseFloat(String(s).replace(/\s/g, "").replace(",", "."));
  return Number.isFinite(n) ? Math.round(n * 100) : NaN;
};
const priceText = (s) => (s.price_from ? "від " : "") + money(s.price);
const kopToInput = (k) => ((k || 0) / 100).toString();
const pct = (bp) => (bp / 100).toLocaleString("uk-UA") + "%";
const pad = (n) => String(n).padStart(2, "0");
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
const EXP_CAT = { rent: "Оренда", utilities: "Комунальні", advertising: "Реклама", materials: "Закупівля матеріалів", other: "Інше" };

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

function kpi(label, value, cls = "", { ico = null, prev, vs = null, sub = null, raw = false } = {}) {
  // vs — з чим порівнюємо («мин. місяць»): показується лише коли є дані за попередній період
  const d = deltaEl(value, prev);
  return h("div", { class: "card kpi-tile " + cls },
    h("div", { class: "kpi-top" }, ico ? h("span", { class: "kpi-ico" }, icon(ico, "sm")) : null, h("span", { class: "kpi-label" }, label)),
    h("div", { class: "kpi-value" + (value < 0 ? " neg" : "") }, raw ? value : money(value)),
    d || sub ? h("div", { class: "row small muted" }, d, d && vs ? vs : null, sub) : null);
}

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
        context: { device: navigator.userAgent, screen: `${window.innerWidth}×${window.innerHeight}`, version: "web", errors: recentErrors },
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
  admin: [["calendar", "calendar", "Календар", "Робота"], ["schedule", "clock", "Графік роботи", "Робота"], ["clients", "users", "Клієнти", "Робота"],
    ["stock", "box", "Склад", "Робота"], ["profile", "user", "Профіль"]],
  director: [
    ["dashboard", "chart", "Огляд", "Огляд"],
    ["calendar", "calendar", "Календар", "Робота"], ["schedule", "clock", "Графік роботи", "Робота"], ["clients", "users", "Клієнти", "Робота"],
    ["stock", "box", "Склад", "Робота"],
    ["orders", "receipt", "Замовлення", "Фінанси"], ["shifts", "banknote", "Зміни й виплати", "Фінанси"], ["expenses", "wallet", "Витрати", "Фінанси"],
    ["staff", "user", "Персонал", "Салон"], ["catalog", "scissors", "Послуги", "Салон"],
    ["settings", "settings", "Налаштування", "Салон"], ["journal", "book", "Журнал", "Салон"],
    ["profile", "user", "Профіль"],
  ],
};
const MOBILE_TABS = { director: ["dashboard", "calendar", "orders", "stock", "more"], admin: ["calendar", "schedule", "stock", "clients", "profile"] };

function currentRoute() { return (location.hash.replace(/^#\/?/, "") || "").split("?")[0]; }

function logout() {
  api.token = null;
  localStorage.removeItem("mds_token");
  state.user = null;
  renderLogin();
}

async function boot() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
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
  setThemeColor("#0b1f19"); // темна смужка статусу над темним екраном входу
  const user = h("input", { autocomplete: "username", autocapitalize: "none", placeholder: "Логін", id: "login-user" });
  const pass = h("input", { type: "password", autocomplete: "current-password", placeholder: "Пароль", id: "login-pass" });
  const btn = h("button", { class: "btn login-btn big", type: "submit" }, "Увійти");
  const form = h("form", { class: "stack", onsubmit: async (e) => {
    e.preventDefault();
    btn.disabled = true;
    try {
      const r = await api.post("/api/auth/login", { username: user.value, password: pass.value });
      api.token = r.token;
      localStorage.setItem("mds_token", r.token);
      location.hash = "";
      await boot();
    } catch (err) {
      toast(err.message, true);
      btn.disabled = false;
    }
  } }, field("Логін", user), field("Пароль", pass), btn);
  document.getElementById("root").replaceChildren(
    h("div", { class: "login" },
      h("div", { class: "login-card" },
        h("div", { class: "login-mark" }, "M&D"),
        h("div", { class: "login-name" }, "MDS Connect"),
        h("div", { class: "login-sub" }, "Beauty salon M&D"),
        form),
      h("div", { class: "login-foot" }, "Внутрішня система салону · доступ лише для персоналу")));
  autoFocus(user);
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
      h("div", {}, h("div", { class: "brand-name" }, "MDS Connect"), h("div", { class: "brand-sub" }, state.settings?.salon_name ? "Салон " + state.settings.salon_name : "Салон M&D"))),
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
  const mbar = h("header", { class: "mbar" }, h("div", { class: "brand-mark" }, "M&D"), h("b", { class: "grow" }, title),
    h("button", { class: "icon-btn", "aria-label": "Повідомити про помилку", onclick: bugReportForm }, icon("bug")));
  main.append(mbar);
  document.getElementById("root").replaceChildren(h("div", { class: "app" }, sidebar, main), tabbar);
  closeSheet();
  const view = route === "more" ? viewMore : VIEWS[route];
  Promise.resolve(view(main)).catch((e) => { main.replaceChildren(emptyState(e.message, "alert")); });
}

function topbar(title, ...actions) {
  let sub = null;
  if (actions.length && typeof actions[0] === "string") sub = actions.shift();
  return h("div", { class: "topbar" },
    h("div", {}, h("h1", {}, title), sub ? h("div", { class: "sub" }, sub) : null),
    h("div", { class: "row" }, actions));
}

function viewMore(main) {
  main.append(topbar("Меню"));
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  main.append(h("div", { class: "card", style: { padding: "6px" } }, NAV[state.user.role].map(([id, ico, label]) =>
    h("a", { href: "#/" + id, class: "row", style: { padding: "12px", color: "var(--ink)" } }, icon(ico), h("span", { class: "grow" }, label), icon("right", "sm"))),
    h("button", { class: "btn ghost", style: { width: "100%", justifyContent: "flex-start", padding: "12px" }, onclick: bugReportForm }, icon("bug"), "Повідомити про помилку"),
    h("button", { class: "btn ghost", style: { width: "100%", justifyContent: "flex-start", padding: "12px" }, onclick: toggleTheme }, icon(dark ? "sun" : "moon"), dark ? "Світла тема" : "Темна тема"),
    h("button", { class: "btn ghost", style: { width: "100%", justifyContent: "flex-start", padding: "12px", color: "var(--danger)" }, onclick: logout }, icon("logout"), "Вийти")));
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

// кількість матеріалу: «30 г (½ туби)», «2 шт»
const fmtNum = (x) => (Math.round(x * 100) / 100).toLocaleString("uk-UA");
function fmtQty(m, q) {
  if (m.pack_size > 1) return `${fmtNum(q)} ${m.unit} · ${fmtNum(q / m.pack_size)} ${m.pack_name}`;
  return `${fmtNum(q)} ${m.unit}`;
}
const matCost = (m, q) => Math.round((m.unit_cost * q) / (m.pack_size || 1));

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
    found.length > (s ? 30 : 8) ? h("div", { class: "small muted", style: { padding: "6px 4px" } }, `Ще ${found.length - 8}… уточніть пошук`) : null,
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
  const methods = h("div", { class: "choice", style: { gridTemplateColumns: "1fr 1fr" } }, Object.entries(PAY_CHOICES).map(([k, t]) =>
    h("button", { type: "button", onclick: (e) => { method = k; [...methods.children].forEach((b) => b.classList.remove("on")); e.currentTarget.classList.add("on"); } }, icon(PAY_ICON[k]), t)));
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
  const go = (dd) => { location.hash = "#/calendar?d=" + dd; };
  const dateInput = h("input", { type: "date", value: day, style: { width: "auto" }, "aria-label": "Дата", onchange: (e) => go(e.target.value) });
  main.append(topbar("Календар", `${WD_LONG[(new Date(day + "T12:00").getDay() + 6) % 7]}, ${dmy(day)}`,
    h("button", { class: "btn", "aria-label": "Попередній день", onclick: () => go(addDays(day, -1)) }, icon("left", "sm")),
    h("button", { class: "btn", onclick: () => go(today()) }, "Сьогодні"),
    h("button", { class: "btn", "aria-label": "Наступний день", onclick: () => go(addDays(day, 1)) }, icon("right", "sm")),
    dateInput,
    h("button", { class: "btn primary", onclick: () => apptForm({ date: day }, reload) }, icon("plus", "sm"), "Новий запис")));

  // стрічка днів: тиждень довкола обраного дня
  const wdIdx = (iso) => String((new Date(iso + "T12:00").getDay() + 6) % 7);
  const salonSched = state.settings.schedule || {};
  const strip = h("div", { class: "daystrip" });
  for (let i = -3; i <= 10; i++) {
    const dd = addDays(day, i);
    const closed = !salonSched[wdIdx(dd)];
    strip.append(h("button", { class: (dd === day ? "on " : "") + (dd === today() ? "today " : "") + (closed ? "off" : ""), onclick: () => go(dd), title: closed ? "Вихідний" : "" },
      h("small", {}, weekday(dd)), h("b", {}, Number(dd.slice(8, 10)))));
  }
  main.append(strip);

  const list = await api.get(`/api/appointments?date_from=${day}`);
  const pend = await pendingBlock(reload);
  if (pend) main.append(pend);
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
  let startMin = 24 * 60, endMin = 0;
  for (const m of masters) { const sp = spanOf(m); if (sp) { startMin = Math.min(startMin, toMin(sp[0])); endMin = Math.max(endMin, toMin(sp[1])); } }
  for (const a of list) { startMin = Math.min(startMin, toMin(hm(a.starts_at))); endMin = Math.max(endMin, toMin(hm(a.ends_at))); }
  if (startMin >= endMin) { startMin = 9 * 60; endMin = 20 * 60; }
  startMin = Math.floor(startMin / 60) * 60; endMin = Math.ceil(endMin / 60) * 60;
  const STEP = 30, ROW = 40, HEAD = 52, TIMEW = 58;
  const rows = (endMin - startMin) / STEP;
  const colW = isMaster ? "minmax(240px, 1fr)" : "minmax(180px, 1fr)";
  const activeList = list.filter((a) => !["cancelled", "no_show"].includes(a.status));

  const grid = h("div", { class: "cal-grid", style: { gridTemplateColumns: `${TIMEW}px repeat(${masters.length}, ${colW})`, gridTemplateRows: `${HEAD}px repeat(${rows}, ${ROW}px)` } });
  grid.append(h("div", { class: "cal-corner" }));
  masters.forEach((m) => {
    const n = activeList.filter((a) => a.master_id === m.id).length;
    const sp = spanOf(m);
    grid.append(h("div", { class: "cal-head" }, avatar(m.full_name, "sm"),
      h("div", { class: "who" }, h("b", {}, m.full_name), h("span", {}, sp ? `${sp[0]}–${sp[1]} · записів: ${n}` : "вихідний"))));
  });
  for (let r = 0; r < rows; r++) {
    const min = startMin + r * STEP;
    const time = `${pad(Math.floor(min / 60))}:${pad(min % 60)}`;
    grid.append(h("div", { class: "cal-time" + (r === 0 ? " first" : "") }, min % 60 === 0 ? time : ""));
    masters.forEach((m) => {
      const sp = spanOf(m);
      const off = !sp || min < toMin(sp[0]) || min >= toMin(sp[1]);
      grid.append(h("div", { class: "cal-cell" + (off ? " off" : "") + (min % 60 ? " half" : ""), title: off ? "Неробочий час" : `Новий запис · ${m.full_name} · ${time}`,
        onclick: off ? null : () => apptForm({ master_id: m.id, date: day, time }, reload) }));
    });
  }
  const wrap = h("div", { class: "cal" }, grid);
  main.append(wrap);
  main.append(h("div", { class: "legend" },
    ["pending", "confirmed", "in_progress", "completed"].map((st) => chip(st)),
    h("span", {}, "Клікніть у вільну клітинку, щоб створити запис")));
  // події позиціонуємо після рендеру, коли відомі ширини колонок
  requestAnimationFrame(() => {
    const heads = grid.querySelectorAll(".cal-head");
    activeList.forEach((a) => {
      const col = masters.findIndex((m) => m.id === a.master_id);
      if (col < 0) return;
      const head = heads[col];
      const top = HEAD + ((toMin(hm(a.starts_at)) - startMin) / STEP) * ROW;
      const height = Math.max(((toMin(hm(a.ends_at)) - toMin(hm(a.starts_at))) / STEP) * ROW - 3, 24);
      grid.append(h("div", { class: "cal-ev " + a.status, title: `${hm(a.starts_at)}–${hm(a.ends_at)} ${a.client.name} · ${a.service_name}`,
        style: { top: top + 2 + "px", height: height + "px", left: head.offsetLeft + 4 + "px", width: head.offsetWidth - 8 + "px" },
        onclick: () => apptSheet(a, reload) },
        h("b", {}, a.client.name), h("div", { class: "t" }, `${hm(a.starts_at)}–${hm(a.ends_at)}`), height > 50 ? h("div", {}, a.service_name) : null));
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
  const cancelled = list.filter((a) => ["cancelled", "no_show"].includes(a.status));
  if (cancelled.length) {
    main.append(h("details", { class: "card", style: { marginTop: "12px" } },
      h("summary", { class: "muted", style: { cursor: "pointer" } }, `Скасовані та неявки: ${cancelled.length}`),
      cancelled.map((a) => h("div", { class: "row", style: { padding: "6px 0" } }, h("b", {}, hm(a.starts_at)), a.client.name, h("span", { class: "muted grow" }, a.master_name, " · ", a.service_name), chip(a.status)))));
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
  main.append(topbar("Профіль"));
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
      } }, "Змінити пароль"))));
};

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
    const d = await api.get("/api/finance/dashboard?" + q);
    const s = d.summary;
    const [pf, pt] = prevRange(s.from, s.to, kind);
    const p = (await api.get(`/api/finance/dashboard?date_from=${pf}&date_to=${pt}`).catch(() => null))?.summary || {};
    const vs = kind === "month" ? "мин. місяць" : kind === "year" ? "мин. рік" : "попер. період";
    const exp = s.expenses;
    const expRows = [["Оренда", exp.rent, "home"], ["Комунальні", exp.utilities, "zap"], ["Реклама", exp.advertising, "megaphone"], ["Інші витрати", exp.other, "receipt"]];
    body.replaceChildren(...[
      h("div", { class: "grid kpi" },
        kpi("Виручка салону", s.revenue, "dark", { ico: "banknote", prev: p.revenue, vs }),
        kpi("Чистий прибуток", s.net_profit, "accent", { ico: "trend", prev: p.net_profit, vs }),
        kpi("Валовий прибуток", s.gross_profit, "", { ico: "chart", prev: p.gross_profit }),
        kpi("Середній чек", s.average_check, "", { ico: "receipt", prev: p.average_check, sub: `${s.orders_count} замовл. · ${s.clients_count} клієнтів` })),
      h("div", { class: "grid two", style: { marginTop: "12px" } },
        h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Куди йдуть гроші"), h("span", { class: "muted small" }, `${dmy(s.from)} — ${dmy(s.to)}`)),
          h("div", { class: "sum-line" }, h("span", {}, "Виручка"), h("b", {}, money(s.revenue))),
          h("div", { class: "sum-line" }, h("span", { class: "muted" }, "− Частка майстрів"), h("span", {}, money(s.masters_share))),
          h("div", { class: "sum-line" }, h("span", { class: "muted" }, "− Матеріали"), h("span", {}, money(s.materials))),
          h("div", { class: "sum-line total" }, h("span", {}, "Валовий прибуток"), h("span", {}, money(s.gross_profit))),
          expRows.map(([l, v]) => h("div", { class: "sum-line" }, h("span", { class: "muted" }, "− " + l), h("span", {}, money(v)))),
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

// ---------- Зміни й виплати
VIEWS.shifts = async (main) => {
  const [closed, all, payouts] = await Promise.all([api.get("/api/shifts?status=closed"), api.get("/api/shifts"), api.get("/api/payouts")]);
  main.append(topbar("Зміни й виплати", h("button", { class: "btn", onclick: () => payoutForm(null) }, "Інша виплата / аванс")));
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
  const body = h("div", { style: { marginTop: "12px" } });
  const mst = select([["", "Усі майстри"], ...state.masters.map((m) => [m.id, m.full_name])], "", { style: { width: "auto" } });
  let q = "period=month";
  const load = async () => {
    const list = await api.get(`/api/orders?${q}${mst.value ? "&master_id=" + mst.value : ""}`);
    const total = list.reduce((a, o) => a + o.amount, 0);
    body.replaceChildren(list.length ? h("div", { class: "card table-wrap" }, h("table", {},
      h("tr", {}, h("th", { class: "m-hide" }, "#"), h("th", {}, "Дата"), h("th", {}, "Майстер"), h("th", {}, "Послуга"), h("th", {}, "Клієнт"), h("th", { class: "m-hide" }, "Оплата"),
        h("th", { class: "num" }, "Сума"), h("th", { class: "num m-hide" }, "Матеріали"), h("th", { class: "num" }, "Майстру"), h("th", { class: "num m-hide" }, "Салону")),
      list.map((o) => h("tr", { class: "click", onclick: () => orderSheet(o.id, load) }, h("td", {}, o.id, o.revision > 1 ? " ✎" : ""), h("td", {}, dm(o.closed_at), " ", hm(o.closed_at)),
        h("td", {}, o.master_name), h("td", {}, o.service_name), h("td", {}, o.client_name), h("td", {}, PAY[o.payment_method]),
        h("td", { class: "num" }, money(o.amount)), h("td", { class: "num" }, money(o.materials_cost)), h("td", { class: "num" }, money(o.master_earning)), h("td", { class: "num" }, money(o.salon_earning)))),
      h("tr", {}, h("td", { colspan: 6 }, h("b", {}, "Разом: ", list.length)), h("td", { class: "num" }, h("b", {}, money(total))), h("td", { colspan: 3 }))))
      : h("div", { class: "card" }, emptyState("Немає замовлень за період")));
  };
  mst.addEventListener("change", load);
  main.append(topbar("Замовлення"), h("div", { class: "row" }, periodPicker((x) => { q = x; load(); }), mst), body);
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
        list.map((e) => h("tr", {}, h("td", {}, dmy(e.spent_on)), h("td", {}, EXP_CAT[e.category]), h("td", {}, e.description || ""), h("td", { class: "num" }, money(e.amount)),
          h("td", { class: "right" }, h("button", { class: "btn sm danger", onclick: () => confirmSheet("Видалити витрату?", `${EXP_CAT[e.category]} ${money(e.amount)}`, "Видалити",
            async (reason) => { await run(() => api.del(`/api/expenses/${e.id}?reason=${encodeURIComponent(reason)}`), "Видалено"); load(); }, { danger: true, withReason: true }) }, "×"))))))
        : h("div", { class: "card", style: { marginTop: "12px" } }, emptyState("Витрат за період немає")));
  };
  const add = () => {
    const cat = select(Object.entries(EXP_CAT), "rent");
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
  main.append(topbar("Витрати", h("button", { class: "btn gold", onclick: add }, "+ Витрата")), periodPicker((x) => { q = x; load(); }), body);
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
  main.append(topbar("Персонал", h("button", { class: "btn gold", onclick: () => userForm(null) }, "+ Співробітник")));
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

function materialForm(m, list, onSaved) {
  const uniq = (k) => [...new Set(list.map((x) => x[k]).filter(Boolean))];
  const name = h("input", { value: m?.name || "", placeholder: "Напр. Igora Royal", list: "dl-mname" });
  const tone = h("input", { value: m?.tone || "", placeholder: "Напр. 7-1 (для фарб)" });
  const cat = h("input", { value: m?.category || "", placeholder: "Фарби, Окисники, Догляд…", list: "dl-mcat" });
  const unit = select([["г", "грами (г)"], ["мл", "мілілітри (мл)"], ["шт", "штуки (шт)"]], m?.unit || "г");
  const packName = h("input", { value: m?.pack_name || "туба", placeholder: "туба, банка, шт" });
  const packSize = h("input", { type: "number", min: 1, step: 1, value: m?.pack_size || 60 });
  const cost = h("input", { inputmode: "decimal", value: m ? kopToInput(m.unit_cost) : "", placeholder: "ціна однієї упаковки" });
  const min = h("input", { type: "number", min: 0, step: "any", value: m?.min_stock ?? "", placeholder: "напр. 60" });
  const initial = h("input", { type: "number", min: 0, step: "any", placeholder: "у г / мл / шт" });
  const active = h("input", { type: "checkbox", checked: m ? m.is_active : true });
  const syncUnit = () => { if (unit.value === "шт") { packSize.value = 1; packName.value = "шт"; } };
  unit.addEventListener("change", syncUnit);
  openSheet(m ? m.display_name : "Новий матеріал", h("div", { class: "stack" },
    h("datalist", { id: "dl-mname" }, uniq("name").map((x) => h("option", { value: x }))),
    h("datalist", { id: "dl-mcat" }, uniq("category").map((x) => h("option", { value: x }))),
    h("div", { class: "form-grid" }, field("Назва / лінійка", name), field("Тон", tone)),
    field("Категорія", cat),
    h("div", { class: "callout" }, icon("box"), h("div", { class: "small" },
      "Облік ведеться в ", h("b", {}, "г / мл / шт"), ". Упаковка — як купуєте: напр. туба 60 г. ",
      "Майстер списує або цілу тубу, або частину (½ туби, 20 г).")),
    h("div", { class: "form-grid" }, field("Одиниця обліку", unit), field("Упаковка (назва)", packName), field("В упаковці (г / мл / шт)", packSize)),
    h("div", { class: "form-grid" }, field("Ціна упаковки, ₴", cost), field("Мінімальний залишок", min), m ? null : field("Є зараз на складі", initial)),
    m ? h("label", { class: "row" }, active, "Використовується") : null,
    h("button", { class: "btn primary big", onclick: async () => {
      const body = { name: name.value.trim(), tone: tone.value.trim() || null, category: cat.value.trim() || null, unit: unit.value,
        pack_name: packName.value.trim() || unit.value, pack_size: Math.max(1, parseInt(packSize.value || "1", 10)),
        unit_cost: toKop(cost.value), min_stock: min.value === "" ? null : parseFloat(min.value) };
      if (!m && initial.value) body.stock = parseFloat(initial.value);
      if (m) body.is_active = active.checked;
      await run(() => (m ? api.patch("/api/materials/" + m.id, body) : api.post("/api/materials", body)), "Збережено");
      closeSheet(); onSaved();
    } }, "Зберегти")));
}

function stockMoveForm(m, kind, onSaved) {
  const qty = h("input", { type: "number", min: 0, step: "any", value: kind === "adjust" ? m.stock : "" });
  const packs = h("input", { type: "number", min: 0, step: "any", placeholder: m.pack_name });
  const cost = h("input", { inputmode: "decimal", placeholder: "за накладною, ₴" });
  const asExp = h("input", { type: "checkbox", checked: state.user.role === "director" });
  const note = h("input", { placeholder: kind === "writeoff" ? "Обов'язково: чому списуємо" : "Примітка" });
  if (m.pack_size > 1) packs.addEventListener("input", () => { qty.value = packs.value ? +(parseFloat(packs.value) * m.pack_size).toFixed(3) : ""; });
  const title = { receipt: "Прихід", adjust: "Інвентаризація", writeoff: "Списання" }[kind];
  openSheet(`${title}: ${m.display_name}`, h("div", { class: "stack" },
    h("div", { class: "muted" }, "Зараз на складі: ", h("b", {}, fmtQty(m, m.stock))),
    kind === "adjust" ? field(`Фактично є (${m.unit})`, qty) :
      h("div", { class: "form-grid" }, m.pack_size > 1 ? field(`Кількість (${m.pack_name})`, packs) : null, field(`Кількість (${m.unit})`, qty)),
    kind === "receipt" ? field("Сума закупівлі, ₴ (необов'язково)", cost) : null,
    kind === "receipt" && state.user.role === "director" ? h("label", { class: "row" }, asExp, "Записати у витрати «Закупівля матеріалів»") : null,
    field("Примітка", note),
    h("button", { class: "btn primary big", onclick: async () => {
      const v = parseFloat(qty.value);
      if (!(v >= 0)) return toast("Вкажіть кількість", true);
      const url = `/api/stock/${m.id}/` + (kind === "adjust" ? "adjust" : kind);
      const body = kind === "adjust" ? { actual: v, note: note.value || null }
        : kind === "writeoff" ? { qty: v, note: note.value }
          : { qty: v, cost: cost.value ? toKop(cost.value) : null, as_expense: asExp.checked && !!cost.value, note: note.value || null };
      await run(() => api.post(url, body), "Склад оновлено");
      closeSheet(); onSaved();
    } }, title)));
}

VIEWS.stock = async (main) => {
  let list = await api.get("/api/stock?all=true");
  const canEdit = state.user.role !== "master";
  let tab = "left", cat = "", q = "";
  const body = h("div");
  const refresh = async () => { list = await api.get("/api/stock?all=true"); state.materials = list.filter((m) => m.is_active); draw(); };
  main.append(topbar("Склад", "Залишки матеріалів і фарб за тонами. Списуються автоматично при закритті замовлення",
    canEdit ? h("button", { class: "btn primary", onclick: () => materialForm(null, list, refresh) }, icon("plus", "sm"), "Матеріал") : null));
  const search = h("input", { type: "search", placeholder: "Пошук: назва або тон", id: "stock-search", oninput: (e) => { q = e.target.value.trim().toLowerCase(); draw(); } });
  main.append(h("div", { class: "row", style: { marginBottom: "12px" } },
    seg([["left", "Залишки"], ["moves", "Рух складу"]], tab, (v) => { tab = v; draw(); }),
    h("div", { class: "search grow", style: { maxWidth: "340px" } }, icon("search", "sm"), search)), body);

  async function draw() {
    if (tab === "moves") {
      const mv = await api.get("/api/stock/moves");
      body.replaceChildren(mv.length ? h("div", { class: "card table-wrap" }, h("table", {},
        h("tr", {}, h("th", {}, "Дата"), h("th", {}, "Матеріал"), h("th", {}, "Операція"), h("th", { class: "num" }, "Кількість"), h("th", { class: "num" }, "Сума"), h("th", {}, "Примітка")),
        mv.filter((x) => !q || x.material.toLowerCase().includes(q)).map((x) => h("tr", {},
          h("td", {}, dm(x.ts), " ", h("span", { class: "muted" }, hm(x.ts))), h("td", {}, x.material), h("td", {}, MOVE_KIND[x.kind] || x.kind),
          h("td", { class: "num " + (x.qty < 0 ? "neg" : "pos") }, (x.qty > 0 ? "+" : "") + fmtNum(x.qty) + " " + x.unit),
          h("td", { class: "num" }, x.cost ? money(x.cost) : ""), h("td", { class: "muted small" }, x.order_id ? `Замовлення #${x.order_id}` : (x.note || "")))))) :
        h("div", { class: "card" }, emptyState("Рухів ще не було", "box")));
      return;
    }
    const cats = [...new Set(list.map((m) => m.category || "Інше"))];
    const shown = list.filter((m) => (!cat || (m.category || "Інше") === cat) && (!q || (m.display_name + " " + (m.category || "")).toLowerCase().includes(q)));
    const low = list.filter((m) => m.is_active && m.low);
    const value = list.filter((m) => m.stock > 0).reduce((a, m) => a + matCost(m, m.stock), 0);
    // групуємо тони однієї лінійки: «Igora Royal» → 7-1, 8-0…
    const groups = new Map();
    shown.forEach((m) => { const k = m.name; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(m); });
    body.replaceChildren(
      h("div", { class: "grid kpi", style: { marginBottom: "12px" } },
        kpi("Позицій на складі", list.filter((m) => m.is_active).length, "", { ico: "box", raw: true }),
        kpi("Закінчується", low.length, low.length ? "accent" : "", { ico: "alert", raw: true, sub: low.slice(0, 3).map((m) => m.display_name).join(", ") }),
        kpi("Вартість залишку", value, "", { ico: "banknote" })),
      cats.length > 1 ? h("div", { class: "row", style: { marginBottom: "10px" } },
        h("button", { class: "btn sm" + (cat ? "" : " primary"), onclick: () => { cat = ""; draw(); } }, "Усі"),
        cats.map((c) => h("button", { class: "btn sm" + (cat === c ? " primary" : ""), onclick: () => { cat = c; draw(); } }, c))) : null,
      list.length ? h("div", { class: "card table-wrap" }, h("table", {},
        h("tr", {}, h("th", {}, "Матеріал / тон"), h("th", { class: "m-hide" }, "Категорія"), h("th", { class: "num" }, "Залишок"), h("th", { class: "num" }, "Ціна упаковки"), canEdit ? h("th", { class: "right" }, "") : null),
        [...groups.entries()].map(([name, items]) => [
          items.length > 1 || items[0].tone ? h("tr", {}, h("td", { colspan: canEdit ? 5 : 4, style: { background: "var(--surface-2)", fontWeight: 700 } }, name, h("span", { class: "muted small" }, `  · тонів: ${items.length}`))) : null,
          items.map((m) => h("tr", { style: { opacity: m.is_active ? 1 : 0.5 } },
            h("td", {}, h("b", {}, m.tone || m.name), !m.is_active ? h("span", { class: "chip plain", style: { marginLeft: "6px" } }, "не використовується") : null),
            h("td", { class: "muted" }, m.category || ""),
            h("td", { class: "num" }, h("span", {}, h("b", { class: m.stock < 0 ? "neg" : "" }, fmtQty(m, m.stock)), m.low ? h("span", { class: "chip st-pending", style: { marginLeft: "8px" } }, "мало") : null)),
            h("td", { class: "num" }, h("span", {}, money(m.unit_cost), h("span", { class: "muted small" }, " / " + m.pack_name))),
            canEdit ? h("td", { class: "right", style: { whiteSpace: "nowrap" } },
              h("button", { class: "btn sm", onclick: () => stockMoveForm(m, "receipt", refresh) }, icon("plus", "sm"), "Прихід"), " ",
              h("button", { class: "btn sm", onclick: () => stockMoveForm(m, "adjust", refresh) }, "Інвентаризація"), " ",
              h("button", { class: "btn sm ghost", title: "Списати (зіпсовано тощо)", onclick: () => stockMoveForm(m, "writeoff", refresh) }, icon("trash", "sm")), " ",
              h("button", { class: "btn sm ghost", title: "Редагувати", onclick: () => materialForm(m, list, refresh) }, icon("edit", "sm"))) : null)),
        ]))) : h("div", { class: "card" }, emptyState("Склад порожній. Додайте матеріали й фарби кнопкою «+ Матеріал»", "box")));
  }
  draw();
};

// ---------- Графік роботи (по датах)
VIEWS.schedule = async (main) => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const monday = (iso) => { const d = new Date(iso + "T12:00"); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return isoDate(d); };
  const start = params.get("w") || monday(today());
  const end = addDays(start, 13);
  const go = (w) => { location.hash = "#/schedule?w=" + w; };
  const canEdit = state.user.role !== "master";
  main.append(topbar("Графік роботи", `${dmy(start)} — ${dmy(end)}. Натисніть на клітинку, щоб змінити день`,
    h("button", { class: "btn", "aria-label": "Раніше", onclick: () => go(addDays(start, -14)) }, icon("left", "sm")),
    h("button", { class: "btn", onclick: () => go(monday(today())) }, "Цей тиждень"),
    h("button", { class: "btn", "aria-label": "Пізніше", onclick: () => go(addDays(start, 14)) }, icon("right", "sm"))));
  const g = await api.get(`/api/schedule?date_from=${start}&date_to=${end}`);
  if (!g.masters.length) { main.append(h("div", { class: "card" }, emptyState("Додайте майстрів у розділі «Персонал»", "users"))); return; }
  const cellEl = (m, d, c) => {
    const short = (t) => (t.endsWith(":00") ? String(+t.slice(0, 2)) : t);
    const label = c.off ? "вихідний" : `${short(c.start)}–${short(c.end)}`;
    return h("td", { style: { padding: "4px" } }, h("button", {
      type: "button", disabled: !canEdit || null, title: c.source === "day" ? "Змінено на цю дату" : c.source === "master" ? "За шаблоном майстра" : "За графіком салону",
      style: {
        width: "100%", minWidth: "74px", border: c.source === "day" ? "1.5px solid var(--gold)" : "1px solid var(--line)", borderRadius: "8px",
        padding: "7px 4px", cursor: canEdit ? "pointer" : "default", fontWeight: 700, fontSize: "12.5px",
        background: c.off ? "var(--muted-bg)" : "var(--ok-bg)", color: c.off ? "var(--ink-3)" : "var(--ok)",
      },
      onclick: () => canEdit && dayForm(m, d, c),
    }, label));
  };
  const head = h("tr", {}, h("th", {}, "Майстер"), g.days.map((d) => h("th", { class: "right", style: { textAlign: "center", color: d === today() ? "var(--gold)" : null } }, weekday(d), h("br"), dm(d))));
  main.append(h("div", { class: "card table-wrap" }, h("table", {}, head,
    g.masters.map((m) => h("tr", {},
      h("td", { style: { whiteSpace: "nowrap" } }, h("div", { class: "row" }, avatar(m.full_name, "sm"), h("b", {}, m.full_name)),
        canEdit ? h("button", { class: "btn sm ghost", style: { marginTop: "4px" }, onclick: () => copyForm(m) }, "Копіювати тиждень…") : null),
      g.days.map((d) => cellEl(m, d, g.cells[m.id][d])))))));
  main.append(h("div", { class: "legend" },
    h("span", { class: "chip st-completed" }, "робочий день"), h("span", { class: "chip st-cancelled" }, "вихідний"),
    h("span", {}, "Золота рамка — змінено на конкретну дату; без рамки — за тижневим шаблоном (Персонал → майстер) або графіком салону")));

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
  const name = h("input", { value: s.salon_name });
  const share = h("input", { type: "number", min: 0, max: 100, step: 0.5, value: s.default_share_bp / 100 });
  const mode = select(Object.entries(MAT_MODES), s.default_materials_mode);
  const step = h("input", { type: "number", min: 5, step: 5, value: s.slot_step_min });
  const horizon = h("input", { type: "number", min: 1, value: s.booking_horizon_days });
  const lead = h("input", { type: "number", min: 0, value: s.booking_min_lead_min });
  const auto = h("input", { type: "checkbox", checked: s.telegram_auto_confirm });
  const takesCash = h("input", { type: "checkbox", checked: s.master_takes_cash !== false });
  const favVisits = h("input", { type: "number", min: 1, max: 50, value: s.favorite_master_visits || 3 });
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
  main.append(topbar("Налаштування"), h("div", { class: "grid two" },
    h("div", { class: "card stack" }, h("h3", {}, "Оплата майстрів"),
      field("Стандартний відсоток майстра, %", share), field("Облік матеріалів за замовчуванням", mode),
      h("label", { class: "row" }, takesCash, "Після зміни майстер бере заробіток з готівки в касі (решту доплачує директор)"),
      h("p", { class: "muted small" }, "Індивідуальні умови задаються в картці майстра (розділ «Персонал»). Зміни діють для нових замовлень; закриті замовлення зберігають умови на момент закриття.")),
    h("div", { class: "card stack" }, h("h3", {}, "Запис клієнтів"), field("Назва салону", name),
      h("div", { class: "form-grid" }, field("Крок слотів, хв", step), field("Запис наперед, днів", horizon), field("Мін. хв до запису (бот)", lead)),
      h("label", { class: "row" }, auto, "Записи з Telegram одразу підтверджені"),
      field("Улюблений майстер клієнта — після скількох візитів", favVisits)),
    h("div", { class: "card stack" }, h("h3", {}, "Telegram-бот"),
      field("Код входу в розділ «Для майстрів»", staffCode),
      h("p", { class: "muted small" }, "Після коду кожен обирає себе й вводить особистий PIN (задається в розділі «Персонал»)."),
      field("Адреса салону", addr), h("div", { class: "form-grid" }, field("Телефон", phone), field("Instagram", insta))),
    h("div", { class: "card stack" }, h("h3", {}, "Графік салону"), days.map((d) => d.row)),
    h("div", { class: "card stack" }, h("h3", {}, "Резервні копії"),
      h("p", { class: "muted small" }, "Автоматично щодня. Останні копії:"),
      backups.slice(0, 5).map((b) => h("div", { class: "sum-line small" }, h("span", {}, b.name), h("span", { class: "muted" }, (b.size / 1024).toFixed(0), " КБ"))),
      h("button", { class: "btn", onclick: async () => { await run(() => api.post("/api/backups"), "Копію створено"); reload(); } }, "Створити копію зараз"))),
    h("button", { class: "btn gold big", style: { marginTop: "14px" }, onclick: async () => {
      const body = {
        salon_name: name.value, default_share_bp: Math.round(parseFloat(share.value) * 100), default_materials_mode: mode.value,
        slot_step_min: +step.value, booking_horizon_days: +horizon.value, booking_min_lead_min: +lead.value, telegram_auto_confirm: auto.checked,
        master_takes_cash: takesCash.checked, favorite_master_visits: Math.max(1, parseInt(favVisits.value || "3", 10)),
        bot_staff_code: staffCode.value.trim(), salon_address: addr.value.trim(), salon_phone: phone.value.trim(), salon_instagram: insta.value.trim(),
        schedule: Object.fromEntries(days.map((d) => [String(d.i), d.on.checked ? [d.a.value, d.b.value] : null])),
      };
      state.settings = await run(() => api.put("/api/settings", body), "Налаштування збережено");
    } }, "Зберегти налаштування"));
};

// ---------- Журнал
VIEWS.journal = async (main) => {
  const body = h("div", { style: { marginTop: "12px" } });
  const KIND = { order_closed: "Закрито замовлення", order_reversal: "Сторно (виправлення)", order_corrected: "Виправлене замовлення", payout: "Виплата майстру",
    shift_closed: "Зміну закрито", expense: "Витрата", expense_reversal: "Сторно витрати" };
  const userName = (id) => (state.allUsers.find((u) => u.id === id) || {}).full_name || (id ? "#" + id : "—");
  const show = async (tab) => {
    if (tab === "ledger") {
      const list = await api.get("/api/journal/ledger?limit=300");
      body.replaceChildren(h("div", { class: "card table-wrap" }, h("table", {},
        h("tr", {}, h("th", {}, "Час"), h("th", {}, "Операція"), h("th", {}, "Об'єкт"), h("th", {}, "Майстер"), h("th", { class: "num" }, "Сума"), h("th", { class: "num" }, "Майстру"), h("th", { class: "num" }, "Салону"), h("th", {}, "Хто")),
        list.map((e) => h("tr", {}, h("td", {}, dm(e.ts), " ", hm(e.ts)), h("td", {}, KIND[e.kind] || e.kind), h("td", {}, e.ref_type, " #", e.ref_id), h("td", {}, e.master_id ? userName(e.master_id) : ""),
          h("td", { class: "num" }, money(e.amount)), h("td", { class: "num" }, money(e.master_amount)), h("td", { class: "num" }, money(e.salon_amount)), h("td", { class: "small muted" }, userName(e.user_id)))))));
    } else {
      const list = await api.get("/api/journal/audit?limit=300");
      body.replaceChildren(h("div", { class: "card table-wrap" }, h("table", {},
        h("tr", {}, h("th", {}, "Час"), h("th", {}, "Хто"), h("th", {}, "Що"), h("th", {}, "Дія"), h("th", {}, "Деталі")),
        list.map((a) => h("tr", {}, h("td", {}, dm(a.ts), " ", hm(a.ts)), h("td", {}, a.actor), h("td", {}, a.entity, " #", a.entity_id), h("td", {}, a.action),
          h("td", { class: "small muted", style: { maxWidth: "420px", wordBreak: "break-word" } }, a.reason ? "Причина: " + a.reason + ". " : "",
            a.before && a.after ? Object.keys(a.after).filter((k) => JSON.stringify(a.before[k]) !== JSON.stringify(a.after[k])).map((k) => `${k}: ${JSON.stringify(a.before[k])} → ${JSON.stringify(a.after[k])}`).join("; ") : ""))))));
    }
  };
  main.append(topbar("Журнал"), seg([["ledger", "Фінансові операції"], ["audit", "Історія змін"]], "ledger", show), body);
  await show("ledger");
};

boot();
