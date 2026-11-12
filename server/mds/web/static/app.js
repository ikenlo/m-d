"use strict";

if (!Element.prototype.replaceChildren) {
  const replaceChildren = function (...nodes) {
    while (this.lastChild) this.removeChild(this.lastChild);
    this.append(...nodes);
  };
  Element.prototype.replaceChildren = replaceChildren;
  DocumentFragment.prototype.replaceChildren = replaceChildren;
}

const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) {  } },
  del(k) { try { localStorage.removeItem(k); } catch (_) {  } },
};
const sessionStore = {
  get(k) { try { return sessionStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, v); } catch (_) {  } },
  del(k) { try { sessionStorage.removeItem(k); } catch (_) {  } },
};
const session = {
  idleMin: 30, lastActive: Date.now(), issued: Date.now(),
  touch() { session.lastActive = Date.now(); },
  async tick() {
    if (!api.token || !state.user) return;
    const idle = Date.now() - session.lastActive;
    if (idle > session.idleMin * 60000) { logout(); toast("Автовихід: 30 хвилин без дій"); return; }
    if (Date.now() - session.issued > 10 * 60000 && idle < 5 * 60000) {
      try {
        const r = await api.post("/api/auth/refresh");
        api.token = r.token; store.set("mds_token", r.token); session.issued = Date.now();
        if (r.idle_minutes) session.idleMin = r.idle_minutes;
      } catch (_) {  }
    }
  },
};
["pointerdown", "keydown", "touchstart"].forEach((ev) => document.addEventListener(ev, session.touch, { passive: true }));
setInterval(() => session.tick(), 60000);

let finPinOpen = null;
function financePinPrompt(setup) {
  if (finPinOpen) return finPinOpen;
  finPinOpen = new Promise((resolve) => {
    const pin = h("input", { type: "password", inputmode: "numeric", maxlength: 8, autocomplete: "off", placeholder: "4–8 цифр" });
    const pin2 = h("input", { type: "password", inputmode: "numeric", maxlength: 8, autocomplete: "off", placeholder: "ще раз" });
    let done = false;
    const finish = (ok) => { if (!done) { done = true; finPinOpen = null; resolve(ok); } };
    const go = h("button", { class: "btn primary big", onclick: async () => {
      try {
        if (setup) {
          if (pin.value !== pin2.value) return toast("PIN не збігається", true);
          await api.send("POST", "/api/auth/finance-pin", { pin: pin.value }, true);
        }
        const r = await api.send("POST", "/api/auth/finance-verify", { pin: pin.value }, true);
        sessionStore.set("mds_fin", r.finance_token);
        closeSheet(); finish(true);
      } catch (e) { toast(e.message, true); }
    } }, setup ? "Задати PIN і відкрити" : "Відкрити фінанси");
    openSheet(setup ? "Задайте PIN для «Фінансів»" : "PIN для «Фінансів»",
      h("div", { class: "stack" }, h("p", { class: "muted small" }, "PIN запитується раз за сесію. Скинути його може інший директор."),
        field("PIN", pin), setup ? field("Повторіть PIN", pin2) : null, go));
    const sheet = document.querySelector(".sheet");
    if (sheet) new MutationObserver((_, obs) => { if (!document.body.contains(sheet)) { obs.disconnect(); finish(false); } })
      .observe(document.body, { childList: true, subtree: true });
    setTimeout(() => pin.focus(), 50);
  });
  return finPinOpen;
}

const api = {
  token: store.get("mds_token"),
  inflight: new Map(),
  req(method, path, body) {
    if (method !== "GET") return this.send(method, path, body);
    const key = path + "|" + (this.token || "");
    if (this.inflight.has(key)) return this.inflight.get(key);
    const p = this.send(method, path).finally(() => this.inflight.delete(key));
    this.inflight.set(key, p);
    return p;
  },
  async send(method, path, body, retried = false) {
    const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = ctl && setTimeout(() => ctl.abort(), method === "GET" ? 20000 : 30000);
    const opts = { method, headers: {}, signal: ctl ? ctl.signal : undefined };
    if (this.token) opts.headers.Authorization = "Bearer " + this.token;
    const fin = sessionStore.get("mds_fin");
    if (fin) opts.headers["X-Finance-Token"] = fin;
    if (body && body.__idem) { opts.headers["Idempotency-Key"] = body.__idem; body = { ...body }; delete body.__idem; }
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      throw new Error(e && e.name === "AbortError" ? "Сервер довго не відповідає — спробуйте ще раз" : "Немає з'єднання з сервером");
    } finally {
      clearTimeout(timer);
    }
    if (res.status === 401 && path !== "/api/auth/login") {
      logout();
      throw new Error("Сесія завершилась");
    }
    const data = res.status === 204 ? null : await res.json().catch(() => null);
    if (res.status === 403 && data && (data.error === "finance_pin_required" || data.error === "finance_pin_setup") && !retried) {
      if (await financePinPrompt(data.error === "finance_pin_setup")) return this.send(method, path, body, true);
    }
    if (res.status === 409 && data && data.error === "version_conflict") {
      const err = new Error("Дані змінилися — хтось уже відредагував. Оновіть сторінку й повторіть.");
      err.code = "version_conflict"; err.status = 409; err.current = data.current;
      throw err;
    }
    if (!res.ok) {
      const msg = (data && data.detail) || "Помилка " + res.status;
      if (res.status >= 500 || res.status === 0) pushErr(`${method} ${path} → ${res.status}: ${msg}`);
      const err = new Error(msg);
      err.code = data && data.error;
      err.status = res.status;
      throw err;
    }
    return data;
  },
  get(p) { return this.req("GET", p); },
  post(p, b) { return this.req("POST", p, b === undefined ? {} : b); },
  patch(p, b) { return this.req("PATCH", p, b); },
  put(p, b) { return this.req("PUT", p, b); },
  del(p) { return this.req("DELETE", p); },
};

const rum = {
  buf: [],
  sentNav: false,
  add(name, ms) {
    if (!/^(nav|view):[a-z0-9_-]{1,32}$/.test(name) || !(ms >= 0)) return;
    this.buf.push({ name, ms: Math.min(Math.round(ms), 300000) });
    if (this.buf.length >= 30) this.flush();
  },
  nav() {
    if (this.sentNav || !window.performance || !performance.getEntriesByType) return;
    const n = performance.getEntriesByType("navigation")[0];
    if (!n || !n.loadEventEnd) return;
    this.sentNav = true;
    this.add("nav:ttfb", n.responseStart);
    this.add("nav:dom", n.domContentLoadedEventEnd);
    this.add("nav:load", n.loadEventEnd);
    const fcp = performance.getEntriesByType("paint").find((e) => e.name === "first-contentful-paint");
    if (fcp) this.add("nav:fcp", fcp.startTime);
  },
  flush() {
    if (!state.user || !api.token) return;
    this.nav();
    if (!this.buf.length) return;
    const items = this.buf.splice(0, 30);
    api.post("/api/perf/rum", { items }).catch(() => {  });
  },
};
setInterval(() => rum.flush(), 60000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") rum.flush(); });

const state = { user: null, services: [], masters: [], materials: [], settings: null };

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
  return v.toLocaleString("uk-UA", { minimumFractionDigits: k % 100 ? 2 : 0, maximumFractionDigits: 2 }) + "\u00a0₴";
};
const toKop = (s) => {
  const n = parseFloat(String(s).replace(/\s/g, "").replace(",", ".").replace(/[^\d.\-]/g, ""));
  return Number.isFinite(n) ? Math.round(n * 100) : NaN;
};
const priceText = (s) => (s.price_from ? "від " : "") + money(s.price);
const kopToInput = (k) => ((k || 0) / 100).toString();
const pct = (bp) => (bp / 100).toLocaleString("uk-UA") + "%";
const pad = (n) => String(n).padStart(2, "0");
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
const phoneLink = (p, attrs = {}) => (p ? h("a", { href: "tel:" + p, ...attrs }, attrs.label ?? p) : h("span", { class: "muted" }, "без номера"));
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now() + "-" + Math.random().toString(16).slice(2));

const EV_ICON = { pending: "alert", confirmed: "ok", in_progress: "play", completed: "check" };
const STATUS = {
  pending: "Очікує", confirmed: "Підтверджено", in_progress: "Триває",
  completed: "Завершено", cancelled: "Скасовано", no_show: "Не прийшов",
};
const SHIFT_STATUS = { open: "Відкрита", closed: "Очікує виплати", paid: "Виплачено" };
const PAY = { cash: "Готівка", card: "Картка", transfer: "Переказ" };
const PAY_CHOICES = { cash: "Готівка", card: "Картка" };
const ROLES = { director: "Директор", master: "Майстер" };
const EXP_CAT = { utilities: "Комунальні", advertising: "Реклама", materials: "Закупівля матеріалів", fuel: "Пальне (закупівля)", other: "Інше" };
const expCat = (k) => EXP_CAT[k] || (k === "rent" ? "Оренда" : k);

const chip = (st, map = STATUS) => h("span", { class: "chip st-" + st }, map[st] || st);

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
  down: '<path d="M6 9l6 6 6-6"/>',
  sliders: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
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
  man: '<circle cx="12" cy="5" r="2.6"/><path d="M8.5 21v-6.5H7v-4a3 3 0 0 1 3-3h4a3 3 0 0 1 3 3v4h-1.5V21"/><path d="M12 14.5V21"/>',
  woman: '<circle cx="12" cy="5" r="2.6"/><path d="M9.5 21v-4H6.8L9.5 10a2.5 2.5 0 0 1 2.4-1.8h.2A2.5 2.5 0 0 1 14.5 10l2.7 7h-2.7v4"/>',
  dye: '<path d="M14.5 3.5l6 6-7.8 7.8a3 3 0 0 1-2.1.9H8.4l-2.6 2.6-2.1-2.1 2.6-2.6v-2.2a3 3 0 0 1 .9-2.1z"/><path d="M11 7l6 6"/><path d="M5 4.5c1.3 1.6 2 2.7 2 3.6a2 2 0 0 1-4 0c0-.9.7-2 2-3.6z"/>',
  gauge: '<path d="M12 14l4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0"/>',
  bell: '<path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  flow: '<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="15" width="6" height="6" rx="1"/><path d="M9 6h4a2 2 0 0 1 2 2v7"/>',
};
function icon(name, cls = "") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("class", "i " + cls);
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || "";
  return s;
}

const AV_COLORS = ["#1d4d3b", "#8a6a2f", "#3f5a7a", "#7a4a5c", "#4f6b3a", "#5b4a7a", "#2f6b6b", "#7a5a3a"];
function initials(name) { return (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join(""); }
function avatar(name, cls = "") {
  let hsh = 0;
  for (const c of name || "") hsh = (hsh * 31 + c.charCodeAt(0)) >>> 0;
  return h("span", { class: "avatar " + cls, style: { background: AV_COLORS[hsh % AV_COLORS.length] } }, initials(name));
}

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

function toast(msg, err = false, ms = null) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const t = h("div", { class: "toast" + (err ? " err" : ""), role: "status" }, icon(err ? "alert" : "ok", "sm"), msg);
  document.body.append(t);
  setTimeout(() => t.remove(), ms || (err ? 4500 : 2600));
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
  if (prev === undefined || prev === null || !prev) return null;
  const p = Math.round(((cur - prev) / Math.abs(prev)) * 100);
  const cls = p > 0 ? "up" : p < 0 ? "down" : "flat";
  return h("span", { class: "delta " + cls, title: "Порівняно з попереднім періодом: " + money(prev) }, (p > 0 ? "▲ " : p < 0 ? "▼ " : "") + Math.abs(p) + "%");
}

function sparkline(values) {
  const NS = "http://www.w3.org/2000/svg";
  const v = values.filter((x) => Number.isFinite(x));
  if (v.length < 2) return null;
  const W = 100, H = 32, lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = v.map((x, i) => [(i / (v.length - 1)) * W, H - 3 - ((x - lo) / span) * (H - 6)]);
  let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], cx = (x0 + x1) / 2;
    d += ` C${cx.toFixed(1)} ${y0.toFixed(1)} ${cx.toFixed(1)} ${y1.toFixed(1)} ${x1.toFixed(1)} ${y1.toFixed(1)}`;
  }
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "spark");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", d);
  path.setAttribute("vector-effect", "non-scaling-stroke");
  svg.append(path);
  return svg;
}
function dailySeries(items, dateOf, valueOf) {
  const m = new Map();
  for (const x of items) { const k = dateOf(x).slice(0, 10); m.set(k, (m.get(k) || 0) + valueOf(x)); }
  return [...m.keys()].sort().map((k) => m.get(k));
}

function kpi(label, value, cls = "", { ico = null, prev, vs = null, sub = null, raw = false, onclick = null, spark = null } = {}) {
  const d = deltaEl(value, prev);
  const line = spark ? sparkline(spark) : null;
  return h(onclick ? "button" : "div", { class: "card kpi-tile " + cls + (onclick ? " kpi-link" : "") + (line ? " has-spark" : ""), onclick, type: onclick ? "button" : null },
    h("div", { class: "kpi-top" }, ico ? h("span", { class: "kpi-ico" }, icon(ico, "sm")) : null, h("span", { class: "kpi-label" }, label),
      onclick ? h("span", { class: "kpi-more" }, icon("right", "sm")) : null),
    h("div", { class: "kpi-row" }, h("div", { class: "kpi-value" + (value < 0 ? " neg" : "") }, raw ? value : money(value)), line),
    d || sub ? h("div", { class: "row small muted" }, d, d && vs ? vs : null, sub) : null);
}

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

function searchToggle(input, ...extra) {
  const close = h("button", { type: "button", class: "icon-btn", "aria-label": "Закрити пошук", title: "Закрити пошук", onclick: () => {
    if (input.value) { input.value = ""; input.dispatchEvent(new Event("input")); }
    box.classList.add("hidden"); btn.classList.remove("on");
  } }, icon("x", "sm"));
  const box = h("div", { class: "search-pop hidden" }, h("div", { class: "search-box grow" }, icon("search", "sm"), input), ...extra, close);
  const btn = h("button", { type: "button", class: "icon-btn search-btn", "aria-label": "Пошук", title: "Пошук", onclick: () => {
    const open = box.classList.toggle("hidden") === false;
    btn.classList.toggle("on", open);
    if (open) input.focus(); else close.click();
  } }, icon("search"));
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") close.click(); });
  return { btn, box };
}
function emptyState(text, ico = "inbox") { return h("div", { class: "empty" }, icon(ico), h("div", {}, text)); }

function labelTables(root) {
  root.querySelectorAll("table:not([data-labeled])").forEach((t) => {
    const ths = [...(t.querySelector("tr")?.querySelectorAll("th") || [])];
    const heads = ths.map((th) => (th.textContent || "").replace(/\s+/g, " ").trim());
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
        if (i === 0 && span === 1) td.classList.add("m-title");
        if (!(td.textContent || "").trim() && !td.querySelector("button, input, select, a, svg, img")) td.classList.add("m-empty");
        i += span;
      });
    });
  });
}
new MutationObserver(() => labelTables(document)).observe(document.documentElement, { childList: true, subtree: true });

const recentErrors = [];
const pushErr = (msg) => { recentErrors.push(`${new Date().toLocaleTimeString("uk-UA")} ${String(msg).slice(0, 300)}`); if (recentErrors.length > 5) recentErrors.shift(); };
window.addEventListener("error", (e) => pushErr(e.message || "Помилка"));
window.addEventListener("unhandledrejection", (e) => pushErr(e.reason?.message || e.reason || "Помилка"));

function readImage(file) {
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

const isTouch = () => window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
function autoFocus(el, select = false) {
  if (isTouch()) return;
  select ? el.select() : el.focus();
}

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

let sheetLocked = false;
let isProcessingPayment = false;
function lockSheet(on) {
  sheetLocked = on;
  const sh = document.querySelector("#overlay .sheet");
  if (sh) { sh.classList.toggle("sheet-locked", on); sh.setAttribute("aria-busy", on ? "true" : "false"); }
}
function openSheet(title, ...content) {
  closeSheet(true);
  const ov = h("div", { class: "overlay", id: "overlay", onclick: (e) => { if (e.target === ov) closeSheet(); } },
    h("div", { class: "sheet", role: "dialog", "aria-modal": "true" },
      h("div", { class: "sheet-head" }, h("h2", {}, title), h("button", { class: "x", onclick: closeSheet, "aria-label": "Закрити" }, icon("x"))),
      ...content));
  document.body.append(ov);
  unfitSheet = fitToKeyboard(ov);
  return ov;
}
function closeSheet(force) {
  if (sheetLocked && force !== true) return;
  if (force === true) sheetLocked = false;
  unfitSheet(); unfitSheet = () => {};
  const o = document.getElementById("overlay");
  if (o) o.remove();
  if (liveSync.pending) setTimeout(liveRefresh, 50);
}

const VIEW_ENTITIES = {
  today: ["appointment", "order", "shift"], calendar: ["appointment", "order", "callback"], dashboard: ["order", "expense", "payout", "appointment"],
  orders: ["order"], shifts: ["shift", "payout", "order"], stock: ["material"], clients: ["client", "appointment"],
  expenses: ["expense"], schedule: ["schedule", "user", "appointment"], earnings: ["order", "payout"], shift: ["shift", "order"],
  myorders: ["order", "shift"],
};
const liveSync = { rev: null, pending: false, busy: false };
function liveRefresh() {
  if (!state.user) return;
  const typing = document.activeElement && document.activeElement.closest && document.activeElement.closest(".main input, .main textarea, .main select");
  if (document.getElementById("overlay") || typing) { liveSync.pending = true; return; }
  liveSync.pending = false;
  const y = window.scrollY;
  Promise.resolve(renderApp()).then(() => window.scrollTo(0, y));
}
async function liveTick() {
  if (!state.user || document.hidden || liveSync.busy) return;
  liveSync.busy = true;
  try {
    const r = await api.get("/api/sync" + (liveSync.rev !== null ? "?since=" + liveSync.rev : ""));
    if (liveSync.rev !== null && r.rev > liveSync.rev) {
      const n = r.new || [];
      if (n.length === 1) {
        const a = n[0];
        toast(`🆕 Новий запис${a.source === "telegram" ? " з Telegram" : ""}: ${a.client_name}, ${dm(a.starts_at)} о ${hm(a.starts_at)}`, false, 8000);
      } else if (n.length > 1) toast(`🆕 Нових записів: ${n.length}`, false, 8000);
      const ents = VIEW_ENTITIES[currentRoute()] || [];
      if ((r.entities || []).some((e) => e === "*" || ents.includes(e))) liveRefresh();
    }
    liveSync.rev = r.rev;
  } catch (_) {  } finally {
    liveSync.busy = false;
  }
}
setInterval(liveTick, 15000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) liveTick(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
["gesturestart", "gesturechange", "gestureend"].forEach((ev) => document.addEventListener(ev, (e) => e.preventDefault(), { passive: false }));
document.addEventListener("touchmove", (e) => { if (e.touches && e.touches.length > 1) e.preventDefault(); }, { passive: false });
document.addEventListener("wheel", (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });

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

const NAV = {
  master: [
    ["today", "clock", "Сьогодні"], ["calendar", "calendar", "Календар"], ["myorders", "receipt", "Мої замовлення"], ["earnings", "wallet", "Мої фінанси"],
    ["shift", "check", "Зміна"], ["security", "shield", "Запити дозволу"], ["profile", "user", "Профіль"],
  ],
  director: [
    ["dashboard", "chart", "Фінанси", "Огляд"], ["analytics", "trend", "Аналітика", "Огляд"],
    ["calendar", "calendar", "Календар", "Робота"], ["schedule", "calclock", "Графік роботи", "Робота"], ["clients", "users", "Клієнти", "Робота"],
    ["stock", "box", "Склад", "Робота"],
    ["orders", "receipt", "Замовлення", "Фінанси"], ["shifts", "coins", "Зміни й виплати", "Фінанси"], ["expenses", "wallet", "Витрати", "Фінанси"], ["utilities", "gauge", "Комунальні платежі", "Фінанси"], ["taxreport", "filetext", "Звіт для податкової", "Фінанси"], ["taxes", "landmark", "Податки і зарплата", "Фінанси"],
    ["staff", "usercog", "Персонал", "Салон"], ["catalog", "scissors", "Послуги", "Салон"],
    ["settings", "settings", "Налаштування", "Салон"], ["journal", "book", "Журнал", "Салон"],
    ["security", "shield", "Безпека", "Салон"], ["logic", "flow", "Логіка системи", "Салон"],
    ["profile", "user", "Профіль"],
  ],
};
const MOBILE_TABS = { director: ["dashboard", "calendar", "orders", "more"],
  master: ["today", "calendar", "earnings", "more"] };

function currentRoute() { return (location.hash.replace(/^#\/?/, "") || "").split("?")[0]; }

function logout() {
  api.token = null;
  store.del("mds_token");
  sessionStore.del("mds_fin");
  state.user = null;
  renderLogin();
}

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
  liveSync.rev = null;
  liveTick();
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
  setThemeColor(document.documentElement.getAttribute("data-theme") === "dark" ? "#0b1a15" : "#f4f3ee");
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
    session.issued = Date.now(); session.touch();
    if (r.idle_minutes) session.idleMin = r.idle_minutes;
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
        if (digits.length > 1) {
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

let renderSeq = 0;
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
  const tabLinks = tabIds.map((id) => {
    const it = items.find((i) => i[0] === id) || ["more", "more", "Ще"];
    const active = id === route || (id === "more" && !tabIds.includes(route));
    return h("a", { href: "#/" + id, class: active ? "active" : "", "aria-label": it[2], title: it[2], "aria-current": active ? "page" : null }, icon(it[1]), h("span", { class: "tab-label" }, it[2]));
  });
  const plus = () => h("button", { class: "tab-plus", type: "button", "aria-label": "Швидкий запис", onclick: () => quickBook({}) }, h("span", {}, icon("plus")));
  tabLinks.splice(Math.floor(tabLinks.length / 2), 0, plus());
  const tabbar = h("nav", { class: "tabbar" }, tabLinks);
  const fab = h("button", { class: "fab", type: "button", "aria-label": "Швидкий запис", title: "Швидкий запис", onclick: () => quickBook({}) }, icon("plus"));
  const main = h("main", { class: "main" });
  const title = route === "more" ? "Меню" : (items.find((i) => i[0] === route) || [])[2] || "";
  const isTab = route === "more" || tabIds.includes(route);
  const backTo = tabIds.includes("more") ? "more" : tabIds[0];
  const lead = isTab ? h("div", { class: "brand-mark" }, "M&D")
    : h("button", { class: "icon-btn back-btn", "aria-label": "Назад", onclick: () => { location.hash = "#/" + backTo; } }, icon("left"), h("span", {}, "Назад"));
  const mbar = h("header", { class: "mbar" }, lead, h("b", { class: "grow" }, title),
    h("button", { class: "icon-btn", "aria-label": "Оновити сторінку", onclick: (e) => refreshView(e.currentTarget) }, icon("refresh")),
    h("button", { class: "icon-btn", "aria-label": "Повідомити про помилку", onclick: bugReportForm }, icon("bug")));
  const skel = h("div", { class: "skeleton view-skel", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("i"));
  main.append(mbar, skel);
  document.getElementById("root").replaceChildren(h("div", { class: "app" }, sidebar, main), tabbar, fab);
  closeSheet();
  const view = route === "more" ? viewMore : VIEWS[route];
  const seq = ++renderSeq;
  const t0 = performance.now();
  return Promise.resolve(view(main)).then(() => rum.add("view:" + route, performance.now() - t0))
    .catch((e) => { if (seq === renderSeq) main.replaceChildren(emptyState(e.message, "alert")); })
    .finally(() => skel.remove());
}

const isPhone = () => window.matchMedia("(max-width: 760px)").matches;
function deckCards(el) {
  return [...el.children].flatMap((c) => (c.classList.contains("card") ? [c] : [...c.children].filter((x) => x.classList.contains("card"))));
}
function makeDeckTabs(el, cards) {
  const bar = h("div", { class: "seg deck-tabs-bar", role: "tablist" });
  const show = (i) => {
    cards.forEach((c, j) => c.classList.toggle("deck-hide", j !== i));
    [...bar.children].forEach((b, j) => { b.classList.toggle("on", j === i); b.setAttribute("aria-selected", String(j === i)); });
  };
  cards.forEach((c, i) => bar.append(h("button", { type: "button", role: "tab", onclick: () => show(i) },
    c.dataset.tab || (c.querySelector("h3")?.textContent || String(i + 1)).trim())));
  el.before(bar);
  el.classList.add("deck-tabs");
  show(0);
}
function attachPager(el, dots, isDeck) {
  const per = isDeck ? 1 : 2;
  const stops = () => {
    const kids = [...el.children], r0 = el.getBoundingClientRect(), padL = parseFloat(getComputedStyle(el).paddingLeft) || 0;
    const max = el.scrollWidth - el.clientWidth, out = [];
    for (let i = 0; i < kids.length; i += per) out.push(Math.max(0, Math.min(max, Math.round(kids[i].getBoundingClientRect().left - r0.left + el.scrollLeft - padL))));
    return [...new Set(out)];
  };
  const nearest = (s, x) => s.reduce((b, v, i) => (Math.abs(v - x) < Math.abs(s[b] - x) ? i : b), 0);
  let last = "";
  const fitHeight = () => {
    const kids = el.children;
    if (!isDeck || !kids.length || !isPhone()) { el.style.height = ""; return; }
    const idx = nearest(stops(), el.scrollLeft);
    el.style.height = kids[Math.min(kids.length - 1, idx)].offsetHeight + 6 + "px";
  };
  const draw = () => {
    fitHeight();
    const s = stops(), cur = nearest(s, el.scrollLeft), key = s.length + ":" + cur;
    if (key === last) return;
    last = key;
    dots.replaceChildren(...(s.length > 1 && isPhone() ? s.map((_, i) => h("i", { class: i === cur ? "on" : "" })) : []));
  };
  let x0 = null, y0 = 0, s0 = 0, t0 = 0, axis = null;
  el.addEventListener("touchstart", (e) => {
    if (!isPhone() || e.touches.length !== 1) { x0 = null; return; }
    x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; s0 = el.scrollLeft; t0 = Date.now(); axis = null;
  }, { passive: true });
  el.addEventListener("touchmove", (e) => {
    if (x0 === null) return;
    const dx = e.touches[0].clientX - x0, dy = e.touches[0].clientY - y0;
    if (!axis && Math.abs(dx) + Math.abs(dy) > 8) axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
    if (axis !== "x") return;
    e.preventDefault();
    el.scrollLeft = s0 - dx;
  }, { passive: false });
  const end = (e) => {
    if (x0 !== null && axis === "x") {
      const dx = (e.changedTouches?.[0]?.clientX ?? x0) - x0;
      const s = stops(), from = nearest(s, s0);
      const fast = Math.abs(dx) / Math.max(1, Date.now() - t0) > 0.3;
      const to = Math.abs(dx) > 40 || fast ? Math.max(0, Math.min(s.length - 1, from + (dx < 0 ? 1 : -1))) : from;
      el.scrollTo({ left: s[to], behavior: "smooth" });
    }
    x0 = null; axis = null;
  };
  el.addEventListener("touchend", end);
  el.addEventListener("touchcancel", end);
  el.addEventListener("scroll", () => requestAnimationFrame(draw), { passive: true });
  window.addEventListener("resize", draw);
  draw();
}
function enhanceSwipes(root) {
  for (const el of root.querySelectorAll(".grid.kpi, .swipe-deck")) {
    if (el.dataset.sw) continue;
    el.dataset.sw = "1";
    const isDeck = el.classList.contains("swipe-deck");
    if (isDeck) {
      const cards = deckCards(el);
      if (cards.length <= 3) { makeDeckTabs(el, cards); continue; }
    } else if (el.children.length <= 3) {
      el.classList.add("few");
      el.style.setProperty("--n", String(el.children.length));
      continue;
    }
    const dots = h("div", { class: "swipe-dots", "aria-hidden": "true" });
    el.after(dots);
    attachPager(el, dots, isDeck);
  }
  for (const el of root.querySelectorAll(".scroll-box")) {
    if (el.dataset.sw) continue;
    el.dataset.sw = "1";
    const mark = () => el.classList.toggle("more", el.scrollTop + el.clientHeight < el.scrollHeight - 4);
    el.addEventListener("scroll", () => requestAnimationFrame(mark), { passive: true });
    window.addEventListener("resize", mark);
    mark();
  }
}
let swipeQueued = false;
new MutationObserver(() => {
  if (swipeQueued) return;
  swipeQueued = true;
  requestAnimationFrame(() => { swipeQueued = false; enhanceSwipes(document); });
}).observe(document.getElementById("root"), { childList: true, subtree: true });

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

(function pullToRefresh() {
  const TH = 72;
  let y0 = null, dy = 0, ind = null;
  const reset = () => { ind?.remove(); ind = null; y0 = null; dy = 0; };
  let x0 = 0;
  const SCROLLERS = ".grid.kpi, .swipe-deck, .daystrip, .slot-strip, .qb-chips, .qb-times, .scroll-box, .table-wrap, .seg";
  window.addEventListener("touchstart", (e) => {
    const inside = e.target.closest && e.target.closest(SCROLLERS);
    y0 = (state.user && window.scrollY <= 0 && !inside && !document.querySelector(".overlay") && e.touches.length === 1) ? e.touches[0].clientY : null;
    x0 = y0 === null ? 0 : e.touches[0].clientX;
    dy = 0;
  }, { passive: true });
  window.addEventListener("touchmove", (e) => {
    if (y0 === null) return;
    dy = e.touches[0].clientY - y0;
    if (Math.abs(e.touches[0].clientX - x0) > Math.max(12, dy * 0.6)) { reset(); return; }
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
    h("div", { class: "row" }, actions, bellButton(),
      h("button", { class: "icon-btn refresh-desk", title: "Оновити сторінку", "aria-label": "Оновити сторінку", onclick: (e) => refreshView(e.currentTarget) }, icon("refresh"))));
}

const ALERT_ICON = { critical: "🔴", important: "🟠", info: "🔵" };
function bellButton() {
  if (!state.user) return null;
  const badge = h("span", { class: "bell-badge hidden" });
  const btn = h("button", { class: "icon-btn bell", title: "Сповіщення", "aria-label": "Сповіщення", onclick: alertsSheet }, icon("bell"), badge);
  api.get("/api/alerts?unread=true").then((r) => {
    if (r.unread) { badge.textContent = r.unread > 99 ? "99+" : String(r.unread); badge.classList.remove("hidden"); }
  }).catch(() => {});
  return btn;
}
async function alertsSheet() {
  const r = await run(() => api.get("/api/alerts"));
  const isDir = state.user.role === "director";
  const list = h("div", { class: "stack" });
  const draw = (items) => list.replaceChildren(...(items.length ? items.map((a) => h("div", { class: "card", style: { padding: "10px 12px", opacity: a.read ? ".7" : "1" } },
    h("div", { class: "row small" }, ALERT_ICON[a.level] || "", h("b", { class: "grow" }, a.label || "Сповіщення"), h("span", { class: "muted" }, `${dm(a.created_at)} ${hm(a.created_at)}`)),
    h("div", { style: { whiteSpace: "pre-line" } }, a.text),
    isDir && !a.handled ? h("button", { class: "btn sm", onclick: async (e) => {
      const res = await run(() => api.post(`/api/alerts/${a.id}/handled`));
      e.currentTarget.replaceWith(h("span", { class: "muted small" }, res.already ? "Вже оброблено іншим директором" : "Оброблено"));
    } }, "Оброблено") : a.handled ? h("span", { class: "muted small" }, "✓ оброблено") : null))
    : [emptyState("Сповіщень немає", "bell")]));
  draw(r.items);
  openSheet("Сповіщення", list);
  if (r.unread) api.post("/api/alerts/read", {}).then(() => document.querySelectorAll(".bell-badge").forEach((b) => b.classList.add("hidden"))).catch(() => {});
}

function viewMore(main) {
  main.append(topbar("Меню"));
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  const tabs = MOBILE_TABS[state.user.role] || [];
  const groups = [];
  for (const [id, ico, label, grp] of NAV[state.user.role].filter(([id]) => !tabs.includes(id))) {
    const key = grp || "";
    if (!groups.length || groups[groups.length - 1][0] !== key) groups.push([key, []]);
    groups[groups.length - 1][1].push(h("a", { href: "#/" + id, class: "row menu-link" }, icon(ico), h("span", { class: "grow" }, label), icon("right", "sm")));
  }
  main.append(...groups.map(([grp, links]) => h("div", { class: "menu-group" }, grp ? h("div", { class: "set-section" }, grp) : null,
    h("div", { class: "card menu-card" }, links))));
  main.append(h("div", { class: "card", style: { padding: "6px", marginTop: "12px" } },
    h("button", { class: "btn ghost", style: { width: "100%", justifyContent: "flex-start", padding: "12px" }, onclick: bugReportForm }, icon("bug"), "Повідомити про помилку"),
    h("button", { class: "btn ghost", style: { width: "100%", justifyContent: "flex-start", padding: "12px" }, onclick: toggleTheme }, icon(dark ? "sun" : "moon"), dark ? "Світла тема" : "Темна тема"),
    h("button", { class: "btn ghost", style: { width: "100%", justifyContent: "flex-start", padding: "12px", color: "var(--danger)" }, onclick: logout }, icon("logout"), "Вийти")),
    h("p", { class: "muted small", style: { textAlign: "center", marginTop: "14px" } }, "MDS Connect · ", versionTag()));
}

const reload = () => renderApp();

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
    }
    secondary.append(h("button", { class: "btn", onclick: () => apptForm({ appt: a }, onChange) }, icon("move", "sm"), "Перенести"));
    if (a.starts_at.slice(0, 10) === today() && a.status !== "in_progress") {
      secondary.append(h("button", { class: "btn", title: "Майстер запізнюється — зсунути цей і всі наступні записи", onclick: () => shiftForm(a.master_id, a, onChange) }, icon("clock", "sm"), "Зсунути наступні"));
    }
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
        h("div", { class: "muted" }, `${weekday(a.starts_at.slice(0, 10))}, ${dmy(a.starts_at)} · ${a.duration_min}\u00a0хв`)), chip(a.status)),
      h("div", { class: "client-box" }, avatar(a.client.name, "lg"),
        h("div", { class: "grow" }, h("div", { style: { fontWeight: 700, fontSize: "15px" } }, a.client.name),
          a.client.phone ? h("a", { href: "tel:" + a.client.phone, class: "row small", style: { gap: "5px" } }, icon("phone", "sm"), a.client.phone) : h("span", { class: "muted small" }, "без номера")),
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
  const key = uuid();
  let method = null;
  const mats = new Map();
  const custom = [];
  const amount = h("input", { class: "huge", inputmode: "decimal", value: kopToInput(a.price) });
  const isDir = state.user && state.user.role === "director";
  const priceEditable = !!a.price_from || isDir;
  const price = h("input", { inputmode: "decimal", value: kopToInput(a.price), disabled: priceEditable ? null : true });
  const unpaid = h("input", { type: "checkbox" });
  const discountHint = h("div", { class: "muted small" });
  const updHint = () => {
    const d = (toKop(price.value) || 0) - (toKop(amount.value) || 0);
    discountHint.textContent = d > 0 ? `Знижка ${money(d)} — за рахунок салону; майстру 50% від ціни` : "";
  };
  amount.addEventListener("input", updHint); price.addEventListener("input", updHint);
  const prodQty = new Map();
  const prodBox = h("details", { class: "card", style: { padding: "4px 12px" } },
    h("summary", { class: "row", style: { padding: "8px 0", cursor: "pointer", listStyle: "none" } }, icon("box", "sm"), h("b", { class: "grow" }, "Товари клієнту"), icon("right", "sm")));
  api.get("/api/products").then((list) => {
    if (!list.length) { prodBox.append(h("p", { class: "muted small" }, "Товарів у каталозі ще немає")); return; }
    list.forEach((pr) => {
      const n = h("b", {}, "0");
      const ch = (dlt) => () => { const q = Math.max(0, (prodQty.get(pr.id) || 0) + dlt); prodQty.set(pr.id, q); n.textContent = String(q); };
      prodBox.append(h("div", { class: "sum-line" }, h("span", {}, pr.name, h("span", { class: "muted small" }, ` · ${money(pr.sale_price_cents)}`)),
        h("span", { class: "row" }, h("button", { type: "button", class: "btn sm", onclick: ch(-1) }, "−"), n,
          h("button", { type: "button", class: "btn sm", onclick: ch(1) }, "+"))));
    });
  }).catch(() => {});
  const PAY_ICON = { cash: "banknote", card: "card", transfer: "transfer" };
  const pick = (k) => (e) => { method = k; [...methods.children].forEach((b) => b.classList.remove("on")); e.currentTarget.classList.add("on"); };
  const methods = h("div", { class: "choice", style: { gridTemplateColumns: "1fr 1fr" } }, Object.entries(PAY_CHOICES).map(([k, t]) =>
    h("button", { type: "button", onclick: pick(k) }, icon(PAY_ICON[k]), t)));
  let ecr = false;
  api.get("/api/card-terminal/status").then((st) => { ecr = st.mode === "ecr"; }).catch(() => {});
  const [first0, ...rest0] = (a.client.name || "").split(/\s+/).filter(Boolean);
  const firstName = h("input", { value: first0 || "", placeholder: "Ім'я", autocomplete: "off", autocapitalize: "words", id: "close-first" });
  const lastName = h("input", { value: rest0.join(" "), placeholder: "Прізвище", autocomplete: "off", autocapitalize: "words", id: "close-last" });
  const looksLikeNick = /[_@\d]|^[A-Za-z]+$/.test(a.client.name || "") || a.source === "telegram";
  const clientBox = h("div", { class: "card", style: { padding: "12px", background: looksLikeNick ? "var(--warn-bg)" : null } },
    h("div", { class: "row small", style: { marginBottom: "8px" } }, icon("user", "sm"), h("b", {}, "Клієнт"),
      h("span", { class: "muted" }, a.client.phone || "без номера"),
      looksLikeNick ? h("span", { class: "muted grow right" }, "перевірте ім'я — могло прийти з Telegram") : null),
    h("div", { class: "form-grid", style: { gridTemplateColumns: "1fr 1fr" } }, firstName, lastName));
  const svc = state.services.find((s) => s.id === a.service_id) || {};
  const isColoring = !!svc.needs_materials || /фарб|мелір|blonde|чорн|тон/i.test([svc.category, svc.subcategory, svc.name].join(" "));
  let noMatOk = false;
  const matsBox = materialsPicker(mats, custom, { open: isColoring });

  const btn = h("button", { class: "btn primary big", onclick: async () => {
    const kop = toKop(amount.value);
    if (!(kop >= 0)) return toast("Вкажіть фактичну вартість", true);
    if (!method) return toast("Оберіть спосіб оплати", true);
    const clientName = [firstName.value, lastName.value].map((x) => x.trim()).filter(Boolean).join(" ");
    if (firstName.value.trim().length < 2) { firstName.focus(); return toast("Вкажіть ім'я клієнта", true); }
    const materials = [...mats.entries()].filter(([, q]) => q > 0).map(([id, q]) => ({ material_id: id, qty: q })).concat(custom);
    const products = [...prodQty.entries()].filter(([, q]) => q > 0).map(([id, q]) => ({ product_id: id, qty: q }));
    const p = priceEditable ? toKop(price.value) : null;
    if (priceEditable && !(p >= 0)) return toast("Вкажіть ціну послуги", true);
    if (svc.needs_materials && !materials.length && !noMatOk) {
      matsBox.open = true;
      matsBox.scrollIntoView({ behavior: "smooth", block: "center" });
      noMatOk = true;
      return toast("Фарбування без матеріалів? Вкажіть фарбу й окисник — або натисніть «Закрити» ще раз (замовлення світитиметься червоним, поки не допишете)", true, 6000);
    }
    const closeBody = () => {
      const body = { amount: kop, payment_method: method, materials, products, idempotency_key: key, client_name: clientName,
        status: unpaid.checked ? "unpaid" : "completed" };
      if (priceEditable) body.price = p;
      return body;
    };
    const closeNow = async (body) => {
      const o = await run(() => api.post(`/api/appointments/${a.id}/close`, body));
      closeSheet(true);
      toast(`Замовлення закрито. Заробіток майстра: ${money(o.master_earning)}`);
      if (o.status === "completed") fiscalizeOrder(o.id);
      state.materials = await api.get("/api/materials").catch(() => state.materials);
      onChange();
    };
    if (method === "card" && !unpaid.checked) {
      if (isProcessingPayment) return;
      if (ecr) {
        if (kop < 100) return toast("Оплата карткою — від 1 грн", true);
        return terminalCheckout({ a, kop, materials, products, clientName, price: priceEditable ? p : null,
          manualClose: () => closeNow({ ...closeBody(), payment_method: "card", idempotency_key: uuid() }), onChange });
      }
      isProcessingPayment = true;
      lockSheet(true);
      try {
        if (!confirm("Підтвердіть, що оплата карткою успішно пройшла на терміналі")) return;
        lockSheet(false);
        await closeNow(closeBody());
      } catch (_) {  } finally { isProcessingPayment = false; lockSheet(false); }
      return;
    }
    btn.disabled = true;
    try { await closeNow(closeBody()); } catch (_) { btn.disabled = false; }
  } }, icon("ok"), "Закрити замовлення");

  openSheet("Закрити замовлення",
    h("div", { class: "muted", style: { marginBottom: "10px" } }, `${a.service_name} · ${hm(a.starts_at)}`),
    h("div", { class: "stack" }, clientBox,
      h("div", { class: "form-grid" }, field(a.price_from ? "Погоджена ціна (P), ₴" : "Ціна за прайсом (P), ₴", price),
        field("Сплачено клієнтом (A), ₴", amount)), discountHint,
      field("Спосіб оплати", methods), h("label", { class: "row small" }, unpaid, "Клієнт ще не оплатив (неоплачене замовлення)"),
      matsBox, prodBox, btn));
  autoFocus(amount, true);
  amount.addEventListener("focus", () => setTimeout(() => amount.select(), 0));
}

let fiscalCfg = null;
function loadFiscalCfg() {
  if (fiscalCfg) return Promise.resolve(fiscalCfg);
  return api.get("/api/checkbox/client-config").then((c) => (fiscalCfg = c)).catch(() => ({ enabled: false }));
}
window.mdsPrintResult = (ok, message) => toast(message || (ok ? "Чек надруковано" : "Чек не надруковано"), !ok);
function printReceipt(b64, p) {
  const bridge = window.MDSAndroid;
  if (!p || p.printer_type === "none") return toast("Чек фіскалізовано, але принтер не налаштовано");
  if (!bridge || (p.printer_type === "wifi" ? typeof bridge.printWifi : typeof bridge.printBluetooth) !== "function") {
    return toast("Чек фіскалізовано. Друк — із планшета в застосунку MDS");
  }
  if (p.printer_type === "wifi") bridge.printWifi(p.printer_ip, p.printer_port, b64);
  else bridge.printBluetooth(b64, p.printer_bt_name || "");
}
async function fiscalizeOrder(orderId, { quiet = false } = {}) {
  const cfg = await loadFiscalCfg();
  if (!cfg.enabled || !orderId) return null;
  try {
    const r = await api.post(`/api/orders/${orderId}/fiscalize`);
    if (r.status === "done" && r.print_base64) printReceipt(r.print_base64, r.printer);
    else if (r.status === "done") toast(`Чек фіскалізовано${r.fiscal_code ? " · ФН " + r.fiscal_code : ""}`);
    else if (r.message) toast(r.message, r.status === "error");
    return r;
  } catch (e) {
    if (!quiet) toast("Чек не створено: " + e.message, true);
    return null;
  }
}

function terminalCheckout({ a, kop, materials, products, clientName, price, manualClose, onChange }) {
  if (isProcessingPayment) return;
  isProcessingPayment = true;
  lockSheet(true);
  let payment = null, cancelled = false;
  const text = h("p", { class: "pw-text", "data-testid": "pay-wait-text" }, "Очікування оплати на терміналі...");
  const sub = h("p", { class: "pw-sub" }, `${money(kop)} · клієнт прикладає або вставляє картку`);
  const mark = h("div", { class: "pw-mark" }, h("i", { class: "spinner", "aria-hidden": "true" }));
  const actions = h("div", { class: "pw-actions" });
  const box = h("div", { class: "pay-wait", role: "alertdialog", "aria-modal": "true", "aria-live": "polite", "data-testid": "pay-wait" },
    h("div", { class: "pw-card" }, mark, text, sub, actions));
  document.body.append(box);
  const finish = () => { box.remove(); isProcessingPayment = false; lockSheet(false); };
  const success = (r) => {
    mark.replaceChildren(h("span", { class: "pw-ok" }, icon("check", "lg")));
    text.textContent = "Оплату проведено";
    sub.textContent = `${money(r.amount)}${r.masked_pan ? " · " + r.masked_pan : ""}${r.rrn ? " · RRN " + r.rrn : ""}`;
    actions.replaceChildren();
    setTimeout(() => { finish(); closeSheet(true); toast("Оплачено карткою. Замовлення закрито"); onChange(); fiscalizeOrder(r.order_id); }, 1200);
  };
  const failure = (msg) => {
    mark.replaceChildren(h("span", { class: "pw-err" }, icon("x", "lg")));
    text.textContent = msg;
    sub.textContent = "Спробуйте ще раз або оберіть «Готівка».";
    actions.replaceChildren(
      h("button", { class: "btn", "data-testid": "pay-wait-close", onclick: finish }, "Закрити"),
      h("button", { class: "link-btn", "data-testid": "pay-manual", onclick: async () => {
        if (!confirm("Ви підтверджуєте, що оплата пройшла успішно?")) return;
        try { box.remove(); lockSheet(false); await manualClose(); } catch (_) {  } finally { finish(); }
      } }, "Закрити вручну"));
  };
  const cancelBtn = h("button", { class: "btn sm", "data-testid": "pay-cancel", onclick: async () => {
    cancelBtn.disabled = true;
    cancelled = true;
    try {
      const r = payment ? await api.post(`/api/card-payments/${payment.id}/cancel`) : null;
      toast(r && r.message && r.status === "processing" ? r.message : "Оплату скасовано");
    } catch (e) { toast(e.message, true); }
    finish();
  } }, "Скасувати");
  actions.append(cancelBtn);
  (async () => {
    try {
      payment = await api.post("/api/card-payments", { appointment_id: a.id, amount: kop, materials, products, client_name: clientName, price });
      if (cancelled) { await api.post(`/api/card-payments/${payment.id}/cancel`).catch(() => {}); return; }
      const r = await api.post(`/api/card-payments/${payment.id}/pay`);
      if (r.status === "paid" && r.order_id) {
        if (cancelled) { toast("Термінал провів оплату — замовлення закрито"); closeSheet(true); onChange(); fiscalizeOrder(r.order_id); return; }
        return success(r);
      }
      if (!cancelled) failure(r.error || r.message || "Оплата не пройшла");
    } catch (e) {
      if (!cancelled) failure(e.message);
    }
  })();
}

function inlineConfirm(anchor, text, okText) {
  return new Promise((resolve) => {
    const done = (v) => () => { box.remove(); resolve(v); };
    const box = h("div", { class: "card inline-confirm", role: "alertdialog" },
      h("div", { class: "row", style: { gap: "8px", alignItems: "flex-start" } }, icon("clock", "sm"), h("p", { style: { margin: 0 } }, text)),
      h("div", { class: "row", style: { marginTop: "10px" } },
        h("button", { type: "button", class: "btn grow", onclick: done(false) }, "Ні"),
        h("button", { type: "button", class: "btn primary grow", onclick: done(true) }, okText)));
    anchor.before(box);
    box.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}
async function createAppt(body, anchor, okMsg) {
  try {
    const r = await api.post("/api/appointments", body);
    toast(okMsg);
    return r;
  } catch (e) {
    if (e.code !== "off_hours") { toast(e.message, true); throw e; }
    if (!(await inlineConfirm(anchor, e.message, "Так, записати"))) throw e;
    return run(() => api.post("/api/appointments", { ...body, confirm_off_hours: true }), okMsg);
  }
}
async function openMyShift(btn) {
  if (btn) btn.disabled = true;
  try { return await run(() => api.post("/api/shifts/open"), "Зміну відкрито"); } finally { if (btn) btn.disabled = false; }
}

function offTextFor(r, isMaster) {
  if (r.working) return "У робочий час вільного немає — оберіть інший день" + (isMaster ? "" : " або майстра") + (r.off_hours?.length ? " або час поза графіком нижче." : ".");
  if (r.off_policy === "forbid") return "За графіком роботи це вихідний. Запис у неробочий час вимкнено в налаштуваннях салону.";
  if (r.off_policy === "if_free_place" && !r.off_hours?.length) return "За графіком роботи це вихідний, і всі робочі місця зайняті. Оберіть інший день.";
  return "За графіком роботи це вихідний. Можна записати на час поза графіком — з підтвердженням.";
}

const RECENT_SVC = () => `mds_recent_svc_${state.user?.id}`;
function rememberService(id) {
  let r = [];
  try { r = JSON.parse(store.get(RECENT_SVC()) || "[]"); } catch (_) { r = []; }
  store.set(RECENT_SVC(), JSON.stringify([id, ...r.filter((x) => x !== id)].slice(0, 8)));
}
function quickBook({ master_id = null, date = today(), time = null, client = null } = {}, onChange = reload) {
  const isMaster = state.user.role === "master";
  const masters = isMaster ? [{ id: state.user.id, full_name: state.user.full_name }] : state.masters;
  const services = state.services.filter((s) => s.is_active);
  let recent = [];
  try { recent = JSON.parse(store.get(RECENT_SVC()) || "[]"); } catch (_) { recent = []; }
  const st = { master: master_id || masters[0]?.id, service: null, date, time, clientId: null, preset: null, duration: null };
  const PRESETS = [
    { key: "men", label: "Чоловіча стрижка", min: 30, ico: "man", match: (s) => /стриж/i.test(`${s.category} ${s.name}`)
      && (/чолов/i.test(`${s.section} ${s.category} ${s.name}`) || /стрижки та послуги/i.test(s.category || "") || /машинк|насадк|модельн/i.test(s.name)) },
    { key: "women", label: "Жіноча стрижка", min: 60, ico: "woman", match: (s) => !/чолов/i.test(`${s.section} ${s.category}`)
      && (/жіноч/i.test(s.section || "") ? /стриж/i.test(`${s.category} ${s.name}`) : /^стрижка$/i.test((s.category || "").trim())) },
    { key: "color", label: "Фарбування", min: 120, ico: "dye", match: (s) => s.needs_materials || /фарб/i.test(`${s.category} ${s.subcategory} ${s.name}`) },
  ].map((p) => ({ ...p, service: services.filter(p.match).sort((a, b) => Math.abs(a.duration_min - p.min) - Math.abs(b.duration_min - p.min))[0] }))
    .filter((p) => p.service);
  if (!PRESETS.length) st.service = services.find((s) => s.id === recent[0])?.id || null;
  const durText = (m) => (m % 60 ? `${m}\u00a0хв` : `${m / 60} год`);
  const pBox = h("div", { class: "qb-presets" });
  const drawPresets = () => pBox.replaceChildren(...PRESETS.map((p) => h("button", { type: "button", class: "qb-preset" + (st.preset === p.key ? " on" : ""),
    onclick: () => { st.preset = p.key; st.service = p.service.id; st.duration = p.min; st.time = null; drawPresets(); drawServices(); loadTimes(); } },
    icon(p.ico), h("b", {}, p.label), h("small", {}, durText(p.min)))));
  const svcById = (id) => services.find((s) => s.id === id);

  const mBox = h("div", { class: "qb-chips" });
  const sBox = h("div", { class: "qb-chips" });
  const sSearch = h("input", { type: "search", placeholder: "Інша послуга: пошук…", class: "qb-search" });
  const sFound = h("div", { class: "qb-found" });
  const dBox = h("div", { class: "qb-chips qb-days" });
  const dPick = h("input", { type: "date", value: date, class: "qb-date", "aria-label": "Інша дата" });
  const tBox = h("div", { class: "qb-times" });
  const offBox = h("div", {});
  const timesWrap = h("div", { class: "qb-times-wrap" }, h("div", { class: "qb-label" }, "Час"), tBox, offBox);
  const shiftBox = h("div", {});
  let offList = [];
  const name = h("input", { placeholder: "Ім'я клієнта *", autocapitalize: "words", "aria-required": "true" });
  const phone = h("input", { type: "tel", inputmode: "tel", placeholder: "Телефон (необов'язково)" });
  const found = h("div", { class: "muted small" });
  const save = h("button", { class: "btn primary big", disabled: true }, icon("ok"), "Записати");

  const chip = (label, on, onclick, sub) => h("button", { type: "button", class: "qb-chip" + (on ? " on" : ""), onclick }, h("b", {}, label), sub ? h("small", {}, sub) : null);
  const drawMasters = () => mBox.replaceChildren(...masters.map((m) => chip(m.full_name.split(" ")[0], m.id === st.master, () => { st.master = m.id; st.time = null; drawMasters(); loadTimes(); })));
  const drawServices = () => {
    const ids = [...new Set([...recent, ...services.map((s) => s.id)])].filter((id) => svcById(id)).slice(0, 6);
    if (st.service && !ids.includes(st.service)) ids.unshift(st.service);
    sBox.replaceChildren(...ids.map((id) => { const s = svcById(id); return chip(s.name, id === st.service && !st.preset, () => pickService(id), [s.subcategory || s.category, priceText(s)].filter(Boolean).join(" · ")); }));
  };
  const pickService = (id) => { st.service = id; st.time = null; st.preset = null; st.duration = null; sSearch.value = ""; sFound.replaceChildren(); drawPresets(); drawServices(); loadTimes(); };
  sSearch.addEventListener("input", () => {
    const q = sSearch.value.trim().toLowerCase();
    sFound.replaceChildren(...(q ? services.filter((s) => [s.name, s.category, s.subcategory, s.section].join(" ").toLowerCase().includes(q)).slice(0, 8)
      .map((s) => h("button", { type: "button", class: "qb-found-item", onclick: () => pickService(s.id) },
        h("span", {}, s.name, h("small", { class: "muted" }, " · " + [s.section, s.subcategory || s.category].filter(Boolean).join(" · "))), h("small", { class: "muted" }, `${s.duration_min}\u00a0хв · ${priceText(s)}`))) : []));
  });
  const drawDays = () => {
    const days = [0, 1, 2].map((i) => addDays(today(), i));
    const label = (dd, i) => (i < 2 ? ["Сьогодні", "Завтра"][i] : WD_LONG[(new Date(dd + "T12:00").getDay() + 6) % 7]);
    dBox.replaceChildren(...days.map((dd, i) => chip(label(dd, i), dd === st.date, () => { st.date = dd; st.time = null; dPick.value = dd; drawDays(); loadTimes(); }, dm(dd))),
      h("label", { class: "qb-chip" + (days.includes(st.date) ? "" : " on") }, h("b", {}, days.includes(st.date) ? "Інша" : dm(st.date)), dPick));
  };
  dPick.addEventListener("change", () => { if (dPick.value) { st.date = dPick.value; st.time = null; drawDays(); loadTimes(); } });
  let req = 0;
  async function loadTimes() {
    summary();
    if (!st.service || !st.master) { tBox.replaceChildren(h("div", { class: "qb-empty" }, PRESETS.length ? "Оберіть стрижку чи фарбування вгорі" : "Оберіть послугу")); return; }
    const my = ++req;
    const keepH = tBox.offsetHeight + offBox.offsetHeight;
    timesWrap.style.minHeight = keepH ? keepH + "px" : "";
    tBox.classList.add("loading");
    const r = await api.get(`/api/availability/detail?master_id=${st.master}&service_id=${st.service}&day=${st.date}${st.duration ? "&duration_min=" + st.duration : ""}`)
      .catch(() => ({ slots: [], off_hours: [], working: true, shift_open: null }));
    if (my !== req) return;
    const list = r.slots;
    offList = r.off_hours || [];
    const pick = (x) => {
      st.time = x;
      timesWrap.querySelectorAll(".qb-time").forEach((b) => b.classList.toggle("on", b.textContent === x));
      summary();
    };
    tBox.classList.remove("loading");
    if (time && !st.time && (list.includes(time) || offList.includes(time))) st.time = time;
    const isMe = isMaster || st.master === state.user.id;
    shiftBox.replaceChildren(...(isMe && r.shift_open === false ? [h("div", { class: "card qb-shift row" },
      icon("lock", "sm"), h("div", { class: "grow" }, h("b", {}, "Зміна не відкрита"),
        h("div", { class: "muted small" }, r.working ? "Відкрийте зміну, щоб працювати сьогодні" : "За графіком сьогодні вихідний — відкрийте зміну, і день стане робочим")),
      h("button", { type: "button", class: "btn primary sm", onclick: async (e) => { await openMyShift(e.currentTarget); loadTimes(); } }, icon("ok", "sm"), "Відкрити зміну"))] : []));
    tBox.replaceChildren(...(list.length ? list.map((x) => h("button", { type: "button", class: "qb-time" + (x === st.time ? " on" : ""), onclick: () => pick(x) }, x))
      : [h("div", { class: "qb-empty" }, icon("calendar", "sm"), h("span", {}, offTextFor(r, isMaster)))]));
    const offOpen = offList.includes(st.time);
    offBox.replaceChildren(...(offList.length ? [h("details", { class: "qb-off", open: offOpen || null },
      h("summary", {}, icon("clock", "sm"), h("span", {}, r.working ? `Поза графіком (${r.hours[0]}–${r.hours[1]})` : "Неробочий день"),
        h("small", { class: "muted" }, r.off_policy === "if_free_place" ? "є вільне місце · з підтвердженням" : "з підтвердженням")),
      h("div", { class: "qb-times" }, offList.map((x) => h("button", { type: "button", class: "qb-time off" + (x === st.time ? " on" : ""), onclick: () => pick(x) }, x))))] : []));
    summary();
    requestAnimationFrame(() => { timesWrap.style.minHeight = ""; });
  }
  function summary() {
    const s = svcById(st.service);
    save.disabled = !(s && st.time && st.master);
    save.replaceChildren(icon("ok"), s && st.time ? `Записати · ${dm(st.date)} о ${st.time}` : "Записати");
  }
  let timer;
  phone.addEventListener("input", () => {
    clearTimeout(timer); st.clientId = null; found.textContent = "";
    timer = setTimeout(async () => {
      const digits = phone.value.replace(/\D/g, "");
      if (digits.length < 9) return;
      const list = await api.get("/api/clients?q=" + encodeURIComponent(digits.slice(-9))).catch(() => []);
      if (list.length === 1) { st.clientId = list[0].id; if (!name.value.trim()) name.value = list[0].name; found.textContent = "Постійний клієнт: " + list[0].name; }
      else found.textContent = list.length ? "" : "Новий клієнт";
    }, 350);
  });
  save.addEventListener("click", async () => {
    if (!st.clientId && name.value.trim().length < 2) { name.focus(); name.classList.add("invalid"); return toast("Вкажіть ім'я клієнта — щоб розуміти, хто це", true); }
    const body = { master_id: st.master, service_id: st.service, starts_at: `${st.date}T${st.time}` };
    if (st.duration) body.duration_min = st.duration;
    if (st.clientId) body.client_id = st.clientId;
    else { body.client_phone = phone.value.trim() || null; body.client_name = name.value.trim(); }
    save.disabled = true;
    if (offList.includes(st.time)) {
      const ok = await inlineConfirm(save, `${dm(st.date)} о ${st.time} — поза графіком роботи. Записати клієнта на неробочий час?`, "Так, записати");
      if (!ok) { save.disabled = false; return; }
      body.confirm_off_hours = true;
    }
    try {
      await createAppt(body, save, "Записано");
      rememberService(st.service);
      closeSheet(); onChange();
    } catch (_) { save.disabled = false; }
  });

  name.addEventListener("input", () => name.classList.remove("invalid"));
  if (client) { st.clientId = client.id; name.value = client.name; phone.value = client.phone || ""; found.textContent = "Клієнт із бази: " + client.name; }
  drawMasters(); drawPresets(); drawServices(); drawDays();
  openSheet("Швидкий запис", h("div", { class: "stack qb" },
    shiftBox,
    masters.length > 1 ? h("div", {}, h("div", { class: "qb-label" }, "Майстер"), mBox) : null,
    PRESETS.length ? pBox : h("div", {}, h("div", { class: "qb-label" }, "Послуга"), sBox, sSearch, sFound),
    h("div", {}, h("div", { class: "qb-label" }, "День"), dBox),
    timesWrap,
    h("div", {}, h("div", { class: "qb-label" }, "Клієнт"), h("div", { class: "form-grid" }, name, phone)), found,
    save,
    ));
  loadTimes();
}

function apptForm({ appt = null, master_id = null, date = today(), time = null, prefill = null, onSaved = null } = {}, onChange) {
  const isMaster = state.user.role === "master";
  const masters = isMaster ? [{ id: state.user.id, full_name: state.user.full_name }] : state.masters;
  const services = state.services.filter((s) => s.is_active || (appt && s.id === appt.service_id));
  const svcById = (id) => services.find((s) => s.id === id);
  const sectionOf = (s) => s.section || "Інші";
  const sections = [...new Set(services.map(sectionOf))];
  const startSvc = appt ? svcById(appt.service_id) : null;
  const st = {
    master: appt ? appt.master_id : master_id || masters[0]?.id,
    service: startSvc?.id || null,
    section: startSvc ? sectionOf(startSvc) : (sections.find((x) => /жіноч/i.test(x)) || sections[0]),
    category: startSvc?.category || null,
    date: appt ? appt.starts_at.slice(0, 10) : date,
    time: appt ? hm(appt.starts_at) : time,
    clientId: null,
  };
  const phone = h("input", { type: "tel", inputmode: "tel", placeholder: "необов'язково", value: prefill?.phone || "" });
  const name = h("input", { placeholder: "Ім'я клієнта", autocapitalize: "words", value: prefill?.name || "" });
  const found = h("div", { class: "muted small" });
  const price = h("input", { inputmode: "decimal", value: appt && startSvc?.price_from ? kopToInput(appt.price) : "" });
  const priceField = field("Ціна, ₴", price);
  const manual = h("input", { type: "time", step: 300, value: st.time || "" });
  const manualBox = h("div", { class: "hidden" }, field("Свій час", manual));
  const secBox = h("div", {});
  const catBox = h("div", { class: "ap-cats" });
  const svcBox = h("div", { class: "ap-svcs" });
  const svcInfo = h("div", { class: "svc-strip hidden" });
  const mBox = h("div", { class: "qb-chips" });
  const dBox = h("div", { class: "qb-chips qb-days" });
  const dPick = h("input", { type: "date", value: st.date, class: "qb-date", "aria-label": "Інша дата" });
  const tBox = h("div", { class: "qb-times" });
  const offBox = h("div", {});
  let offList = [];
  const chip = (label, on, onclick, sub) => h("button", { type: "button", class: "qb-chip" + (on ? " on" : ""), onclick }, h("b", {}, label), sub ? h("small", {}, sub) : null);

  const drawSections = () => {
    secBox.replaceChildren(sections.length > 1 ? seg(sections.map((x) => [x, /чолов/i.test(x) ? "Чоловіча" : /жіноч/i.test(x) ? "Жіноча" : x]), st.section,
      (v) => { st.section = v; st.category = null; drawServices(); }) : h("span"));
    secBox.firstChild?.classList?.add("ap-sections");
  };
  const drawServices = () => {
    const inSec = services.filter((s) => sectionOf(s) === st.section);
    const cats = [...new Set(inSec.map((s) => s.category || "Інше"))];
    const useCats = cats.length > 1 && inSec.length > 6;
    if (useCats && !cats.includes(st.category)) st.category = startSvc && sectionOf(startSvc) === st.section ? startSvc.category : null;
    catBox.replaceChildren(...(useCats ? cats.map((c) => h("button", { type: "button", class: "chip-btn" + (c === st.category ? " on" : ""),
      onclick: () => { st.category = st.category === c ? null : c; drawServices(); } }, c)) : []));
    const list = useCats ? (st.category ? inSec.filter((s) => (s.category || "Інше") === st.category) : []) : inSec;
    const groups = [];
    for (const s of list) {
      const g = useCats ? s.subcategory || "" : "";
      if (!groups.length || groups[groups.length - 1].g !== g) groups.push({ g, items: [] });
      groups[groups.length - 1].items.push(s);
    }
    svcBox.replaceChildren(...(useCats && !st.category ? [h("div", { class: "muted small" }, "Оберіть категорію")] : groups.map((gr) => h("div", { class: "ap-svc-group" },
      gr.g ? h("div", { class: "ap-svc-sub" }, gr.g) : null,
      h("div", { class: "ap-svc-grid" }, gr.items.map((s) => h("button", { type: "button", class: "ap-svc" + (s.id === st.service ? " on" : ""),
        onclick: () => { st.service = s.id; st.time = appt && st.time ? st.time : null; drawServices(); drawInfo(); loadTimes(); } },
        h("b", {}, s.name), h("small", {}, `${s.duration_min} хв · ${priceText(s)}`))))))));
  };
  const drawInfo = () => {
    const s = svcById(st.service);
    svcInfo.classList.toggle("hidden", !s);
    priceField.classList.toggle("hidden", !(s && s.price_from));
    if (!s) return;
    if (s.price_from) price.placeholder = kopToInput(s.price);
    svcInfo.replaceChildren(icon("clock", "sm"), h("span", { class: "grow" }, h("b", {}, s.name), h("span", { class: "muted" }, ` · ${s.duration_min} хв · ${priceText(s)}`)));
  };
  const drawMasters = () => mBox.replaceChildren(...masters.map((m) => chip(m.full_name.split(" ")[0], m.id === st.master, () => { st.master = m.id; drawMasters(); loadTimes(); })));
  const drawDays = () => {
    const days = [0, 1, 2].map((i) => addDays(today(), i));
    const label = (dd, i) => (i < 2 ? ["Сьогодні", "Завтра"][i] : WD_LONG[(new Date(dd + "T12:00").getDay() + 6) % 7]);
    dBox.replaceChildren(...days.map((dd, i) => chip(label(dd, i), dd === st.date, () => { st.date = dd; st.time = null; dPick.value = dd; drawDays(); loadTimes(); }, dm(dd))),
      h("label", { class: "qb-chip" + (days.includes(st.date) ? "" : " on") }, h("b", {}, days.includes(st.date) ? "Інша" : dm(st.date)), dPick));
  };
  dPick.addEventListener("change", () => { if (dPick.value) { st.date = dPick.value; st.time = null; drawDays(); loadTimes(); } });
  const pick = (x) => {
    st.time = x; manual.value = x;
    timesWrap.querySelectorAll(".qb-time").forEach((b) => b.classList.toggle("on", b.textContent === x));
    summary();
  };
  manual.addEventListener("input", () => { st.time = manual.value || null; timesWrap.querySelectorAll(".qb-time").forEach((b) => b.classList.toggle("on", b.textContent === st.time)); summary(); });
  let req = 0;
  async function loadTimes() {
    summary();
    if (!st.service || !st.master) { tBox.replaceChildren(h("div", { class: "qb-empty" }, "Спершу оберіть послугу")); offBox.replaceChildren(); return; }
    const my = ++req;
    timesWrap.style.minHeight = timesWrap.offsetHeight ? timesWrap.offsetHeight + "px" : "";
    tBox.classList.add("loading");
    const r = await api.get(`/api/availability/detail?master_id=${st.master}&service_id=${st.service}&day=${st.date}`)
      .catch(() => ({ slots: [], off_hours: [], working: true }));
    if (my !== req) return;
    tBox.classList.remove("loading");
    const list = [...r.slots];
    offList = r.off_hours || [];
    if (appt && st.time && st.date === appt.starts_at.slice(0, 10) && !list.includes(st.time) && !offList.includes(st.time)) list.push(st.time), list.sort();
    tBox.replaceChildren(...(list.length ? list.map((x) => h("button", { type: "button", class: "qb-time" + (x === st.time ? " on" : ""), onclick: () => pick(x) }, x))
      : [h("div", { class: "qb-empty" }, icon("calendar", "sm"), h("span", {}, offTextFor(r, isMaster)))]));
    offBox.replaceChildren(...(offList.length ? [h("details", { class: "qb-off", open: offList.includes(st.time) || null },
      h("summary", {}, icon("clock", "sm"), h("span", {}, r.working ? `Поза графіком (${r.hours[0]}–${r.hours[1]})` : "Неробочий день"),
        h("small", { class: "muted" }, r.off_policy === "if_free_place" ? "є вільне місце · з підтвердженням" : "з підтвердженням")),
      h("div", { class: "qb-times" }, offList.map((x) => h("button", { type: "button", class: "qb-time off" + (x === st.time ? " on" : ""), onclick: () => pick(x) }, x))))] : []));
    summary();
    requestAnimationFrame(() => { timesWrap.style.minHeight = ""; });
  }
  const timesWrap = h("div", { class: "qb-times-wrap" }, tBox, offBox,
    h("button", { type: "button", class: "link-btn", onclick: (e) => { manualBox.classList.toggle("hidden"); e.currentTarget.textContent = manualBox.classList.contains("hidden") ? "Вказати свій час" : "Сховати свій час"; } }, "Вказати свій час"),
    manualBox);

  let timer;
  phone.addEventListener("input", () => {
    clearTimeout(timer);
    st.clientId = null;
    timer = setTimeout(async () => {
      const digits = phone.value.replace(/\D/g, "");
      if (digits.length < 9) { found.textContent = ""; return; }
      try {
        const list = await api.get("/api/clients?q=" + encodeURIComponent(digits.slice(-9)));
        if (list.length === 1) {
          st.clientId = list[0].id; if (!name.value.trim()) name.value = list[0].name; found.textContent = "Постійний клієнт: " + list[0].name;
          const card = await api.get(`/api/clients/${st.clientId}`).catch(() => null);
          const fav = card?.favorite_master;
          if (fav && !isMaster && masters.some((m) => m.id === fav.id)) {
            if (!master_id) { st.master = fav.id; drawMasters(); loadTimes(); }
            found.textContent = `Постійний клієнт: ${list[0].name} · ⭐ улюблений майстер — ${fav.full_name} (${fav.visits} візитів)`;
          }
        } else found.textContent = list.length ? `Знайдено ${list.length}` : "Новий клієнт";
      } catch (_) { found.textContent = ""; }
    }, 350);
  });

  const save = h("button", { class: "btn primary big ap-save" });
  function summary() {
    const s = svcById(st.service);
    save.disabled = !(s && st.time && st.master);
    save.replaceChildren(icon("ok"), s && st.time ? `${appt ? "Зберегти" : "Записати"} · ${dm(st.date)} о ${st.time}` : appt ? "Зберегти зміни" : "Записати");
  }
  save.addEventListener("click", async () => {
    const s = svcById(st.service);
    const body = { master_id: st.master, service_id: st.service, starts_at: `${st.date}T${st.time}` };
    if (s?.price_from && price.value.trim()) {
      const kop = toKop(price.value);
      if (!(kop >= 0)) return toast("Невірна ціна", true);
      body.price = kop;
    }
    save.disabled = true;
    try {
      if (appt) {
        if (offList.includes(st.time)) body.confirm_off_hours = true;
        await run(() => api.patch(`/api/appointments/${appt.id}`, body), "Запис змінено");
      } else {
        if (st.clientId) body.client_id = st.clientId;
        else {
          if (name.value.trim().length < 2) { save.disabled = false; name.focus(); name.classList.add("invalid"); return toast("Вкажіть ім'я клієнта (телефон — необов'язково)", true); }
          body.client_phone = phone.value.trim() || null; body.client_name = name.value.trim();
        }
        if (offList.includes(st.time)) {
          const ok = await inlineConfirm(save, `${dm(st.date)} о ${st.time} — поза графіком роботи. Записати клієнта на неробочий час?`, "Так, записати");
          if (!ok) { save.disabled = false; return; }
          body.confirm_off_hours = true;
        }
        const created = await createAppt(body, save, "Запис створено");
        if (onSaved) await onSaved(created);
      }
      closeSheet();
      onChange();
    } catch (_) { summary(); }
  });
  name.addEventListener("input", () => name.classList.remove("invalid"));

  drawSections(); drawServices(); drawInfo(); drawMasters(); drawDays();
  openSheet(appt ? "Перенести / змінити запис" : "Новий запис",
    h("div", { class: "stack ap-form" },
      appt ? h("div", { class: "client-box" }, icon("user", "sm"), h("div", { class: "grow" }, h("b", {}, appt.client.name), h("div", { class: "muted small" }, appt.client.phone || "без номера")))
        : h("section", { class: "ap-section" }, h("div", { class: "ap-title" }, "Клієнт"),
          h("div", { class: "ap-grid ap-grid-sm2" }, field("Ім'я", name), field("Телефон", phone)), found),
      h("section", { class: "ap-section" }, h("div", { class: "ap-title" }, "Послуга"), secBox, catBox, svcBox, svcInfo, priceField),
      masters.length > 1 ? h("section", { class: "ap-section" }, h("div", { class: "ap-title" }, "Майстер"), mBox) : null,
      h("section", { class: "ap-section" }, h("div", { class: "ap-title" }, "День"), dBox),
      h("section", { class: "ap-section" }, h("div", { class: "ap-title" }, "Час"), timesWrap),
      save));
  loadTimes();
}


const VIEWS = {};

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
      toast(r.notified ? "Клієнту надіслано пропозицію в Telegram" : `Час змінено. Клієнт не в Telegram — зателефонуйте: ${a.client.phone || "номера немає"}`);
      onChange();
    } }, t)) : [h("span", { class: "muted small" }, "На цей день вільного часу немає")]));
  };
  d.addEventListener("change", load);
  openSheet("Запропонувати інший час", h("div", { class: "stack" },
    h("div", { class: "muted" }, `${a.client.name} хоче на ${dayWord(a.starts_at)} о ${hm(a.starts_at)} · ${a.service_name}`),
    field("День", d), h("div", { class: "kpi-label" }, "Вільний час майстра"), box,
    h("p", { class: "muted small" }, a.client.telegram_linked
      ? "Клієнт отримає в боті: «Майстер пропонує інший час» з кнопками «Підходить» / «Інший час» / «Скасувати»."
      : `Клієнт записаний не через Telegram — після зміни зателефонуйте йому: ${a.client.phone || "номера немає"}`)));
  load();
}

function declineBusy(a, onChange) {
  confirmSheet("Цей час зайнятий?", a.client.telegram_linked
    ? `Запис ${dayWord(a.starts_at)} о ${hm(a.starts_at)} буде знято. Бот напише клієнту, що час зайнятий, і запропонує найближчі вільні години.`
    : `Запис буде знято. Клієнт не в Telegram — зателефонуйте йому: ${a.client.phone || "номера немає"}`,
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
        h("div", {}, h("b", {}, a.client.name), " · ", phoneLink(a.client.phone)),
        h("div", { class: "muted small" }, a.service_name, isMgr ? ` · ${a.master_name}` : "", a.source === "telegram" ? " · з Telegram" : ""),
        a.awaiting_client ? h("div", { class: "chip st-in_progress", style: { marginTop: "6px", whiteSpace: "normal", borderRadius: "8px" } }, `Запропоновано ${hm(a.starts_at)} — чекаємо відповіді клієнта в боті`) : null),
      h("div", { class: "row" },
        h("button", { class: "btn sm primary", title: a.awaiting_client ? "Якщо клієнт погодився телефоном, а не в боті" : null,
          onclick: async () => { await run(() => api.post(`/api/appointments/${a.id}/status`, { status: "confirmed" }), "Запис підтверджено"); onChange(); } },
          icon("ok", "sm"), a.awaiting_client ? "Клієнт погодився" : "Підтвердити"),
        h("button", { class: "btn sm", onclick: () => proposeForm(a, onChange) }, icon("clock", "sm"), "Інший час"),
        h("button", { class: "btn sm danger", onclick: () => declineBusy(a, onChange) }, "Зайнято")))));
}

VIEWS.today = async (main) => {
  const d = today();
  const [list, my, shift] = await Promise.all([
    api.get(`/api/appointments?date_from=${d}`), api.get("/api/finance/my"), api.get("/api/shifts/current"),
  ]);
  const active = list.filter((a) => !["cancelled", "no_show"].includes(a.status));
  const left = active.filter((a) => a.status !== "completed");
  main.append(topbar(`Добрий день, ${state.user.full_name.split(" ")[0]}`,
    `${WD_LONG[(new Date().getDay() + 6) % 7]}, ${dm(d)} · записів: ${active.length}, залишилось: ${left.length}`,
    left.length ? h("button", { class: "btn", onclick: () => shiftForm(state.user.id, null, reload) }, icon("clock", "sm"), "Запізнююсь") : null,
    h("button", { class: "btn primary", onclick: () => apptForm({ date: d }, reload) }, icon("plus", "sm"), "Запис")));
  main.append(h("div", { class: "grid kpi", style: { marginBottom: "16px" } },
    kpi("Мій заробіток сьогодні", my.day.earning, "dark", { ico: "wallet" }),
    kpi("Виручка сьогодні", my.day.revenue, "", { ico: "banknote" }),
    kpi("Цей місяць", my.month.earning, "", { ico: "chart", sub: `${my.month.orders_count} послуг` }),
    h("div", { class: "card kpi-tile" }, h("div", { class: "kpi-top" }, h("span", { class: "kpi-ico" }, icon("check", "sm")), h("span", { class: "kpi-label" }, "Зміна")),
      h("div", { class: "kpi-value", style: { fontSize: "18px" } }, shift ? "Відкрита з " + hm(shift.opened_at) : "Не відкрита"),
      shift ? h("a", { href: "#/shift", class: "small" }, "Закрити зміну →")
        : h("button", { class: "btn primary sm", style: { alignSelf: "flex-start" }, onclick: async (e) => { await openMyShift(e.currentTarget); reload(); } }, icon("ok", "sm"), "Відкрити зміну"))));
  const pend = await pendingBlock(reload);
  if (pend) main.querySelector(".topbar").after(pend);
  if (!active.length) { main.append(h("div", { class: "card" }, emptyState("На сьогодні записів немає", "calendar"))); return; }
  const unc = await api.get("/api/appointments/unclosed").catch(() => []);
  if (unc.length) main.querySelector(".topbar").after(unclosedBlock(unc, reload));
  const noMat = await api.get("/api/orders/needs-materials").catch(() => []);
  if (noMat.length) main.querySelector(".topbar").after(needsMaterialsBlock(noMat, reload));
  const focus = focusAppt(active.filter((a) => !unc.some((u) => u.id === a.id)));
  if (focus) main.querySelector(".topbar").after(nowCard(focus, unc));
  const focusId = focus?.id;
  const tick = setInterval(() => {
    if (!main.isConnected) return clearInterval(tick);
    if (focusAppt(active)?.id !== focusId && !document.getElementById("overlay")) { clearInterval(tick); reload(); }
  }, 30000);
  for (const a of active) {
    const done = a.status === "completed";
    main.append(h("div", { class: `card appt st-b-${a.status}` + (done ? " done" : ""), onclick: () => (done ? null : apptSheet(a, reload)) },
      h("div", { class: "appt-time" }, hm(a.starts_at), h("small", {}, "до " + hm(a.ends_at))),
      h("div", { class: "appt-body" },
        h("div", { class: "row" }, h("span", { class: "appt-title" }, a.client.name), chip(a.status)),
        h("div", { class: "muted" }, a.service_name, " · ", money(a.price)),
        a.client.phone ? h("a", { href: "tel:" + a.client.phone, class: "small row", style: { gap: "4px", marginTop: "2px" }, onclick: (e) => e.stopPropagation() }, icon("phone", "sm"), a.client.phone) : h("span", { class: "muted small" }, "без номера")),
      done ? h("span", { class: "muted small" }, "Закрито") :
        h("button", { class: "btn primary", onclick: (e) => { e.stopPropagation(); closeOrderSheet(a, reload); } }, icon("banknote", "sm"), "Закрити")));
  }
};

function needsMaterialsBlock(list, onChange) {
  return h("div", { class: "card unc-card mat-alert" },
    h("div", { class: "card-head" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("box"), `Вкажіть матеріали: ${list.length}`),
      h("span", { class: "muted small" }, "Фарбування закрито без матеріалів — склад і собівартість неправильні")),
    list.map((o) => h("div", { class: "unc-row" },
      h("div", { class: "grow" }, h("b", {}, o.client_name), h("div", { class: "muted small" }, `${dm(o.closed_at)} ${hm(o.closed_at)} · ${o.service_name} · ${money(o.amount)}`)),
      h("div", { class: "unc-actions" }, h("button", { class: "btn sm primary", onclick: () => (state.user.role === "master" ? myOrderSheet(o.id, onChange) : orderMaterialsSheet(o, onChange)) }, icon("plus", "sm"), "Вказати матеріали")))));
}
async function orderMaterialsSheet(o, onChange) {
  state.materials = await api.get("/api/materials").catch(() => state.materials);
  const mats = new Map(), custom = [];
  for (const m of o.materials || []) if (m.material_id) mats.set(m.material_id, m.qty); else custom.push({ name: m.name, unit_cost: m.unit_cost, qty: 1 });
  openSheet(`Матеріали: ${o.client_name}`, h("div", { class: "stack" },
    h("div", { class: "muted" }, `${o.service_name} · ${dmy(o.closed_at)} ${hm(o.closed_at)}`),
    materialsPicker(mats, custom, { open: true }),
    h("button", { class: "btn primary big", onclick: async () => {
      const materials = [...mats.entries()].filter(([, q]) => q > 0).map(([id, q]) => ({ material_id: id, qty: q })).concat(custom);
      if (!materials.length) return toast("Оберіть використані матеріали", true);
      await run(() => api.patch(`/api/orders/${o.id}`, { materials, reason: "Вказано використані матеріали" }), "Матеріали записано, склад оновлено");
      closeSheet(); onChange && onChange();
    } }, icon("ok"), "Зберегти матеріали")));
}
function unclosedBlock(list, onChange, bare = false) {
  const quick = async (u, method, btn) => {
    btn.disabled = true;
    try {
      await run(() => api.post(`/api/appointments/${u.id}/close`, { amount: u.price, payment_method: method, materials: [], idempotency_key: uuid() }), "Замовлення закрито");
      onChange();
    } finally { btn.disabled = false; }
  };
  const rows = list.map((u) => h("div", { class: "unc-row" },
    h("div", { class: "grow" }, h("b", {}, u.client.name), h("div", { class: "muted small" }, `${dm(u.starts_at)} ${hm(u.starts_at)} · ${u.service_name} · ${money(u.price)}`)),
    h("div", { class: "unc-actions" },
      h("button", { class: "btn sm", onclick: (e) => quick(u, "cash", e.currentTarget) }, icon("banknote", "sm"), "Готівка"),
      h("button", { class: "btn sm", onclick: (e) => quick(u, "card", e.currentTarget) }, icon("card", "sm"), "Картка"),
      h("button", { class: "btn sm ghost", title: "Інша сума, матеріали", onclick: () => closeOrderSheet(u, onChange) }, icon("edit", "sm")))));
  if (bare) return h("div", { class: "stack" }, rows);
  return h("div", { class: "card unc-card" }, h("div", { class: "card-head" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("receipt"), `Не закрито: ${list.length}`),
    h("span", { class: "muted small" }, "Час минув — закрийте одним дотиком (сума за записом) або ✎ детально")), rows);
}
async function shiftForm(masterId, fromAppt, onChange) {
  const d = today();
  const list = (await api.get(`/api/appointments?date_from=${d}&master_id=${masterId}`).catch(() => []))
    .filter((a) => ["pending", "confirmed"].includes(a.status)).sort((x, y) => x.starts_at.localeCompare(y.starts_at));
  const now = new Date();
  const from = fromAppt || list.find((a) => new Date(a.starts_at) >= now);
  if (!from) return toast("Немає наступних записів, які можна зсунути", true);
  let minutes = 15;
  const affected = list.filter((a) => a.starts_at >= from.starts_at);
  const notify = h("input", { type: "checkbox", checked: true });
  const preview = h("div", { class: "stack" });
  const draw = () => preview.replaceChildren(...affected.map((a) => {
    const t2 = new Date(new Date(a.starts_at).getTime() + minutes * 60000);
    return h("div", { class: "sum-line" }, h("span", {}, a.client.name, h("span", { class: "muted small" }, " · " + a.service_name)),
      h("span", {}, h("s", { class: "muted" }, hm(a.starts_at)), " → ", h("b", {}, `${pad(t2.getHours())}:${pad(t2.getMinutes())}`)));
  }));
  draw();
  openSheet("Зсунути записи", h("div", { class: "stack" },
    h("p", { class: "muted", style: { margin: 0 } }, `Записи з ${hm(from.starts_at)} і всі наступні сьогодні зсунуться на обраний час.`),
    seg([[10, "10 хв"], [15, "15 хв"], [20, "20 хв"], [30, "30 хв"], [45, "45 хв"], [60, "1 год"]], minutes, (v) => { minutes = Number(v); draw(); }),
    preview,
    h("label", { class: "row" }, notify, "Повідомити клієнтів (Telegram; іншим — SMS/Viber, якщо підключено)"),
    h("button", { class: "btn primary big", onclick: async () => {
      const r = await run(() => api.post("/api/appointments/shift", { master_id: masterId, minutes, from_appt_id: from.id, notify_clients: notify.checked }), "Записи зсунуто");
      closeSheet();
      const call = r.call || [];
      openSheet("Записи зсунуто", h("div", { class: "stack" },
        h("div", { class: "sum-line" }, h("span", {}, "Зсунуто записів"), h("b", {}, String(r.shifted.length))),
        h("div", { class: "sum-line" }, h("span", {}, "Повідомлено в Telegram"), h("b", {}, String(r.telegram))),
        r.sms_sent ? h("div", { class: "sum-line" }, h("span", {}, "Надіслано SMS / Viber"), h("b", {}, String(r.sms_sent))) : null,
        call.length ? h("div", { class: "callout", style: { margin: 0 } }, icon("phone"), h("div", { class: "grow" },
          h("b", {}, "Подзвоніть цим клієнтам (немає Telegram, SMS не підключено):"),
          call.map((c) => h("div", { class: "sum-line" }, h("span", {}, c.client, " · ", hm(c.starts_at)), h("a", { href: "tel:" + c.phone }, c.phone))))) : null,
        h("button", { class: "btn primary", onclick: () => { closeSheet(); onChange(); } }, "Готово")));
    } }, "Зсунути")));
}

function focusAppt(list) {
  const now = new Date();
  const t = (iso) => new Date(iso);
  const open = list.filter((a) => !["completed", "cancelled", "no_show"].includes(a.status));
  return open.find((a) => a.status === "in_progress")
    || open.find((a) => t(a.starts_at) <= now && now < t(a.ends_at))
    || open.filter((a) => t(a.starts_at) > now).sort((x, y) => x.starts_at.localeCompare(y.starts_at))[0]
    || open.filter((a) => t(a.ends_at) <= now).sort((x, y) => y.starts_at.localeCompare(x.starts_at))[0]
    || null;
}
function nowCard(a, unc = []) {
  const now = new Date(), st = new Date(a.starts_at), en = new Date(a.ends_at);
  const mins = Math.round((st - now) / 60000);
  const label = a.status === "in_progress" ? "Зараз у кріслі"
    : st <= now && now < en ? "Зараз · час запису настав"
      : en <= now ? "Не закрито — час минув"
        : mins <= 60 ? `Наступний · через ${mins}\u00a0хв` : `Наступний · о ${hm(a.starts_at)}`;
  const urgent = st <= now;
  return h("div", { class: "card now-card" + (urgent ? " urgent" : "") },
    h("div", { class: "now-top" }, h("span", { class: "now-label" }, h("i", { class: "pulse" }), label), chip(a.status)),
    h("div", { class: "now-main", onclick: () => apptSheet(a, reload) },
      h("div", { class: "now-time" }, hm(a.starts_at), h("small", {}, "до " + hm(a.ends_at))),
      h("div", { class: "grow" }, h("div", { class: "now-client" }, a.client.name), h("div", { class: "muted" }, a.service_name, " · ", money(a.price)),
        a.comment ? h("div", { class: "small muted" }, icon("edit", "sm"), " ", a.comment) : null)),
    h("div", { class: "now-actions" },
      h("button", { class: "btn primary", onclick: () => closeOrderSheet(a, reload) }, icon("banknote", "sm"), "Закрити замовлення"),
      a.client.phone ? h("a", { class: "btn", href: "tel:" + a.client.phone }, icon("phone", "sm"), "Подзвонити") : null));
}

const calStrip = { from: null, scroll: null };
VIEWS.calendar = async (main) => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const day = params.get("d") || today();
  const isMaster = state.user.role === "master";
  const narrow = window.matchMedia("(max-width: 760px)").matches;
  const mode = params.get("v") || store.get("mds_cal_view") || (narrow ? "list" : "grid");
  const go = (dd, v = mode) => { location.hash = `#/calendar?d=${dd}&v=${v}`; };
  const setMode = (v) => { store.set("mds_cal_view", v); go(day, v); };

  const datePick = h("span", { class: "btn date-pick", title: "Обрати дату" }, icon("calendar", "sm"), h("span", { class: "m-hide" }, "Дата"),
    h("input", { type: "date", value: day, "aria-label": "Обрати дату", onchange: (e) => e.target.value && go(e.target.value) }));
  const calTop = topbar("Календар", `${WD_LONG[(new Date(day + "T12:00").getDay() + 6) % 7]}, ${dmy(day)}`,
    h("div", { class: "btn-group" },
      h("button", { class: "btn", "aria-label": "Попередній день", onclick: () => go(addDays(day, -1)) }, icon("left", "sm")),
      h("button", { class: "btn" + (day === today() ? " on" : ""), onclick: () => go(today()) }, "Сьогодні"),
      h("button", { class: "btn", "aria-label": "Наступний день", onclick: () => go(addDays(day, 1)) }, icon("right", "sm"))),
    datePick,
    h("div", { class: "seg icons", role: "tablist" },
      h("button", { class: mode === "grid" ? "on" : "", title: "Сітка по майстрах", "aria-label": "Сітка", onclick: () => setMode("grid") }, icon("grid", "sm"), h("span", {}, "Сітка")),
      h("button", { class: mode === "list" ? "on" : "", title: "Список записів", "aria-label": "Список", onclick: () => setMode("list") }, icon("rows", "sm"), h("span", {}, "Список"))),
    h("button", { class: "btn primary", onclick: () => quickBook({ date: day }) }, icon("plus", "sm"), "Новий запис"));
  calTop.classList.add("cal-top");
  if (narrow) {
    const other = mode === "grid" ? "list" : "grid";
    main.append(h("div", { class: "cal-mhead" },
      h("span", { class: "cal-month", title: "Обрати дату" }, h("b", {}, monthTitle(day)), icon("down", "sm"),
        h("input", { type: "date", value: day, "aria-label": "Обрати дату", onchange: (e) => e.target.value && go(e.target.value) })),
      h("div", { class: "cal-acts" },
        day !== today() ? h("button", { class: "cal-act txt", type: "button", onclick: () => go(today()) }, "Сьогодні") : null,
        h("button", { class: "cal-act", type: "button", title: other === "grid" ? "Сітка по майстрах" : "Список записів",
          "aria-label": other === "grid" ? "Показати сіткою" : "Показати списком", onclick: () => setMode(other) }, icon(other === "grid" ? "grid" : "rows")),
        h("button", { class: "cal-act", type: "button", "aria-label": "Новий запис", onclick: () => quickBook({ date: day }) }, icon("plus")))));
  } else main.append(calTop);

  const wdIdx = (iso) => String((new Date(iso + "T12:00").getDay() + 6) % 7);
  const salonSched = state.settings.schedule || {};
  const mondayOf = (iso) => addDays(iso, -Number(wdIdx(iso)));
  if (!calStrip.from || day < calStrip.from || day > addDays(calStrip.from, 20)) { calStrip.from = addDays(mondayOf(day), -7); calStrip.scroll = null; }
  const stripFrom = calStrip.from, stripTo = addDays(calStrip.from, 20);
  const strip = h("div", { class: "daystrip" });
  const cnt = {};
  for (let i = 0; i <= 20; i++) {
    const dd = addDays(stripFrom, i);
    const closed = !salonSched[wdIdx(dd)];
    cnt[dd] = h("i", { class: "cnt zero" }, "·");
    strip.append(h("button", { class: (dd === day ? "on " : "") + (dd === today() ? "today " : "") + (closed ? "off" : ""), title: closed ? "Вихідний" : null,
      onclick: (e) => {
        strip.querySelectorAll(".on").forEach((x) => x.classList.remove("on")); e.currentTarget.classList.add("on");
        calStrip.scroll = strip.scrollLeft; if (dd !== day) go(dd);
      } },
      h("small", {}, weekday(dd)), h("b", {}, Number(dd.slice(8, 10))), cnt[dd]));
  }
  main.append(strip);
  if (calStrip.scroll !== null) strip.scrollLeft = calStrip.scroll;
  requestAnimationFrame(() => {
    if (calStrip.scroll !== null) strip.scrollLeft = calStrip.scroll;
    else { const on = strip.querySelector(".on"); if (on) strip.scrollLeft = on.offsetLeft - strip.clientWidth / 2 + on.offsetWidth / 2; }
    strip.addEventListener("scroll", () => { calStrip.scroll = strip.scrollLeft; }, { passive: true });
  });
  const range = await api.get(`/api/appointments?date_from=${stripFrom}&date_to=${stripTo}`);
  if (!main.isConnected) return;
  const isActive = (a) => !["cancelled", "no_show"].includes(a.status);
  const perDay = {};
  for (const a of range) if (isActive(a)) perDay[a.starts_at.slice(0, 10)] = (perDay[a.starts_at.slice(0, 10)] || 0) + 1;
  for (const [dd, el] of Object.entries(cnt)) {
    const n = perDay[dd] || 0;
    el.className = "cnt" + (n ? "" : " zero"); el.textContent = n ? String(n) : "·";
    if (n) el.parentElement.title = `Записів: ${n}`;
  }

  const list = range.filter((a) => a.starts_at.slice(0, 10) === day);
  const activeList = list.filter(isActive).sort((a, b) => a.starts_at.localeCompare(b.starts_at));
  const pend = await pendingBlock(reload);
  let pendChip = null;
  if (pend) {
    if (narrow) {
      const n = pend.querySelectorAll(":scope > .row").length;
      pendChip = h("button", { class: "pend-chip", type: "button", onclick: () => openSheet("Непідтверджені записи", pend) },
        icon("alert", "sm"), `${n} ${plural(n, "непідтверджений", "непідтверджені", "непідтверджених")}`);
    } else main.append(pend);
  }
  if (!isMaster) {
    const calls = await api.get("/api/callbacks").catch(() => []);
    if (calls.length) {
      main.append(h("div", { class: "callout" }, icon("phone"),
        h("div", { class: "grow" }, h("b", {}, `Просять передзвонити: ${calls.length}`),
          calls.map((c) => h("div", { class: "cb-row" },
            h("div", { class: "cb-who" }, h("b", {}, c.name || "Клієнт"), h("span", { class: "muted small" }, c.phone, " · ", dm(c.created_at), " ", hm(c.created_at))),
            h("div", { class: "cb-actions" },
              h("a", { class: "btn sm", href: "tel:" + c.phone }, icon("phone", "sm"), "Зателефонувати"),
              h("button", { class: "btn sm primary", onclick: () => apptForm({ date: day, prefill: { name: c.name || "", phone: c.phone },
                onSaved: () => api.post(`/api/callbacks/${c.id}/done`).catch(() => null) }, reload) }, icon("plus", "sm"), "Записати"),
              h("button", { class: "icon-btn", title: "Без запису — прибрати заявку", "aria-label": "Без запису — прибрати заявку",
                onclick: async () => { await run(() => api.post(`/api/callbacks/${c.id}/done`), "Заявку прибрано"); reload(); } }, icon("x", "sm"))))))));
    }
  }
  const masters = isMaster ? [{ id: state.user.id, full_name: state.user.full_name, schedule: state.user.schedule }] : state.masters;
  if (!masters.length) { main.append(h("div", { class: "card" }, emptyState("Додайте майстрів у розділі «Персонал»", "users"))); return; }

  const sched = await api.get(`/api/schedule?date_from=${day}&date_to=${day}`).catch(() => null);
  const wd = wdIdx(day);
  const spanOf = (m) => {
    const c = sched?.cells?.[m.id]?.[day];
    if (c) return c.off ? null : [c.start, c.end];
    return ((m.schedule || salonSched)[wd] || null);
  };
  const toMin = (s) => { const [a, b] = s.split(":").map(Number); return a * 60 + b; };

  const waiting = activeList.filter((a) => a.status === "pending").length;
  const planned = activeList.reduce((s, a) => s + (a.price || 0), 0);
  const working = masters.filter((m) => spanOf(m)).length;
  main.append(h("div", { class: "day-sum" },
    narrow ? h("b", { class: "ds-date" }, `${WD_LONG[(new Date(day + "T12:00").getDay() + 6) % 7]}, ${+day.slice(8, 10)} ${MONTHS_GEN[+day.slice(5, 7) - 1]}`) : null,
    pendChip,
    h("span", {}, icon("calendar", "sm"), h("b", {}, activeList.length), " ", plural(activeList.length, "запис", "записи", "записів")),
    waiting && !pendChip ? h("span", { class: "warn" }, icon("alert", "sm"), "очікують: ", h("b", {}, waiting)) : null,
    !isMaster ? h("span", {}, icon("users", "sm"), working ? (narrow ? `${working} з ${masters.length}` : `працюють: ${working} з ${masters.length}`) : "усі вихідні") : null,
    planned ? h("span", {}, icon("banknote", "sm"), narrow ? "" : "за прайсом ", h("b", {}, money(planned))) : null));

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
          h("button", { class: "btn primary", onclick: () => quickBook({ date: day }) }, icon("plus", "sm"), "Записати клієнта")) : null));
    }
    main.append(agenda);
  } else {
    const STEP = 30, ROW = 46, HEAD = 60, TIMEW = narrow ? 36 : 52, GAP = 30;
    const fmt = (x) => `${pad(Math.floor(x / 60))}:${pad(x % 60)}`;
    const hrs = new Set();
    const addHours = (a0, a1) => { for (let hh = Math.floor(a0 / 60); hh < Math.ceil(a1 / 60); hh++) hrs.add(hh); };
    for (const m of masters) { const sp = spanOf(m); if (sp) addHours(toMin(sp[0]), toMin(sp[1])); }
    for (const a of activeList) addHours(toMin(hm(a.starts_at)), Math.max(toMin(hm(a.ends_at)), toMin(hm(a.starts_at)) + 1));
    if (!hrs.size) addHours(9 * 60, 20 * 60);
    const hours = [...hrs].sort((x, y) => x - y);
    const rowsDef = [];
    let yAcc = HEAD;
    hours.forEach((hh, k) => {
      if (k && hh !== hours[k - 1] + 1) { rowsDef.push({ gap: true, from: (hours[k - 1] + 1) * 60, to: hh * 60, y: yAcc, hgt: GAP }); yAcc += GAP; }
      for (const half of [0, STEP]) { rowsDef.push({ min: hh * 60 + half, y: yAcc, hgt: ROW }); yAcc += ROW; }
    });
    const startMin = hours[0] * 60, endMin = (hours[hours.length - 1] + 1) * 60;
    const yOf = (x) => {
      for (const r of rowsDef) {
        if (r.gap) { if (x >= r.from && x < r.to) return r.y + GAP * (x - r.from) / (r.to - r.from); }
        else if (x >= r.min && x < r.min + STEP) return r.y + ((x - r.min) / STEP) * ROW;
        else if (x < r.min) return r.y;
      }
      return yAcc;
    };
    const colW = isMaster ? "minmax(240px, 1fr)" : "minmax(180px, 1fr)";

    const grid = h("div", { class: "cal-grid", style: { gridTemplateColumns: `${TIMEW}px repeat(${masters.length}, ${colW})`, gridTemplateRows: `${HEAD}px ` + rowsDef.map((r) => r.hgt + "px").join(" ") } });
    grid.append(h("div", { class: "cal-corner" }));
    masters.forEach((m) => {
      const mine = activeList.filter((a) => a.master_id === m.id);
      const sum = mine.reduce((t, a) => t + (a.price || 0), 0);
      const sp = spanOf(m);
      const hours = sp ? `${sp[0]}–${sp[1]}` : (mine.length ? "поза графіком" : "вихідний");
      grid.append(h("div", { class: "cal-head" + (sp || mine.length ? "" : " is-off") }, avatar(m.full_name, "sm"),
        h("div", { class: "who" }, h("b", {}, m.full_name),
          h("span", {}, hours, mine.length ? ` · ${mine.length} ${plural(mine.length, "запис", "записи", "записів")}` : "")),
        sum ? h("i", { class: "cnt" }, money(sum)) : null));
    });
    rowsDef.forEach((row, r) => {
      if (row.gap) {
        grid.append(h("div", { class: "cal-gap", style: { gridColumn: `1 / span ${masters.length + 1}` } },
          h("span", {}, `${fmt(row.from)}–${fmt(row.to)}`), " · без записів"));
        return;
      }
      const min = row.min;
      const time = fmt(min);
      grid.append(h("div", { class: "cal-time" + (r === 0 || rowsDef[r - 1].gap ? " first" : "") }, min % 60 === 0 ? time : ""));
      masters.forEach((m, mi) => {
        const sp = spanOf(m);
        const off = !sp || min < toMin(sp[0]) || min >= toMin(sp[1]);
        grid.append(h("div", { class: "cal-cell" + (mi ? "" : " c0") + (off ? " off" : "") + (min % 60 ? " half" : ""), title: off ? "Неробочий час" : `Новий запис · ${m.full_name} · ${time}`,
          onclick: off ? null : () => quickBook({ master_id: m.id, date: day, time }) }, off ? null : h("span", { class: "cell-add" }, "+ ", time)));
      });
    });
    const wrap = h("div", { class: "cal" }, grid);
    main.append(wrap);
    main.append(h("div", { class: "legend" },
      ["pending", "confirmed", "in_progress", "completed"].map((st) => chip(st)),
      h("span", {}, "Натисніть на вільний час, щоб записати клієнта")));
    requestAnimationFrame(() => {
      const heads = grid.querySelectorAll(".cal-head");
      activeList.forEach((a, i) => {
        const col = masters.findIndex((m) => m.id === a.master_id);
        if (col < 0) return;
        const head = heads[col];
        const top = yOf(toMin(hm(a.starts_at)));
        const height = Math.max(yOf(toMin(hm(a.ends_at))) - top - 3, 26);
        const short = height < 60;
        const nowM = day === today() ? new Date().getHours() * 60 + new Date().getMinutes() : -1;
        const past = nowM >= 0 && toMin(hm(a.ends_at)) <= nowM && a.status !== "completed";
        grid.append(h("div", { class: `cal-ev st-${a.status}` + (short ? " short" : "") + (past ? " past" : ""),
          title: `${hm(a.starts_at)}–${hm(a.ends_at)} · ${a.client.name} · ${a.service_name} · ${money(a.price)} · ${STATUS[a.status] || ""}`,
          style: { top: top + 2 + "px", height: height + "px", left: head.offsetLeft + 4 + "px", width: head.offsetWidth - 8 + "px", animationDelay: Math.min(i * 25, 300) + "ms" },
          onclick: () => apptSheet(a, reload) },
          h("div", { class: "ev-top" }, h("span", { class: "ev-time" }, `${hm(a.starts_at)}–${hm(a.ends_at)}`), h("b", {}, a.client.name),
            h("span", { class: "ev-st", title: STATUS[a.status] || "" }, icon(EV_ICON[a.status] || "clock", "sm"))),
          h("div", { class: "ev-sub" }, h("span", { class: "ev-svc" }, icon(/фарб|тон|мелір|блонд/i.test(a.service_name) ? "dye" : "scissors", "sm"),
            ` ${a.service_name} · ${Math.floor(a.duration_min / 60)}:${String(a.duration_min % 60).padStart(2, "0")}`), h("b", { class: "ev-price" }, money(a.price))),
          short ? null : h("div", { class: "ev-meta" }, `${hm(a.starts_at)}–${hm(a.ends_at)} · ${STATUS[a.status] || ""}`,
            a.client.phone ? h("a", { href: "tel:" + a.client.phone, class: "ev-call", "aria-label": "Подзвонити", onclick: (e) => e.stopPropagation() }, icon("phone", "sm")) : null)));
      });
      masters.forEach((m, col) => {
        const sp = spanOf(m);
        if (!sp) return;
        const head = heads[col];
        const busy = activeList.filter((a) => a.master_id === m.id).map((a) => [toMin(hm(a.starts_at)), toMin(hm(a.ends_at))]).sort((x, y) => x[0] - y[0]);
        let cur = Math.max(toMin(sp[0]), day === today() ? Math.ceil((new Date().getHours() * 60 + new Date().getMinutes()) / STEP) * STEP : 0);
        const end = toMin(sp[1]);
        const gaps = [];
        for (const [b0, b1] of busy) { if (b0 - cur >= STEP) gaps.push([cur, b0]); cur = Math.max(cur, b1); }
        if (end - cur >= STEP) gaps.push([cur, end]);
        for (const [g0, g1] of gaps) {
          const len = g1 - g0;
          grid.append(h("button", { class: "cal-free", type: "button",
            style: { top: yOf(g0) + 4 + "px", left: head.offsetLeft + 6 + "px", width: head.offsetWidth - 12 + "px" },
            onclick: () => quickBook({ master_id: m.id, date: day, time: fmt(g0) }) },
            icon("plus", "sm"), `Вільно ${fmt(g0)}–${fmt(g1)}`, h("span", {}, len >= 60 ? ` · ${Math.floor(len / 60)} год${len % 60 ? " " + (len % 60) + " хв" : ""}` : ` · ${len}\u00a0хв`)));
        }
      });
      if (day === today()) {
        const now = new Date();
        const nm = now.getHours() * 60 + now.getMinutes();
        if (nm >= startMin && nm <= endMin) {
          grid.append(h("div", { class: "now-line", style: { top: yOf(nm) + "px", left: TIMEW + "px", right: "0" } }),
            h("span", { class: "now-pill", style: { top: yOf(nm) + "px", width: TIMEW + "px" } }, `${pad(Math.floor(nm / 60))}:${pad(nm % 60)}`));
          if (params.get("d") === null) wrap.scrollTop = Math.max(0, yOf(nm) - HEAD - 120);
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

VIEWS.earnings = async (main) => {
  const [my, bonus] = await Promise.all([api.get("/api/finance/my"), api.get("/api/bonuses/my").catch(() => null)]);
  main.append(topbar("Мої фінанси", "50% від ціни кожної послуги + 10% від проданих товарів"));
  const cs = my.current_shift;
  const ms = my.month_summary;
  main.append(h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Поточна зміна")),
    cs ? h("div", { class: "stack" },
      h("div", { class: "sum-line" }, h("span", {}, "Замовлень"), h("b", {}, String(cs.orders_count))),
      h("div", { class: "sum-line" }, h("span", {}, "Заробіток"), h("b", {}, money(cs.earnings))),
      h("div", { class: "sum-line" }, h("span", {}, "Взяти з каси"), h("b", {}, money(cs.takes_from_till))),
      h("div", { class: "sum-line total" }, h("span", {}, "До виплати від директора"), h("span", {}, money(cs.to_pay))))
      : h("span", { class: "muted" }, "Зміну не відкрито")));
  main.append(h("div", { class: "grid kpi", style: { marginTop: "12px" } },
    kpi("Заробіток за місяць", ms.earnings, "", { ico: "chart", sub: `${my.month.orders_count} посл.` }),
    kpi("Виплачено за місяць", ms.paid, "", { ico: "wallet" }),
    kpi("Борг салону мені", ms.debt, "dark", { ico: "coins" }),
    kpi("Бонус за товари", ms.product_bonus, "", { ico: "box", sub: "виплата раз на місяць" })));
  const max = Math.max(1, ...my.last_7_days.map((x) => x.earning));
  main.append(h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Останні 7 днів")),
    h("div", { class: "bars7", style: { display: "flex", alignItems: "flex-end", gap: "6px", height: "120px" } },
      my.last_7_days.map((x) => h("div", { style: { flex: "1", display: "flex", flexDirection: "column", alignItems: "center", gap: "4px" }, title: money(x.earning) },
        h("div", { style: { width: "100%", height: `${Math.round((x.earning / max) * 90)}px`, minHeight: "2px", background: "var(--brand)", borderRadius: "6px 6px 2px 2px" } }),
        h("span", { class: "muted small" }, dm(x.date)))))));
  main.append(h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Топ-3 послуги місяця")),
    my.top_services.length ? my.top_services.map((x) => h("div", { class: "sum-line" }, h("span", {}, `${x.service}: ${x.count}×`), h("b", {}, `→ ${money(x.earnings)}`)))
      : h("span", { class: "muted" }, "Ще немає замовлень")));
  const TYPES = { order: "Замовлення", payout: "Виплата", refunded: "Повернення", cancelled: "Скасовано" };
  main.append(h("div", { class: "section" }, h("h2", {}, "Історія (останні 20)")));
  main.append(my.history.length ? h("div", { class: "card table-wrap" }, h("table", {},
    h("tr", {}, h("th", {}, "Дата"), h("th", {}, "Тип"), h("th", {}, ""), h("th", { class: "num" }, "Сума")),
    my.history.map((x) => h("tr", {}, h("td", {}, dmy(x.date)), h("td", {}, TYPES[x.type] || x.type), h("td", { class: "muted" }, x.title),
      h("td", { class: "num" }, h("b", { class: x.amount < 0 ? "neg" : "" }, money(x.amount)))))))
    : h("div", { class: "card" }, emptyState("Історія порожня", "history")));
  if (bonus && bonus.history.length) {
    main.append(h("div", { class: "section" }, h("h2", {}, "Бонус за товари")));
    main.append(h("div", { class: "card" }, bonus.history.slice(0, 10).map((b) => h("div", { class: "sum-line small" },
      h("span", {}, dmy(b.accrued_on), " · ", b.note || "", b.paid ? h("span", { class: "muted" }, " (виплачено)") : null), h("b", {}, money(b.amount))))));
  }
};

function shiftReport(r) {
  const n = (label, v, ico) => h("div", { class: "card kpi-tile" }, h("div", { class: "kpi-top" }, h("span", { class: "kpi-ico" }, icon(ico, "sm")), h("span", { class: "kpi-label" }, label)), h("div", { class: "kpi-value" }, v));
  return h("div", { class: "stack" },
    h("div", { class: "grid", style: { gridTemplateColumns: "repeat(3, 1fr)" } },
      n("Клієнтів", r.clients_count, "users"), n("Послуг", r.services_count, "scissors"), n("Скасувань", r.cancellations, "cancel")),
    h("div", { class: "card" },
      (r.services || []).map((s) => h("div", { class: "sum-line" }, h("span", {}, s.service, h("span", { class: "muted" }, " × ", s.count)), h("span", {}, money(s.revenue)))),
      r.revenue !== undefined ? h("div", { class: "divider" }) : null,
      r.revenue !== undefined ? h("div", { class: "sum-line" }, h("span", {}, "Загальна виручка"), h("b", {}, money(r.revenue))) : null,
      r.discounts ? h("div", { class: "sum-line muted small" }, h("span", {}, "Знижки (за рахунок салону)"), h("span", {}, money(r.discounts))) : null,
      r.unpaid && r.unpaid.count ? h("div", { class: "sum-line small" }, h("span", {}, `Неоплачені: ${r.unpaid.count}`), h("span", {}, money(r.unpaid.amount))) : null,
      Object.entries(r.by_payment || {}).map(([k, v]) => h("div", { class: "sum-line muted small" }, h("span", {}, PAY[k] || k), h("span", {}, money(v)))),
      r.cash ? null : h("div", { class: "sum-line" }, h("span", {}, "Заробіток майстра"), h("b", {}, money(r.master_earning))),
      r.cash ? cashBlock(r) : h("div", { class: "sum-line total" }, h("span", {}, "Сума до виплати"), h("span", {}, money(r.payout_due)))));
}

function cashBlock(r) {
  const c = r.cash;
  const line = (label, v, cls = "") => h("div", { class: "sum-line " + cls }, h("span", {}, label), h("b", {}, money(v)));
  const takeFromTill = c.mode === "take_from_till";
  const fromTill = takeFromTill ? c.master_takes : 0;
  const toCard = takeFromTill ? c.director_pays : r.payout_due;
  return h("div", {},
    h("div", { class: "divider" }),
    h("div", { style: { background: "var(--ok-bg)", borderRadius: "10px", padding: "10px 12px", margin: "4px 0 10px" } },
      h("div", { class: "sum-line", style: { fontSize: "16px" } }, h("b", {}, "Заробіток майстра"), h("b", { style: { color: "var(--ok)" } }, money(r.master_earning))),
      line("💵 Готівкою з каси (одразу)", fromTill, "small"),
      line(takeFromTill ? "💳 На карту від директора" : "💳 Виплатить директор", toCard, "small"),
      takeFromTill && toCard <= 0 ? h("div", { class: "small muted" }, "Увесь заробіток отримано готівкою з каси") : null,
      takeFromTill && toCard > 0 && fromTill === 0 ? h("div", { class: "small muted" }, "Клієнти платили карткою, тож готівки в касі немає — увесь заробіток директор переказує на карту") : null),
    h("div", { class: "kpi-label", style: { margin: "4px 0 6px" } }, "Каса"),
    line("Готівка від клієнтів", c.from_clients),
    line("Карткою — на рахунок салону", c.noncash, "muted"),
    h("div", { class: "sum-line total" }, h("span", {}, "🏦 Залишити в касі"), h("span", {}, money(c.leave_in_till))));
}

VIEWS.shift = async (main) => {
  const [cur, history] = await Promise.all([api.get("/api/shifts/current"), api.get("/api/shifts")]);
  main.append(topbar("Моя зміна", cur ? `Відкрита з ${dmy(cur.opened_at)} ${hm(cur.opened_at)}` : "Зміна відкривається автоматично з першим закритим замовленням"));
  if (cur) {
    const bad = (cur.report.orders || []).filter((o) => o.missing && o.missing.length);
    if (bad.length) main.append(incompleteBlock(bad, reload));
    main.append(shiftReport(cur.report));
    if (bad.length) {
      main.append(h("button", { class: "btn big", style: { marginTop: "14px" }, disabled: true }, icon("lock"), "Спершу заповніть замовлення"));
    } else main.append(h("button", { class: "btn primary big", style: { marginTop: "14px" }, onclick: () => confirmSheet("Закрити зміну?",
      "Буде сформовано звіт, директор отримає його на підтвердження виплати.", "Закрити зміну", async () => {
        const sh = await run(() => api.post("/api/shifts/close"), "Зміну закрито");
        renderApp();
        setTimeout(() => openSheet("Звіт зміни", shiftReport(sh.report)), 60);
      }) }, icon("lock"), "Закрити зміну"));
  } else {
    main.append(h("div", { class: "card" }, emptyState("Зараз зміна не відкрита", "clock"),
      h("div", { style: { textAlign: "center" } }, h("button", { class: "btn primary", onclick: async (e) => { await openMyShift(e.currentTarget); reload(); } }, icon("ok", "sm"), "Відкрити зміну зараз"))));
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
    u.role === "director" ? await devicesCard(u) : null));
};

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

const SEG_CHIP = { new: "st-confirmed", regular: "st-completed", vip: "st-in_progress", lapsed: "st-pending", inactive: "st-cancelled" };
const SOURCES = ["instagram", "рекомендація", "сайт", "telegram-бот", "проходили повз", "google", "інше"];
async function clientSheet(id, onChange) {
  const isMgr = state.user.role !== "master", isDir = state.user.role === "director";
  let c, hist;
  try { [c, hist] = await Promise.all([api.get(`/api/clients/${id}/card`), api.get(`/api/clients/${id}`)]); } catch (e) { return toast(e.message, true); }
  const reopen = () => { clientSheet(id, onChange); onChange && onChange(); };
  const body = h("div", { class: "stack" });
  const ago = (iso) => (iso ? dmy(iso) : "—");
  const stat = (label, value) => h("div", {}, h("span", {}, label), h("b", {}, value));
  const tabs = [["info", "Загальне"], ["visits", "Візити"], ...(isMgr ? [["money", "Фінанси"]] : []), ["svc", "Послуги й майстри"], ["msg", "Повідомлення"], ["notes", "Примітки"]];
  const draw = (tab) => {
    if (tab === "info") {
      body.replaceChildren(
        h("div", { class: "row", style: { gap: "12px", alignItems: "center" } }, avatar(c.name, "lg"),
          h("div", { class: "grow" }, h("div", { class: "row", style: { gap: "8px" } }, h("b", { style: { fontSize: "17px" } }, c.name), h("span", { class: "chip " + (SEG_CHIP[c.segment] || "") }, c.segment_label)),
            h("div", { class: "muted small" }, phoneLink(c.phone), c.phone2 ? [" · ", phoneLink(c.phone2)] : null))),
        h("div", { class: "mini-stats" }, stat("Візитів", String(c.visits)), c.spent !== undefined ? stat("Витрачено", money(c.spent)) : null,
          c.average_check !== undefined ? stat("Середній чек", money(c.average_check)) : null, stat("Останній візит", ago(c.last_visit)),
          stat("Інтервал", c.avg_interval_days ? `${c.avg_interval_days} дн.` : "—"), stat("Скасувань / неявок", `${c.cancelled} / ${c.no_show}`)),
        c.next_visit_hint ? h("div", { class: "callout", style: { margin: 0 } }, icon("calendar"), h("div", { class: "small" }, "Рекомендований наступний візит — близько ", h("b", {}, dmy(c.next_visit_hint)), " (за звичним інтервалом).")) : null,
        h("div", { class: "info-list" },
          h("div", { class: "info-row" }, icon("calendar"), "День народження: ", h("b", {}, c.birthday ? dmy(c.birthday) : "—")),
          h("div", { class: "info-row" }, icon("send"), "Telegram: ", h("b", {}, c.telegram_linked ? "підключено" : "ні"), c.telegram_username ? ` · @${c.telegram_username}` : ""),
          h("div", { class: "info-row" }, icon("trend"), "Джерело: ", h("b", {}, c.source || "—")),
          h("div", { class: "info-row" }, icon("megaphone"), "Згода на рекламу: ", h("b", {}, c.marketing_consent ? "так" : "ні")),
          h("div", { class: "info-row" }, icon("user"), "Картка з ", dmy(c.created_at), c.first_visit ? ` · перший візит ${dmy(c.first_visit)}` : ""),
          c.blocked ? h("div", { class: "info-row neg" }, icon("lock"), "Онлайн-запис заблоковано", c.blocked_reason ? ` · ${c.blocked_reason}` : "") : null),
        isMgr ? h("div", { class: "grid", style: { gridTemplateColumns: "1fr 1fr", gap: "8px" } },
          h("button", { class: "btn", onclick: () => editForm() }, icon("edit", "sm"), "Редагувати"),
          h("button", { class: "btn", onclick: () => quickBook({ client: c }) }, icon("plus", "sm"), "Записати"),
          c.blocked ? h("button", { class: "btn", onclick: async () => { await run(() => api.patch(`/api/clients/${id}/card`, { blocked: false }), "Онлайн-запис дозволено"); reopen(); } }, icon("ok", "sm"), "Дозволити онлайн-запис")
            : h("button", { class: "btn", onclick: () => confirmSheet("Заблокувати онлайн-запис?", `${c.name} не зможе записатися через бот і сайт.`, "Заблокувати",
              async (reason) => { await run(() => api.patch(`/api/clients/${id}/card`, { blocked: true, blocked_reason: reason || null }), "Заблоковано"); reopen(); }, { danger: true, withReason: true }) }, icon("lock", "sm"), "Заблокувати"),
          isDir ? h("button", { class: "btn danger", onclick: () => confirmSheet("Знеособити клієнта?", "Ім'я, телефони, дата народження, Telegram і примітки буде стерто назавжди. Візити й суми лишаться у звітах.", "Знеособити",
            async (reason) => { await run(() => api.post(`/api/clients/${id}/anonymize`, { reason }), "Дані знеособлено"); reopen(); }, { danger: true, withReason: true }) }, icon("trash", "sm"), "Знеособити") : null) : null);
    } else if (tab === "visits") {
      const list = [...hist.history].sort((a, b) => b.starts_at.localeCompare(a.starts_at));
      body.replaceChildren(list.length ? h("div", { class: "card ord-list" }, list.map((a) => h("div", { class: "ord-item" },
        h("div", { class: "ord-time" }, dm(a.starts_at)), h("div", { class: "grow ord-main" }, h("b", {}, a.service_name), h("div", { class: "muted small" }, `${hm(a.starts_at)} · ${a.master_name}`)),
        h("div", { class: "ord-amt" }, chip(a.status), h("span", { class: "small muted" }, money(a.price)))))) : emptyState("Ще немає візитів", "calendar"));
    } else if (tab === "money") {
      const f = c.finance || { spent: 0, orders: 0, by_month: [], materials: 0 };
      const max = Math.max(1, ...f.by_month.map((x) => x.amount));
      body.replaceChildren(h("div", { class: "mini-stats" }, stat("Оплачено", money(f.spent)), stat("Замовлень", String(f.orders)),
        stat("Середній чек", money(c.average_check || 0)), stat("Матеріали", money(f.materials))),
        f.by_month.length ? h("div", {}, h("h3", {}, "По місяцях"), barList(f.by_month.map((x) => [MONTHS_UA[+x.month.slice(5) - 1] + " " + x.month.slice(0, 4), x.amount]), max)) : emptyState("Оплачених замовлень ще немає", "receipt"),
        h("p", { class: "muted small", style: { margin: 0 } }, "Лише закриті (оплачені) замовлення — та сама сума, що у «Замовленнях» і звітах."));
    } else if (tab === "svc") {
      body.replaceChildren(h("h3", {}, "Найчастіші послуги"),
        c.top_services.length ? barList(c.top_services.map((x) => [x.name, x.count * 100, `${x.count} раз`]), c.top_services[0].count * 100) : emptyState("Ще немає", "scissors"),
        h("h3", {}, "Майстри"), c.top_masters.length ? h("div", { class: "stack" }, c.top_masters.map((m) => h("div", { class: "row" }, avatar(m.name, "sm"), h("span", { class: "grow" }, m.name), h("b", {}, `${m.count} візитів`)))) : emptyState("Ще немає", "user"));
    } else if (tab === "msg") {
      const msgBox = h("div", {}, h("p", { class: "muted small" }, "Завантаження…"));
      body.replaceChildren(h("div", { class: "info-list" },
        h("div", { class: "info-row" }, icon("send"), "Telegram: ", h("b", {}, c.telegram_linked ? "підключено — нагадування й сервісні повідомлення приходять у бот" : "не підключено")),
        h("div", { class: "info-row" }, icon("megaphone"), "Реклама: ", h("b", {}, c.marketing_consent ? "дозволено" : "не надсилати (немає згоди)")),
        h("div", { class: "info-row" }, icon("history"), "Останній контакт: ", h("b", {}, c.last_contact_at ? `${dmy(c.last_contact_at)} ${hm(c.last_contact_at)}` : "—"))),
        msgBox);
      if (isMgr) api.get(`/api/sms/messages?client_id=${id}`).then((list) => msgBox.replaceChildren(list.length
        ? h("div", { class: "card ord-list" }, list.map((m) => h("div", { class: "ord-item" }, h("div", { class: "ord-time" }, dm(m.created_at)),
          h("div", { class: "grow ord-main muted small sms-text" }, m.text), h("div", { class: "ord-amt" }, smsChip(m.status)))))
        : h("p", { class: "muted small" }, "SMS / Viber цьому клієнту ще не надсилались."))).catch(() => {});
    } else {
      const notes = h("textarea", { rows: 4, placeholder: "Примітки адміністратора (бачить персонал)" }, c.notes || "");
      const wishes = h("textarea", { rows: 3, placeholder: "Побажання клієнта: улюблений майстер, напій, алергії, формула фарби…" }, c.wishes || "");
      body.replaceChildren(field("Побажання клієнта", wishes), field("Примітки", notes),
        isMgr ? h("button", { class: "btn primary", onclick: async () => { await run(() => api.patch(`/api/clients/${id}/card`, { notes: notes.value, wishes: wishes.value }), "Збережено"); reopen(); } }, "Зберегти") : null);
      if (!isMgr) { notes.disabled = true; wishes.disabled = true; }
    }
  };
  function editForm() {
    const f = { name: h("input", { value: c.name }), phone: h("input", { type: "tel", value: c.phone || "", placeholder: "+380…" }), phone2: h("input", { type: "tel", value: c.phone2 || "", placeholder: "додатковий" }),
      birthday: h("input", { type: "date", value: c.birthday || "" }), telegram_username: h("input", { value: c.telegram_username || "", placeholder: "@username" }),
      source: select([["", "—"], ...SOURCES.map((x) => [x, x])], c.source || "") };
    const consent = h("input", { type: "checkbox", checked: !!c.marketing_consent });
    openSheet(`Редагувати: ${c.name}`, h("div", { class: "stack" }, field("Ім'я та прізвище", f.name),
      h("div", { class: "form-grid" }, field("Телефон", f.phone), field("Додатковий телефон", f.phone2)),
      h("div", { class: "form-grid" }, field("Дата народження", f.birthday), field("Telegram", f.telegram_username)),
      field("Звідки дізнався", f.source), h("label", { class: "row" }, consent, "Згода на рекламні повідомлення"),
      h("button", { class: "btn primary big", onclick: async () => {
        const b = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value.trim() || null]));
        b.name = f.name.value.trim(); b.marketing_consent = consent.checked;
        if (await run(() => api.patch(`/api/clients/${id}/card`, b), "Картку збережено")) reopen();
      } }, "Зберегти")));
  }
  const tabBar = seg(tabs, "info", (v) => draw(v));
  tabBar.classList.add("card-tabs");
  openSheet(c.name, h("div", { class: "stack" }, tabBar, body));
  draw("info");
}

const SMS_STATUS = { queued: ["у черзі", "st-pending"], sent: ["надіслано", "st-confirmed"], delivered: ["доставлено", "st-completed"],
  failed: ["помилка", "st-overdue"], not_configured: ["не підключено", "st-cancelled"] };
const smsChip = (st) => { const [l, c] = SMS_STATUS[st] || [st, ""]; return h("span", { class: "chip " + c }, l); };
const smsParts = (t) => (t.length <= 70 ? 1 : Math.ceil(t.length / 67));
VIEWS.sms = async (main) => {
  const [sm, ins, camps, msgs] = await Promise.all([api.get("/api/sms/summary"), api.get("/api/sms/insights"), api.get("/api/sms/campaigns"), api.get("/api/sms/messages")]);
  const st = state.settings || {};
  main.append(topbar("SMS-розсилки", "Viber + SMS через TurboSMS: баланс, розсилки, аналіз бази",
    h("button", { class: "btn primary", onclick: () => campaignForm() }, icon("send", "sm"), "Нова розсилка")));
  main.append(h("div", { class: "grid kpi kpi-6" },
    kpi("Баланс TurboSMS", sm.balance === null ? "—" : `${sm.balance.toLocaleString("uk-UA", { maximumFractionDigits: 2 })} ₴`, "dark", { ico: "wallet", raw: true, sub: sm.configured ? "залишок на рахунку" : "не підключено" }),
    kpi("Витрачено цього місяця", sm.spent_month, "", { ico: "banknote", sub: `усього ${money(sm.spent_total)} · у «Витратах»` }),
    kpi("Надіслано цього місяця", sm.sent_month, "", { ico: "send", raw: true, sub: `усього ${sm.sent_total}` }),
    kpi("Клієнтів зі згодою", sm.consent_clients, "accent", { ico: "users", raw: true, sub: "лише їм — реклама" })));
  if (!sm.configured || !sm.enabled) {
    main.append(h("div", { class: "callout" }, icon("alert"), h("div", { class: "small" },
      h("b", {}, sm.configured ? "TurboSMS підключено, але SMS вимкнено. " : "TurboSMS ще не підключено. "),
      sm.configured ? "Увімкніть нижче в «Налаштуваннях SMS»." : "Зареєструйтесь на turbosms.ua (Viber + SMS), додайте змінні сервера MDS_TURBOSMS_TOKEN і MDS_TURBOSMS_SENDER. До того розсилки не надсилаються, а повідомлення в історії мають статус «не підключено».")));
  }
  main.append(h("div", { class: "section" }, h("h2", {}, "Аналіз бази")),
    h("div", { class: "card stack" },
      h("div", { class: "row", style: { gap: "10px", alignItems: "center" } }, icon("clock"),
        h("div", { class: "grow" }, "Найкращий час для повідомлень: ", h("b", {}, `${ins.best_day}, ${ins.best_hour}`),
          h("div", { class: "muted small" }, `за ${ins.sample} записами за пів року — трохи раніше, ніж клієнти зазвичай записуються`))),
      ins.recommendations.length ? h("div", { class: "rec-grid" }, ins.recommendations.map((r) => h("div", { class: "rec" },
        h("div", { class: "row" }, h("b", { class: "grow" }, r.title), h("span", { class: "chip plain" }, `${r.count}`)),
        h("div", { class: "muted small" }, `${r.audience_label} · «${r.text.replace("{name}", "Олено")}»`),
        h("button", { class: "btn sm", onclick: () => campaignForm({ audience: r.audience, title: r.title, text: r.text }) }, icon("send", "sm"), "Створити розсилку"))))
        : h("p", { class: "muted small", style: { margin: 0 } }, "Поки немає клієнтів зі згодою на рекламу — відмічайте згоду в картках клієнтів.")));
  const on = h("input", { type: "checkbox", checked: !!st.sms_enabled });
  const price = h("input", { inputmode: "decimal", value: kopToInput(st.sms_price || 0), placeholder: "напр. 1,20" });
  const bday = h("input", { type: "checkbox", checked: !!st.sms_auto_birthday });
  const ret = h("input", { type: "checkbox", checked: !!st.sms_auto_return });
  const hour = select(Array.from({ length: 14 }, (_, i) => [i + 8, `${i + 8}:00`]), st.sms_auto_hour || 11);
  const bdayText = h("textarea", { rows: 2 }, st.sms_birthday_text || "");
  const retText = h("textarea", { rows: 2 }, st.sms_return_text || "");
  const testPhone = h("input", { type: "tel", placeholder: "Ваш номер для тесту" });
  main.append(h("details", { class: "card", style: { marginTop: "12px" } }, h("summary", { class: "row", style: { cursor: "pointer" } }, icon("settings", "sm"), h("b", {}, "Налаштування SMS й автоматизація")),
    h("div", { class: "stack", style: { marginTop: "12px" } },
      h("label", { class: "row" }, on, "SMS / Viber увімкнено"),
      field("Ціна 1 SMS за тарифом, ₴ (для обліку витрат)", price),
      h("label", { class: "row" }, bday, "Вітати з днем народження (зі згодою, раз на рік)"), field("Текст привітання ({name} — ім'я)", bdayText),
      h("label", { class: "row" }, ret, "Нагадувати про повторний візит, коли настав звичний інтервал (зі згодою, не частіше раз на 30 днів)"), field("Текст нагадування", retText),
      field("Надсилати автоматичні о", hour),
      h("button", { class: "btn primary", onclick: async () => {
        state.settings = await run(() => api.put("/api/settings", { sms_enabled: on.checked, sms_price: toKop(price.value) || 0, sms_auto_birthday: bday.checked,
          sms_auto_return: ret.checked, sms_auto_hour: Number(hour.value), sms_birthday_text: bdayText.value.trim(), sms_return_text: retText.value.trim() }), "Збережено");
        reload();
      } }, "Зберегти"),
      h("div", { class: "row" }, h("div", { class: "grow" }, testPhone), h("button", { class: "btn", onclick: async () => {
        if (!testPhone.value.trim()) return toast("Вкажіть номер", true);
        const r = await run(() => api.post("/api/sms/test", { phone: testPhone.value }));
        toast(r.ready ? "Тестове повідомлення в черзі — статус у «Повідомленнях»" : "TurboSMS не підключено — статус «не підключено»", !r.ready);
        setTimeout(reload, 1500);
      } }, "Тест")))));
  const hist = h("div");
  const drawHist = (tab) => hist.replaceChildren(tab === "camps"
    ? (camps.length ? h("div", { class: "card ord-list" }, camps.map((c) => h("div", { class: "ord-item" },
      h("div", { class: "ord-time" }, dm(c.created_at)),
      h("div", { class: "grow ord-main" }, h("b", {}, c.title), h("div", { class: "muted small" }, `${c.audience_label} · ${c.recipients} отримувачів · ${money(c.cost)}`),
        h("div", { class: "row small", style: { gap: "4px", flexWrap: "wrap" } }, Object.entries(c.statuses).map(([k, n]) => h("span", {}, smsChip(k), " ", n))))))) : h("div", { class: "card" }, emptyState("Розсилок ще не було", "send")))
    : (msgs.length ? h("div", { class: "card ord-list" }, msgs.map((m) => h("div", { class: "ord-item" },
      h("div", { class: "ord-time" }, dm(m.created_at), h("div", { class: "muted small" }, hm(m.created_at))),
      h("div", { class: "grow ord-main" }, h("b", {}, m.client_name || m.phone), h("div", { class: "muted small sms-text" }, m.text),
        m.error ? h("div", { class: "small neg" }, m.error) : null),
      h("div", { class: "ord-amt" }, smsChip(m.status), h("span", { class: "small muted" }, m.kind === "service" ? "сервісне" : "реклама"))))) : h("div", { class: "card" }, emptyState("Повідомлень ще немає", "send"))));
  main.append(h("div", { class: "section" }, h("h2", {}, "Історія")), h("div", { class: "filters" }, seg([["camps", "Розсилки"], ["msgs", "Повідомлення"]], "camps", drawHist)), hist);
  drawHist("camps");

  async function campaignForm(pre = {}) {
    const title = h("input", { value: pre.title || "", placeholder: "Напр. «Осіння акція»" });
    const aud = select(Object.entries({ all: "Усі зі згодою", new: "Нові", regular: "Постійні", vip: "VIP", lapsed: "Давно не були", inactive: "Неактивні", birthdays: "Дні народження (7 днів)", return_due: "Час повторного візиту" }), pre.audience || "all");
    const text = h("textarea", { rows: 4, maxlength: 670, placeholder: "{name}, …" }, pre.text || "");
    const info = h("div", { class: "card gen-preview stack" });
    const send = h("button", { class: "btn primary big" }, icon("send"), "Надіслати");
    let last = null, t;
    const preview = async () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        if (!text.value.trim()) { info.replaceChildren(h("span", { class: "muted small" }, "Напишіть текст — тут з'являться отримувачі й вартість")); return; }
        last = await api.post(`/api/sms/campaigns?dry_run=true`, { title: title.value || "чернетка", text: text.value, audience: aud.value }).catch((e) => { toast(e.message, true); return null; });
        if (!last) return;
        info.replaceChildren(h("div", { class: "sum-line" }, h("span", {}, "Отримувачів (зі згодою)"), h("b", {}, String(last.recipients))),
          h("div", { class: "sum-line" }, h("span", {}, `Довжина: ${text.value.length} симв. · ${last.parts} SMS на людину`), h("b", {}, money(last.cost))),
          h("div", { class: "muted small" }, "Так побачить клієнт: ", h("b", {}, last.preview)),
          !last.ready ? h("div", { class: "small neg" }, "TurboSMS не підключено або вимкнено — надіслати поки неможливо") : null);
        send.disabled = !last.ready || !last.recipients;
      }, 350);
    };
    [title, text].forEach((x) => x.addEventListener("input", preview)); aud.addEventListener("change", preview);
    send.addEventListener("click", () => confirmSheet("Надіслати розсилку?", `${last?.recipients || 0} отримувачів, ≈ ${money(last?.cost || 0)} (запишеться у «Витрати → Реклама»). Надіслане скасувати неможливо.`, "Надіслати",
      async () => { await run(() => api.post("/api/sms/campaigns", { title: title.value, text: text.value, audience: aud.value }), "Розсилку запущено"); reload(); }));
    openSheet("Нова розсилка", h("div", { class: "stack" }, field("Назва (для себе)", title), field("Кому", aud),
      field("Текст ({name} — ім'я клієнта)", text), info, send,
      h("p", { class: "muted small", style: { margin: 0 } }, "Лише клієнтам зі згодою на рекламу. Сервісні повідомлення (перенесення запису) надсилаються окремо, без згоди.")));
    preview();
  }
};

VIEWS.clients = async (main) => {
  const role = state.user.role, isDir = role === "director";
  if (role === "master") return clientsSimple(main);
  const st = { q: "", segment: "", sort: "name", page: 1, birthdays: false };
  const purge = async () => {
    const r = await run(() => api.post("/api/clients/purge-no-phone?dry_run=true"));
    const total = r.deleted + r.merged;
    if (!total) return toast(r.kept_future ? `Клієнти без номера мають майбутні записи (${r.kept_future}) — їх не чіпаємо` : "Клієнтів без номера немає");
    confirmSheet("Видалити клієнтів без номера?", `Буде прибрано: ${total}. ${r.merged ? `Візити ${r.merged} з них збережуться в картці «Клієнти без номера». ` : ""}`, "Видалити",
      async () => { await run(() => api.post("/api/clients/purge-no-phone"), "Готово"); reload(); }, { danger: true });
  };
  const doExport = () => run(() => downloadFile("/api/clients/export.csv", "klienty.csv"));
  const actionsSheet = () => openSheet("Дії з базою", h("div", { class: "stack" },
    isDir ? h("button", { class: "btn big", onclick: () => { closeSheet(); doExport(); } }, icon("filetext", "sm"), "Експорт у CSV") : null,
    h("button", { class: "btn big", onclick: () => { closeSheet(); dupSheet(); } }, icon("users", "sm"), "Знайти дублікати"),
    isDir ? h("button", { class: "btn big danger", onclick: () => { closeSheet(); purge(); } }, icon("trash", "sm"), "Видалити клієнтів без номера") : null));
  main.append(topbar("Клієнти", "Єдина база: картка, візити, витрати, сегменти",
    isDir ? h("button", { class: "btn only-desk", onclick: doExport }, icon("filetext", "sm"), "Експорт") : null,
    h("button", { class: "btn only-desk", onclick: dupSheet }, icon("users", "sm"), "Дублікати"),
    isDir ? h("button", { class: "btn ghost only-desk", onclick: purge }, icon("trash", "sm"), "Без номера") : null));
  let topBtn = null, topClients = [];
  if (isDir) {
    const box = h("div");
    const loadA = async (days) => {
      const to = today(), from = addDays(to, -days + 1);
      const a = await api.get(`/api/clients/analytics?date_from=${from}&date_to=${to}`).catch(() => null);
      if (!a) return;
      box.replaceChildren(h("div", { class: "grid kpi kpi-6" },
        kpi("Усього клієнтів", a.total_clients, "", { ico: "users", raw: true }),
        kpi("Нові", a.new_clients, "", { ico: "plus", raw: true }),
        kpi("Активні", a.active_clients, "", { ico: "check", raw: true, sub: `${a.visits} візитів` }),
        kpi("Повторні візити", `${Math.round(a.repeat_share_bp / 100)}%`, "", { ico: "history", raw: true }),
        kpi("Середній чек", a.average_check, "accent", { ico: "receipt" }),
        kpi("Перестали ходити", a.stopped, a.stopped ? "" : "", { ico: "alert", raw: true })));
      topClients = a.top_clients;
      topBtn.disabled = !topClients.length;
    };
    box.className = "cl-analytics";
    topBtn = h("button", { type: "button", class: "btn cl-top", onclick: () => openSheet("Найприбутковіші клієнти",
      topClients.length ? barList(topClients.map((x) => [x.name, x.spent, `${x.visits} віз.`]), topClients[0].spent) : emptyState("За період немає даних", "trend")) },
      icon("trend", "sm"), h("span", {}, "Найприбутковіші клієнти"));
    main.append(h("div", { class: "filters cl-period" }, seg([[30, "30 днів"], [90, "90 днів"], [365, "Рік"]], 30, (v) => loadA(Number(v)))), box);
    loadA(30);
  }
  const search = h("input", { type: "search", placeholder: "Ім'я, телефон", "aria-label": "Пошук: ім'я, прізвище або телефон", id: "client-search" });
  const sort = select([["name", "За ім'ям"], ["visits", "За візитами"], ...(isDir ? [["spent", "За витратами"]] : []), ["last_visit", "Нещодавні візити"], ["created", "Нові картки"]], "name", { style: { width: "auto" }, "aria-label": "Сортування" });
  const chips = h("div", { class: "chips-row cl-chips" });
  const list = h("div");
  const more = h("button", { class: "btn", style: { width: "100%", marginTop: "10px" } }, "Показати ще");
  const sToggle = searchToggle(search, sort);
  main.append(h("div", { class: "row cl-search", style: { marginBottom: "10px" } }, topBtn || h("span", { class: "grow" }), sToggle.btn,
    h("button", { class: "btn only-mob-flex cl-more", type: "button", "aria-label": "Дії: експорт, дублікати, без номера", onclick: actionsSheet }, icon("more"))), sToggle.box, chips, list, more);
  let req = 0, items = [];
  async function load(append = false) {
    const my = ++req;
    if (!append) { st.page = 1; list.replaceChildren(h("div", { class: "skeleton" }, h("i"), h("i"), h("i"))); }
    const qs = new URLSearchParams({ q: st.q, segment: st.segment, sort: st.sort, page: st.page, per_page: 40 });
    if (st.birthdays) qs.set("birthdays", 14);
    const r = await api.get("/api/clients/base?" + qs).catch((e) => { toast(e.message, true); return null; });
    if (!r || my !== req) return;
    items = append ? items.concat(r.items) : r.items;
    const chip = (key, label, n) => h("button", { class: "chip-btn" + (st.segment === key ? " on" : ""),
      onclick: () => { st.birthdays = false; st.segment = st.segment === key ? "" : key; load(); } }, label, n !== null ? h("span", { class: "chip-n" }, String(n)) : null);
    chips.replaceChildren(...["new", "regular", "lapsed"].filter((k) => r.segment_labels[k]).map((k) => chip(k, r.segment_labels[k], r.segments[k])));
    list.replaceChildren(items.length ? h("div", { class: "card ord-list anim-in" }, items.map((c) => h("button", { class: "ord-item cl-item", onclick: () => clientSheet(c.id, () => load()) },
      avatar(c.name, "sm"),
      h("div", { class: "grow ord-main" }, h("b", {}, c.name), h("div", { class: "muted small" }, c.phone || "без номера", c.birthday && st.birthdays ? ` · 🎂 ${dm(c.birthday)}` : "")),
      h("div", { class: "ord-amt" }, h("span", { class: "chip " + (SEG_CHIP[c.segment] || "") }, c.segment_label),
        h("span", { class: "small muted" }, `${c.visits} віз.${c.spent !== undefined ? " · " + money(c.spent) : ""}`))))) : h("div", { class: "card" }, emptyState(st.q ? "Нікого не знайдено" : "Клієнтів у цій категорії немає", "users")));
    more.hidden = items.length >= r.total;
    more.textContent = `Показати ще (${r.total - items.length})`;
  }
  let t;
  search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { st.q = search.value.trim(); load(); }, 300); });
  sort.addEventListener("change", () => { st.sort = sort.value; load(); });
  more.addEventListener("click", () => { st.page += 1; load(true); });
  async function dupSheet() {
    const groups = await run(() => api.get("/api/clients/duplicates"));
    openSheet("Можливі дублікати", h("div", { class: "stack" },
      h("p", { class: "muted small", style: { margin: 0 } }, "Однакове ім'я або додатковий номер збігається з основним. Об'єднання переносить усі візити й замовлення в першу картку."),
      groups.length ? groups.map((g) => h("div", { class: "card stack", style: { boxShadow: "none" } },
        g.map((c, i) => h("div", { class: "row" }, h("span", { class: "grow" }, h("b", {}, c.name), h("span", { class: "muted small" }, " · ", c.phone || "без номера", ` · #${c.id}`),
          i ? (isDir ? h("button", { class: "btn sm", onclick: () => confirmSheet("Об'єднати клієнтів?", `«${c.name}» (#${c.id}) буде об'єднано в «${g[0].name}» (#${g[0].id}). Візити й замовлення переносяться.`, "Об'єднати",
            async () => { await run(() => api.post(`/api/clients/${g[0].id}/merge`, { duplicate_id: c.id }), "Об'єднано"); dupSheet(); load(); }) }, "Об'єднати в першу") : null)
            : h("span", { class: "chip plain" }, "основна")))))) : emptyState("Дублікатів не знайдено", "check")));
  }
  load();
};
async function clientsSimple(main) {
  main.append(topbar("Клієнти", "Ваші клієнти та їхні візити"));
  const q = h("input", { placeholder: "Пошук за ім'ям або телефоном", type: "search", id: "client-search" });
  const box = h("div", { class: "card ord-list", style: { marginTop: "12px" } });
  const sToggle = searchToggle(q);
  main.querySelector(".topbar > .row").prepend(sToggle.btn);
  main.append(sToggle.box, box);
  let timer;
  const load = async () => {
    const list = await api.get("/api/clients?q=" + encodeURIComponent(q.value.trim()));
    box.replaceChildren(...(list.length ? list.map((c) => h("button", { class: "ord-item cl-item", onclick: () => clientSheet(c.id) }, avatar(c.name, "sm"),
      h("div", { class: "grow ord-main" }, h("b", {}, c.name), h("div", { class: "muted small" }, c.phone || "без номера")))) : [emptyState("Нікого не знайдено", "search")]));
  };
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 300); });
  await load();
}

function periodPicker(onChange, initial = "month") {
  const from = h("input", { type: "date", "aria-label": "З" });
  const to = h("input", { type: "date", "aria-label": "По" });
  const custom = h("div", { class: "period-range hidden" },
    h("label", { class: "f" }, h("span", {}, "З"), from), h("label", { class: "f" }, h("span", {}, "По"), to),
    h("button", { class: "btn", onclick: () => from.value && onChange(`date_from=${from.value}&date_to=${to.value || from.value}`, "custom") }, "Показати"));
  const s = seg([["day", "День"], ["week", "Тиждень"], ["month", "Місяць"], ["year", "Рік"], ["custom", "Період"]], initial, (v) => {
    custom.classList.toggle("hidden", v !== "custom");
    if (v !== "custom") onChange("period=" + v, v);
  });
  return h("div", { class: "row period-picker", style: { marginBottom: "14px" } }, s, custom);
}

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

const REPORTS = { revenue: "Виручка і знижки", split: "Частки і матеріали", gross: "Валовий прибуток", expenses: "Витрати",
  net: "Чистий прибуток", profitability: "Рентабельність", masters: "Майстри: виплати, борг, бонус", unpaid: "Неоплачені замовлення",
  by_master: "По майстрах", by_service: "По послугах", by_day: "По днях", insights: "Порівняння і прогноз",
  recommendations: "Рекомендації", pie: "Куди йдуть гроші", average_check: "Середній чек" };
const PROFIT_LEVEL = { excellent: ["💚", "відмінно"], good: ["🟢", "добре"], fair: ["🟡", "задовільно"], low: ["🟠", "низько"], critical: ["🔴", "критично"] };
function profitChip(s) {
  if (s.profitability_bp === null || s.profitability_bp === undefined) return h("span", { class: "muted" }, "—");
  const [em, txt] = PROFIT_LEVEL[s.profitability_level] || ["", ""];
  return h("span", { class: "row" }, h("b", { class: "kpi-value" }, `${(s.profitability_bp / 100).toFixed(1).replace(".", ",")}%`), `${em} ${txt}`);
}
async function downloadFile(path, filename) {
  const headers = { Authorization: "Bearer " + api.token };
  const fin = sessionStore.get("mds_fin");
  if (fin) headers["X-Finance-Token"] = fin;
  const r = await fetch(path, { headers });
  if (!r.ok) { const d = await r.json().catch(() => null); return toast((d && d.detail) || "Не вдалося завантажити", true); }
  const url = URL.createObjectURL(await r.blob());
  const a = h("a", { href: url, download: filename });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
function exportSheet(q) {
  openSheet("Експорт звітів", h("div", { class: "stack" },
    ...[["finance", "Фінанси (підсумки)"], ["orders", "Замовлення"]].map(([name, label]) => h("div", { class: "card stack" }, h("b", {}, label),
      h("div", { class: "row" }, ...["pdf", "xlsx", "csv"].map((fmt) => h("button", { class: "btn", onclick: () => downloadFile(`/api/exports/${name}.${fmt}?${q}`, `mds-${name}.${fmt}`) },
        fmt === "xlsx" ? "Excel" : fmt.toUpperCase())))))));
}
function reportsToggleSheet(hidden, onSaved) {
  const boxes = Object.entries(REPORTS).map(([k, label]) => [k, h("input", { type: "checkbox", checked: !hidden.includes(k) }), label]);
  openSheet("Які звіти показувати", h("div", { class: "stack" },
    h("p", { class: "muted small" }, "Приховані звіти не показуються, але дані рахуються далі."),
    boxes.map(([, cb, label]) => h("label", { class: "row" }, cb, label)),
    h("button", { class: "btn primary", onclick: async () => {
      const v = boxes.filter(([, cb]) => !cb.checked).map(([k]) => k);
      state.settings = await run(() => api.put("/api/settings", { reports_hidden: v }), "Збережено");
      closeSheet(); onSaved();
    } }, "Зберегти")));
}

VIEWS.dashboard = async (main) => {
  const body = h("div");
  let lastQ = "period=month";
  main.append(topbar("Фінанси",
    h("button", { class: "btn", onclick: () => exportSheet(lastQ) }, icon("filetext", "sm"), "Експорт"),
    h("button", { class: "btn", onclick: () => reportsToggleSheet(state.settings?.reports_hidden || [], () => load(lastQ)) }, icon("settings", "sm"), "Звіти"),
    h("button", { class: "btn", onclick: () => { location.hash = "#/expenses"; } }, icon("plus", "sm"), "Витрата")),
    periodPicker((q, kind) => load(q, kind)), body);
  const load = async (q, kind = "month") => {
    lastQ = q;
    body.replaceChildren(h("div", { class: "empty" }, "Завантаження…"));
    const d = await api.get("/api/finance/dashboard?" + q);
    const s = d.summary;
    const hidden = new Set(s.hidden_reports || []);
    const show = (k, node) => (hidden.has(k) ? null : node);
    const exp = s.expenses;
    const gd = s.gross_details;
    const line = (label, v, cls = "") => h("div", { class: "sum-line " + cls }, h("span", {}, label), h("b", { class: v < 0 ? "neg" : "" }, money(v)));
    const un = s.unpaid_orders;
    body.replaceChildren(...[
      show("revenue", h("div", { class: "grid kpi" },
        kpi("Реальна виручка", s.real_revenue, "dark", { ico: "banknote", sub: `послуги ${money(s.revenue)} · товари ${money(s.products.revenue)}` }),
        s.discounts >= 0 ? kpi("Знижки (за рахунок салону)", s.discounts, "", { ico: "percent" })
          : kpi("Доплати понад ціну", -s.discounts, "", { ico: "percent", sub: "клієнти заплатили більше за прайс" }))),
      show("split", h("div", { class: "grid kpi", style: { marginTop: "12px" } },
        kpi("Частка майстрів", s.masters_share, "", { ico: "users", sub: "50% від ціни" }),
        kpi("Частка салону (до матеріалів)", s.salon_share, "", { ico: "home" }),
        kpi("Матеріали", s.materials, "", { ico: "box" }))),
      show("gross", h("details", { class: "card", style: { marginTop: "12px" } },
        h("summary", { class: "row", style: { cursor: "pointer", listStyle: "none" } }, h("h3", { class: "grow", style: { margin: 0 } }, "Валовий прибуток"),
          h("b", { class: "kpi-value" }, money(s.gross_profit)), icon("down", "sm")),
        h("div", { class: "stack", style: { marginTop: "8px" } },
          line("Послуги: виручка − майстри − матеріали", gd.services), line("Товари: продаж − собівартість − бонус", gd.products),
          gd.refund_losses ? line("Матеріали на повернених замовленнях", -gd.refund_losses) : null,
          line("Вигода від кращих закупівель", gd.purchase_benefit, "muted"),
          h("b", { class: "small" }, "Матеріали за категоріями"),
          gd.materials_by_category.length ? gd.materials_by_category.map((c) => line(c.category, c.amount, "muted small")) : h("span", { class: "muted small" }, "—")))),
      show("expenses", h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Витрати")),
        line("Оренда", exp.rent), line("Комунальні", exp.utilities), line("Реклама", exp.advertising), line("Податки", exp.taxes),
        line("Генератор", exp.generator), line("Інше", exp.other),
        h("div", { class: "sum-line muted small" }, h("span", {}, "Закупівля матеріалів і пального (лише грошовий потік)"), h("span", {}, money(exp.materials_purchases + exp.fuel_purchases))))),
      show("net", h("div", { class: "card", style: { marginTop: "12px" } }, line("Чистий прибуток", s.net_profit, "total"))),
      show("profitability", h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Рентабельність")), profitChip(s),
        h("div", { class: "muted small" }, "≥25% 💚 · 18–25% 🟢 · 14–18% 🟡 · 8–14% 🟠 · <8% 🔴"))),
      show("masters", h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Майстри")),
        line("Виплачено майстрам за період", s.payouts), line("Борг майстрам зараз", s.owed_to_masters, "total"),
        line("Бонус за товари (накопичено)", s.product_bonus_accrued),
        h("a", { href: "#/shifts", class: "small" }, "Виплати й бонуси →"))),
      show("unpaid", h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Неоплачені замовлення"),
        h("span", { class: "muted small" }, `${un.count} шт. · ${money(un.amount)}`)),
        un.items.length ? un.items.map((x) => h("button", { class: "sum-line link" + (x.overdue ? " neg" : ""), onclick: () => orderSheet(x.id, () => load(lastQ)) },
          h("span", {}, `#${x.id} · ${x.days} дн.`, x.overdue ? " · понад 3 дні" : ""), h("b", {}, money(x.amount)))) : h("span", { class: "muted small" }, "Немає"))),
      show("by_day", d.by_day.length > 1 ? h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Виручка по днях")), revenueChart(d.by_day)) : null),
      show("by_master", h("div", {}, h("div", { class: "section" }, h("h2", {}, "По майстрах")),
        d.by_master.length ? h("div", { class: "card table-wrap scroll-box" }, h("table", {},
          h("tr", {}, h("th", {}, "Майстер"), h("th", { class: "num" }, "Послуг"), h("th", { class: "num" }, "Виручка"), h("th", { class: "num" }, "Нараховано"), h("th", { class: "num m-hide" }, "Виплачено"), h("th", { class: "num" }, "Борг")),
          d.by_master.map((m) => h("tr", {}, h("td", {}, m.master_name), h("td", { class: "num" }, m.orders_count), h("td", { class: "num" }, money(m.revenue)),
            h("td", { class: "num" }, money(m.masters_share)), h("td", { class: "num m-hide" }, money(m.paid)), h("td", { class: "num" }, h("b", {}, money(m.balance)))))))
          : h("div", { class: "card" }, emptyState("Немає даних", "users")))),
      show("by_service", h("div", {}, h("div", { class: "section" }, h("h2", {}, "По послугах")),
        d.by_service.length ? h("div", { class: "card table-wrap scroll-box" }, h("table", {},
          h("tr", {}, h("th", {}, "Послуга"), h("th", { class: "num" }, "К-сть"), h("th", { class: "num" }, "Виручка"), h("th", { class: "num m-hide" }, "Майстрам"), h("th", { class: "num m-hide" }, "Матеріали"), h("th", { class: "num" }, "Салону")),
          d.by_service.map((x) => h("tr", {}, h("td", {}, x.service_name), h("td", { class: "num" }, x.orders_count), h("td", { class: "num" }, money(x.revenue)),
            h("td", { class: "num m-hide" }, money(x.masters_share)), h("td", { class: "num m-hide" }, money(x.materials)), h("td", { class: "num" }, money(x.salon_net_from_orders))))))
          : h("div", { class: "card" }, emptyState("Немає замовлень за період", "receipt")))),
    ].filter(Boolean));
  };
  await load("period=month", "month");
};

const SVGNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, String(v));
  kids.flat().forEach((c) => c && el.append(c));
  return el;
}
function pieChart(parts) {
  const total = parts.reduce((a, [, v]) => a + Math.max(0, v), 0);
  if (!total) return h("div", { class: "muted small" }, "Немає даних");
  let ang = -Math.PI / 2;
  const slices = parts.filter(([, v]) => v > 0).map(([label, v, color]) => {
    const a2 = ang + (v / total) * Math.PI * 2;
    const large = a2 - ang > Math.PI ? 1 : 0;
    const p = (a) => `${50 + 45 * Math.cos(a)} ${50 + 45 * Math.sin(a)}`;
    const d = v === total ? "M 50 5 A 45 45 0 1 1 49.99 5 Z" : `M 50 50 L ${p(ang)} A 45 45 0 ${large} 1 ${p(a2)} Z`;
    ang = a2;
    return [svg("path", { d, fill: color }), label, v, color];
  });
  return h("div", { class: "row", style: { gap: "16px", flexWrap: "wrap" } },
    (() => { const el = svg("svg", { viewBox: "0 0 100 100", width: 150, height: 150, role: "img", "aria-label": "Куди йдуть гроші" }, slices.map((x) => x[0])); return el; })(),
    h("div", { class: "stack small" }, slices.map(([, l, v, c]) => h("div", { class: "row" }, h("span", { style: { width: "10px", height: "10px", borderRadius: "50%", background: c, display: "inline-block" } }),
      `${l}: ${money(v)} (${Math.round((v / total) * 100)}%)`))));
}

let mermaidLoading = null;
function loadMermaid() {
  if (window.mermaid) return Promise.resolve(window.mermaid);
  if (mermaidLoading) return mermaidLoading;
  mermaidLoading = new Promise((resolve, reject) => {
    const sc = document.createElement("script");
    sc.src = "/static/vendor/mermaid.min.js?v=11.4.1";
    sc.onload = () => resolve(window.mermaid);
    sc.onerror = () => { mermaidLoading = null; reject(new Error("Не вдалося завантажити схеми")); };
    document.head.append(sc);
  });
  return mermaidLoading;
}
const uah = (k) => (Math.round((k || 0) / 100)).toLocaleString("uk-UA").replace(/\u00a0/g, " ") + " ₴";
function logicDiagrams(st) {
  const finance = `flowchart TD
    P["Ціна (середня)<br/>P = ${uah(st.avg_price)}"] --> M1["Майстер = P × 50%<br/>${uah(st.avg_master)}"]
    A["Сплачено (середнє)<br/>A = ${uah(st.avg_paid)}"] --> S1["Салон = A − Майстер − M<br/>${uah(st.avg_salon)}"]
    M["Матеріали (середні)<br/>M = ${uah(st.avg_materials)}"] --> S1
    M1 --> S1
    S1 --> CHK{"Салон < 0?"}
    CHK -->|Так| ALERT["🔔 Сповіщення обом директорам"]
    CHK -->|Ні| OK["✅ ОК"]`;
  const lifecycle = `flowchart LR
    A["Клієнт"] --> B["Запис<br/>(pending)"]
    B --> C["confirmed"]
    C --> D["in_progress"]
    D --> E["Замовлення<br/>(completed / unpaid)"]
    E --> F["Зміна закрита"]
    F --> G["Виплата"]
    B -.-> X["cancelled / no_show"]
    E -.-> R["refunded / cancelled"]`;
  const cash = `flowchart TD
    S["Відкрита зміна"] --> O["Замовлення"]
    O --> C{"Готівка?"}
    C -->|Так| CASH["Готівка в касу"]
    C -->|Ні| CARD["На картку салону"]
    CASH --> CLOSE["Закриття зміни"]
    CARD --> CLOSE
    CLOSE --> CALC["Майстер бере: min(Заробіток, Готівка)"]
    CALC --> PAYOUT["Доплата на картку: Заробіток − взяте"]`;
  return [["Фінансовий поділ (середнє за місяць)", finance], ["Життєвий цикл замовлення", lifecycle], ["Зміна і каса", cash]];
}

VIEWS.logic = async (main) => {
  main.append(topbar("Логіка системи", "Як працюють гроші, замовлення і каса — на реальних середніх цифрах"));
  const [st, mermaid] = await Promise.all([api.get("/api/finance/logic-stats"), loadMermaid()]);
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "neutral", flowchart: { htmlLabels: true } });
  main.append(h("p", { class: "muted small" }, st.orders ? `Місяць з ${dmy(st.month)}: ${st.orders} замовлень. ` +
    (st.avg_discount >= 0 ? `Знижка в середньому ${uah(st.avg_discount)}.` : `Клієнти в середньому платили на ${uah(-st.avg_discount)} більше за ціну.`)
    : "У цьому місяці ще немає закритих замовлень — цифри дорівнюють нулю."));
  let i = 0;
  for (const [title, code] of logicDiagrams(st)) {
    const holder = h("div", { class: "mermaid-box", style: { overflowX: "auto" } });
    main.append(h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, title)), holder));
    try {
      const { svg } = await mermaid.render(`logic-${Date.now()}-${i++}`, code);
      const doc = new DOMParser().parseFromString(svg, "text/html");
      const el = doc.body.querySelector("svg");
      if (!el) throw new Error("порожня схема");
      holder.append(document.importNode(el, true));
    } catch (e) {
      holder.append(h("div", { class: "muted small" }, "Схему не вдалося побудувати: " + (e.message || e)));
    }
  }
};

VIEWS.analytics = async (main) => {
  const body = h("div");
  let q = "period=month";
  main.append(topbar("Аналітика"), periodPicker((x) => { q = x; load(); }), body);
  const recBox = h("div", { class: "stack" });
  const loadRec = async (refresh) => {
    recBox.replaceChildren(h("div", { class: "muted small" }, "Готуємо рекомендації…"));
    const r = await api.post(`/api/analytics/recommend?${q}${refresh ? "&refresh=true" : ""}`).catch((e) => ({ items: [{ title: "Немає рекомендацій", text: e.message, priority: "low" }] }));
    const pr = { high: "🔴", medium: "🟡", low: "🟢" };
    recBox.replaceChildren(...r.items.map((x) => h("div", { class: "rec-item", style: { whiteSpace: "normal", overflowWrap: "anywhere", padding: "6px 0", borderBottom: "1px solid var(--line)" } },
      `${pr[x.priority] || ""} `, h("b", {}, x.title), " — ", x.text)),
      h("div", { class: "muted small" }, r.provider === "llama" ? "Локальна модель" : "Правила", r.cached ? " · з кешу (1 год)" : ""));
  };
  const load = async () => {
    body.replaceChildren(h("div", { class: "empty" }, "Завантаження…"));
    const [d, ins] = await Promise.all([api.get("/api/finance/dashboard?" + q), api.get("/api/finance/insights?" + q).catch(() => null)]);
    const s = d.summary;
    const hidden = new Set(s.hidden_reports || []);
    const show = (k, node) => (hidden.has(k) ? null : node);
    const exp = s.expenses;
    const opEx = exp.rent + exp.utilities + exp.advertising + exp.taxes + exp.generator + exp.other;
    body.replaceChildren(...[
      show("profitability", h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Рентабельність")), profitChip(s))),
      show("recommendations", h("div", { class: "card", style: { marginTop: "12px" } },
        h("div", { class: "card-head" }, h("h3", {}, "Рекомендації"), h("button", { class: "btn sm", onclick: () => loadRec(true) }, "Оновити")), recBox)),
      show("pie", h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Куди йдуть гроші")),
        pieChart([["Майстрам", s.masters_share, "#8a6f3c"], ["Матеріали", s.materials, "#c9a46a"], ["Витрати", opEx, "#9aa59e"],
          ["Прибуток", Math.max(0, s.net_profit), "#2f6b4f"]]))),
      show("expenses", h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Витрати")),
        [["Оренда", exp.rent], ["Комунальні", exp.utilities], ["Реклама", exp.advertising], ["Податки", exp.taxes], ["Генератор", exp.generator], ["Інше", exp.other]]
          .map(([l, v]) => h("div", { class: "sum-line" }, h("span", {}, l), h("b", {}, money(v)))))),
      show("insights", ins ? insightsBlock(ins) : null),
      show("average_check", h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Середній чек"), h("b", {}, money(s.average_check))),
        d.by_service.map((x) => h("div", { class: "sum-line small" }, h("span", {}, x.service_name, h("span", { class: "muted" }, ` · ${x.orders_count}×`)),
          h("span", {}, money(x.orders_count ? Math.floor(x.revenue / x.orders_count) : 0)))))),
    ].filter(Boolean));
    if (!hidden.has("recommendations")) loadRec(false);
  };
  await load();
};

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
    h("div", { class: "card stack", style: { boxShadow: "none" } },
      h("div", { class: "sum-line muted small" }, h("span", {}, "Закупівля матеріалів (гроші, не витрата періоду)"), h("span", {}, money(exp.materials_purchases))),
      h("div", { class: "sum-line muted small" }, h("span", {}, "Закупівля пального"), h("span", {}, money(exp.fuel_purchases || 0))),
      h("div", { class: "sum-line muted small" }, h("span", {}, "Матеріали, списані в замовленнях"), h("span", {}, money(s.materials)))),
    h("div", { class: "row" }, h("a", { class: "btn", href: "#/expenses" }, icon("wallet", "sm"), "Витрати"),
      h("a", { class: "btn", href: "#/utilities" }, icon("gauge", "sm"), "Комунальні платежі"))));
}
function profitSheet({ s, p, vs, opEx, exp }) {
  const parts = [["Майстрам", s.masters_share, "c-masters"], ["Матеріали", s.materials, "c-mat"], ["Витрати", Math.max(0, opEx), "c-exp"],
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
    h("div", { class: "sum-line" }, h("span", { class: "muted" }, "Витрати салону"), h("span", {}, "− " + money(f.operating_expenses))),
    h("div", { class: "sum-line total" }, h("span", {}, "Прогноз чистого прибутку"), h("span", { class: f.forecast_net < 0 ? "neg" : "" }, money(f.forecast_net))),
    h("p", { class: "fc-note" }, "Прогноз — більше з двох: «за темпом» або «факт + записи наперед». Прибуток — за структурою цього місяця (частки майстрів і матеріали)."));

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
    h("div", { class: "grid two swipe-deck" }, forecastCard, checkCard, clientsCard, weekCard));
}

const MONTHS_UA = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень", "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"];
const monthTitle = (iso) => `${MONTHS_UA[+iso.slice(5, 7) - 1]} ${iso.slice(0, 4)}`;
const addMonths = (iso, n) => { const d = new Date(iso.slice(0, 7) + "-01T12:00"); d.setMonth(d.getMonth() + n); return isoDate(d).slice(0, 7) + "-01"; };
const fmtH = (h) => { const m = Math.round(h * 60); return `${Math.floor(m / 60)} год ${m % 60}\u00a0хв`; };

const num = (v) => parseFloat(String(v).replace(",", "."));
function hourCostText(st) {
  return st.hour_cost ? `${money(st.hour_cost)} за мотогодину = ${fmtNum(st.fuel_lph_milli / 1000)}\u00a0л × ${money(st.fuel_price)} + амортизація ${money(st.amort_per_hour)}` : "вартість мотогодини не задано";
}
function generatorHoursForm(g, onDone) {
  let mode = "meter";
  const val = h("input", { inputmode: "decimal", placeholder: "напр. 125,5" });
  const on = h("input", { type: "date", value: today(), max: today() });
  const note = h("input", { placeholder: "необов'язково" });
  const lbl = h("span", {}, "Показник лічильника");
  const hint = h("p", { class: "muted small" });
  const preview = h("div", { class: "card gen-preview" });
  const st = g.settings;
  const calc = () => {
    const x = num(val.value);
    const hrs = mode === "meter" ? x - g.motohours : x;
    hint.textContent = mode === "meter" ? `Попередній показник: ${fmtNum(g.motohours)}\u00a0мотогод. Різниця = відпрацьовано.` : "Скільки мотогодин генератор пропрацював (можна дробове: 2,5).";
    if (!(hrs > 0)) { preview.replaceChildren(h("span", { class: "muted small" }, Number.isFinite(x) && mode === "meter" ? "Показник має бути більший за попередній" : "Вкажіть значення — тут з'явиться розрахунок")); return; }
    const fuel = hrs * st.fuel_lph_milli / 1000;
    const fuelCost = Math.round(fuel * st.fuel_price), amort = Math.round(hrs * st.amort_per_hour);
    preview.replaceChildren(
      h("div", { class: "sum-line" }, h("span", {}, "Відпрацьовано"), h("b", {}, `${fmtNum(Math.round(hrs * 1000) / 1000)}\u00a0мотогод.`)),
      h("div", { class: "sum-line" }, h("span", {}, `Бензин ${fmtNum(Math.round(fuel * 100) / 100)}\u00a0л × ${money(st.fuel_price)}`), h("span", {}, money(fuelCost))),
      h("div", { class: "sum-line" }, h("span", {}, `Амортизація ${money(st.amort_per_hour)} × мотогод.`), h("span", {}, money(amort))),
      h("div", { class: "sum-line total" }, h("span", {}, "Разом витрати"), h("b", {}, money(fuelCost + amort))));
  };
  val.addEventListener("input", calc);
  const modeSeg = seg([["meter", "Показник лічильника"], ["hours", "Відпрацьовано годин"]], mode, (v) => { mode = v; lbl.textContent = v === "meter" ? "Показник лічильника" : "Відпрацьовано, мотогод."; val.value = ""; calc(); });
  calc();
  openSheet("Внести мотогодини", h("div", { class: "stack" },
    modeSeg,
    h("div", { class: "ap-grid ap-grid-2" }, h("label", { class: "f" }, lbl, val), field("Дата", on)),
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
        h("div", { class: "kpi-value" }, `${fmtNum(g.motohours)}\u00a0мотогод.`),
        h("div", { class: "muted small" }, hourCostText(g.settings))),
      h("button", { class: "btn primary", onclick: () => generatorHoursForm(g, onChange) }, icon("plus", "sm"), "Внести мотогодини")),
    !g.configured ? h("div", { class: "callout", style: { marginTop: "10px", marginBottom: 0 } }, icon("alert"),
      h("div", { class: "small" }, "Задайте витрату бензину, ціну й амортизацію в ", h("a", { href: "#/settings" }, "«Налаштуваннях»"), " — інакше вартість буде 0 ₴.")) : null);
}
function genHistoryChart(hist) {
  const max = Math.max(1, ...hist.map((x) => x.total));
  return h("div", { class: "gen-bars" }, hist.map((x) => h("div", { class: "gen-bar", title: `${monthTitle(x.month)}: ${fmtNum(x.hours)}\u00a0мотогод., ${fmtNum(x.fuel_l)}\u00a0л, ${money(x.total)}` },
    h("span", { class: "v" }, x.total ? money(x.total) : "—"),
    h("div", { class: "col" }, h("i", { class: "fuel", style: { height: (x.fuel_cost / max) * 100 + "%" } }), h("i", { class: "amort", style: { height: (x.amort_cost / max) * 100 + "%" } })),
    h("b", {}, MONTHS_UA[+x.month.slice(5, 7) - 1].slice(0, 3)), h("span", { class: "muted" }, `${fmtNum(x.hours)} год`))));
}
function genRunsTable(runs, onEdit) {
  return runs.length ? h("div", { class: "table-wrap" }, h("table", {},
    h("tr", {}, h("th", {}, "Дата"), h("th", { class: "num" }, "Мотогод."), h("th", { class: "num" }, "Бензин"), h("th", { class: "num m-hide" }, "Амортизація"), h("th", { class: "num" }, "Разом"), h("th", { class: "m-hide" }, "Примітка"), onEdit ? h("th", {}, "") : null),
    runs.map((x) => h("tr", {}, h("td", {}, dmy(x.started_at), x.corrected ? h("span", { class: "chip plain", style: { marginLeft: "6px" } }, "виправлено") : null),
      h("td", { class: "num" }, fmtNum(x.hours)), h("td", { class: "num" }, `${fmtNum(x.fuel_l)}\u00a0л · ${money(x.fuel_cost)}`), h("td", { class: "num" }, money(x.amort_cost)),
      h("td", { class: "num" }, h("b", {}, money(x.total_cost))), h("td", { class: "small muted" }, x.note || ""),
      onEdit ? h("td", { class: "right" }, x.running ? null : h("button", { class: "btn sm ghost", "aria-label": "Виправити", onclick: () => onEdit(x) }, icon("edit", "sm"))) : null))))
    : emptyState("Цього місяця генератор не використовувався", "gauge");
}
function antispamSettingsCard(st) {
  const num = (k, d) => h("input", { type: "number", min: 0, max: 1000, value: st[k] ?? d });
  const f = { antispam_max_active: num("antispam_max_active", 2), antispam_max_per_day: num("antispam_max_per_day", 3),
    antispam_max_pending_total: num("antispam_max_pending_total", 20),
    antispam_callbacks_per_day: num("antispam_callbacks_per_day", 2) };
  const wl = h("input", { value: (st.antispam_whitelist || []).join(", "), placeholder: "+380687166910" });
  const smsOn = h("input", { type: "checkbox", checked: !!st.sms_enabled });
  const smsNote = h("p", { class: "muted small", style: { margin: 0 } }, "Перевіряю підключення TurboSMS…");
  api.get("/api/sms/status").then((r) => { smsNote.textContent = r.configured ? "✓ TurboSMS підключено" : "TurboSMS не підключено: зареєструйтесь на turbosms.ua (Viber + SMS), задайте на сервері MDS_TURBOSMS_TOKEN і MDS_TURBOSMS_SENDER. Поки — клієнтам без Telegram програма покаже, кому подзвонити."; }).catch(() => { smsNote.textContent = ""; });
  return h("div", { class: "card stack" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("lock"), "Захист онлайн-запису"),
    h("p", { class: "muted small", style: { margin: 0 } }, "Обмеження для записів з Telegram-бота (персонал записує без обмежень). 0 — без обмеження. Окремого клієнта можна заблокувати в його картці."),
    h("div", { class: "form-grid" }, field("Активних записів на клієнта", f.antispam_max_active), field("Нових записів від клієнта за добу", f.antispam_max_per_day),
      field("Непідтверджених у черзі (усього)", f.antispam_max_pending_total),
      field("Заявок «передзвоніть» з номера за добу", f.antispam_callbacks_per_day)),
    field("Номери без обмежень (власниця, персонал) — через кому", wl),
    h("label", { class: "row" }, smsOn, "SMS / Viber клієнтам (напр. «запис зсунуто на 15 хв»)"), smsNote,
    h("button", { class: "btn primary", onclick: async () => {
      const body = { sms_enabled: smsOn.checked,
        antispam_whitelist: wl.value.split(/[,;\n]/).map((x) => x.trim()).filter(Boolean) };
      for (const [k, el] of Object.entries(f)) body[k] = Math.max(0, parseInt(el.value || "0", 10));
      state.settings = await run(() => api.put("/api/settings", body), "Збережено"); reload();
    } }, "Зберегти"));
}
function stockSettingsCard(st) {
  const v = st.stock_auto_writeoff_milli ?? 100;
  const opts = [[0, "вимкнено"], [50, "менше 0,05 упаковки"], [100, "менше 0,1 упаковки (напр. 0,09 туби)"], [200, "менше 0,2 упаковки"]];
  if (!opts.some(([x]) => x === v)) opts.push([v, `менше ${String(v / 1000).replace(".", ",")} упаковки`]);
  const sel = select(opts, v);
  return h("div", { class: "card stack" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("box"), "Склад"),
    field("Автоматично списувати залишок", sel),
    h("p", { class: "muted small", style: { margin: 0 } }, "Коли після замовлення чи списання лишається крихта (напр. 6 г з туби 60 г), вона списується сама — у «Русі складу» з приміткою «Автоматичне списання залишку». Якщо замовлення виправляють — повертається."),
    h("button", { class: "btn primary", onclick: async () => {
      state.settings = await run(() => api.put("/api/settings", { stock_auto_writeoff_milli: Number(sel.value) }), "Збережено"); reload();
    } }, "Зберегти"));
}
const RUM_NAMES = { "nav:ttfb": "Відповідь сервера (перший байт)", "nav:fcp": "Перший малюнок на екрані",
  "nav:dom": "Сторінка розібрана", "nav:load": "Повне завантаження" };
function perfCard() {
  const box = h("div", { class: "stack" });
  const ms = (v) => (v >= 1000 ? (v / 1000).toFixed(1).replace(".", ",") + " с" : v + " мс");
  const table = (rows, _label, slowMs) => rows.length
    ? h("div", { class: "perf-list" }, rows.map((r) => h("div", { class: "perf-row" },
      h("span", { class: "perf-name" }, r.label),
      h("b", { class: "num" + (r.p95 >= slowMs ? " neg" : "") }, ms(r.p95)),
      h("span", { class: "perf-meta num muted small" }, `${r.count} ${plural(r.count, "раз", "рази", "разів")} · звичайно ${ms(r.p50)}` + (r.slow ? ` · повільних ${r.slow}` : "")))))
    : h("p", { class: "muted small" }, "Ще немає вимірів.");
  const load = async (btn) => {
    const d = await run(() => api.get("/api/perf"));
    if (!d) return;
    const viewName = (id) => ((NAV[state.user.role] || []).find((i) => i[0] === id) || [])[2] || id;
    const client = d.client.map((r) => ({ ...r, label: r.name.startsWith("view:") ? "Сторінка «" + viewName(r.name.slice(5)) + "»" : (RUM_NAMES[r.name] || r.name) }));
    const server = d.server.slice(0, 12).map((r) => ({ ...r, label: r.name }));
    box.replaceChildren(
      h("p", { class: "muted small", style: { margin: 0 } }, `З ${dmy(d.since)} ${hm(d.since)} (після кожного оновлення програми рахується заново). Жирне число — 95% випадків швидші за нього; червоне — повільно.`),
      h("h4", { style: { margin: "4px 0 0" } }, "У браузерах (телефони й комп'ютери)"), table(client, "Що", d.slow_client_ms),
      h("h4", { style: { margin: "4px 0 0" } }, "Сервер (найповільніші запити)"), table(server, "Запит", d.slow_api_ms));
    if (btn) btn.textContent = "Оновити";
  };
  const btn = h("button", { class: "btn", onclick: (e) => load(e.currentTarget) }, "Показати");
  return h("div", { class: "card stack" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("zap"), "Швидкодія"),
    h("p", { class: "muted small", style: { margin: 0 } }, "Як швидко відкриваються сторінки в працівників і відповідає сервер. Без персональних даних — лише час."),
    box, btn);
}
function vacationCard() {
  const box = h("div", { class: "card stack" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("calendar"), "Режим «Ми у відпустці»"));
  const draw = (v) => {
    const days = h("input", { type: "number", min: 1, max: 14, value: 14, style: { width: "80px" } });
    box.replaceChildren(box.firstChild,
      h("p", { class: "muted small", style: { margin: 0 } }, "Майстри можуть запитати дозвіл на 7 дій (скасування, повернення, виправлення, примусове закриття зміни, матеріали після закриття, інвентаризація, прихід). Вирішує перший із директорів. Автовимкнення — максимум 14 днів."),
      v.active ? h("div", { class: "sum-line" }, h("b", {}, "Увімкнено"), h("span", {}, "до ", dmy(v.expires_at), " ", hm(v.expires_at))) : h("span", { class: "muted" }, "Вимкнено"),
      v.active ? h("button", { class: "btn", onclick: async () => draw(await run(() => api.post("/api/settings/vacation-mode", { active: false }), "Вимкнено")) }, "Вимкнути")
        : h("div", { class: "row" }, field("Днів", days), h("button", { class: "btn primary", onclick: async () =>
          draw(await run(() => api.post("/api/settings/vacation-mode", { active: true, days: parseInt(days.value, 10) || 14 }), "Увімкнено")) }, "Увімкнути")),
      h("a", { href: "#/security", class: "small" }, "Запити дозволу →"));
  };
  api.get("/api/settings/vacation-mode").then(draw).catch(() => {});
  return box;
}
function alertsSettingsCard() {
  const box = h("div", { class: "card stack" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("bell"), "Сповіщення"));
  api.get("/api/alerts/settings").then((cfg) => {
    const boxes = cfg.important.map((k) => [k, h("input", { type: "checkbox", checked: !cfg.disabled.includes(k) })]);
    box.append(h("p", { class: "muted small", style: { margin: 0 } }, "Критичні (салон у мінусі, від'ємний баланс майстра, запит дозволу) вимкнути не можна. Інформаційні приходять щоденним дайджестом."),
      ...boxes.map(([k, cb]) => h("label", { class: "row" }, cb, cfg.labels[k] || k)),
      h("button", { class: "btn primary", onclick: async () => {
        await run(() => api.put("/api/alerts/settings", { disabled: boxes.filter(([, cb]) => !cb.checked).map(([k]) => k) }), "Збережено");
      } }, "Зберегти"));
  }).catch(() => {});
  return box;
}

VIEWS.security = async (main) => {
  const isDir = state.user.role === "director";
  main.append(topbar("Безпека", isDir ? "Входи, сесії, PIN фінансів, запити дозволу" : "Мої запити дозволу"));
  const reqBox = h("div", { class: "card stack" }, h("h3", {}, "Запити дозволу"));
  const drawReq = async () => {
    const list = await api.get("/api/permission-requests").catch(() => []);
    const ST = { pending: "очікує", approved: "дозволено", rejected: "відхилено", withdrawn: "відкликано", expired: "прострочено" };
    reqBox.replaceChildren(reqBox.firstChild, ...(list.length ? list.map((r) => h("div", { class: "sum-line" },
      h("span", {}, `#${r.id} · ${r.user_name || ""} · ${r.action_label} · `, h("b", {}, ST[r.status] || r.status)),
      r.status === "pending" ? h("span", { class: "row" }, ...(isDir
        ? [h("button", { class: "btn sm primary", onclick: async () => { await run(() => api.post(`/api/permission-requests/${r.id}/approve`, {}), "Дозволено"); drawReq(); } }, "Дозволити"),
           h("button", { class: "btn sm", onclick: async () => { await run(() => api.post(`/api/permission-requests/${r.id}/reject`, {}), "Відхилено"); drawReq(); } }, "Відхилити")]
        : [h("button", { class: "btn sm", onclick: async () => { await run(() => api.post(`/api/permission-requests/${r.id}/withdraw`), "Відкликано"); drawReq(); } }, "Відкликати")])) : null))
      : [h("span", { class: "muted" }, "Запитів немає")]));
  };
  if (!isDir) {
    main.append(permissionRequestCard(drawReq), h("div", { style: { marginTop: "12px" } }, reqBox));
    return drawReq();
  }
  const s = state.settings || {};
  const pinOn = h("input", { type: "checkbox", checked: !!s.finance_pin_enabled });
  const newPin = h("input", { type: "password", inputmode: "numeric", maxlength: 8, placeholder: "новий PIN" });
  const curPin = h("input", { type: "password", inputmode: "numeric", maxlength: 8, placeholder: "поточний (якщо вже задано)" });
  const dirs = (state.allUsers || []).filter((u) => u.role === "director" && u.id !== state.user.id);
  const users = (state.allUsers || []).filter((u) => u.is_active !== false);
  const pinCard = h("div", { class: "card stack" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("lock"), "PIN вкладки «Фінанси»"),
    h("label", { class: "row" }, pinOn, "Запитувати PIN раз за сесію (для директорів)"),
    h("button", { class: "btn", onclick: async () => { state.settings = await run(() => api.put("/api/settings", { finance_pin_enabled: pinOn.checked }), "Збережено"); } }, "Зберегти"),
    h("div", { class: "form-grid" }, field("Мій новий PIN", newPin), field("Поточний PIN", curPin)),
    h("button", { class: "btn", onclick: async () => { await run(() => api.post("/api/auth/finance-pin", { pin: newPin.value, current_pin: curPin.value || null }), "PIN змінено"); newPin.value = curPin.value = ""; } }, "Змінити мій PIN"),
    dirs.map((u) => h("button", { class: "btn ghost", onclick: () => confirmSheet("Скинути PIN?", `${u.full_name} задасть новий PIN при наступному відкритті фінансів.`, "Скинути",
      async () => { await run(() => api.post(`/api/users/${u.id}/reset-finance-pin`), "PIN скинуто"); }) }, `Скинути PIN: ${u.full_name}`)));
  const sessCard = h("div", { class: "card stack" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("logout"), "Сесії"),
    h("p", { class: "muted small", style: { margin: 0 } }, "Завершити всі входи людини на всіх пристроях (наприклад, якщо загубили телефон)."),
    users.map((u) => h("div", { class: "sum-line" }, h("span", {}, u.full_name, h("span", { class: "muted small" }, ` · ${ROLES[u.role] || u.role}`)),
      h("button", { class: "btn sm", onclick: () => confirmSheet("Завершити всі сесії?", u.full_name, "Завершити", async () => {
        await run(() => api.post(`/api/users/${u.id}/revoke-sessions`), "Сесії завершено");
        if (u.id === state.user.id) logout();
      }) }, "Завершити сесії"))));
  const logBox = h("div", { class: "card table-wrap scroll-box" });
  const onlyFail = h("input", { type: "checkbox" });
  const loadLog = async () => {
    const rows = await api.get("/api/security/logins" + (onlyFail.checked ? "?success=false" : ""));
    const M = { password: "пароль", "2fa": "код 2FA", bot_pin: "PIN бота" };
    logBox.replaceChildren(h("table", {}, h("tr", {}, h("th", {}, "Час"), h("th", {}, "Хто"), h("th", {}, "Спосіб"), h("th", {}, "Результат"), h("th", { class: "m-hide" }, "IP / пристрій")),
      rows.map((r) => h("tr", {}, h("td", {}, dm(r.ts), " ", hm(r.ts)), h("td", {}, r.user_name || "—"), h("td", {}, M[r.method] || r.method),
        h("td", { class: r.success ? "" : "neg" }, r.success ? "успішно" : (r.detail || "невдало")), h("td", { class: "muted small m-hide" }, [r.ip, (r.user_agent || "").slice(0, 40)].filter(Boolean).join(" · "))))));
  };
  onlyFail.addEventListener("change", loadLog);
  main.append(h("div", { class: "grid two" }, pinCard, sessCard),
    h("div", { style: { marginTop: "12px" } }, reqBox),
    h("div", { class: "section" }, h("h2", {}, "Журнал входів (90 днів)"), h("label", { class: "row small" }, onlyFail, "лише невдалі")), logBox);
  await Promise.all([loadLog(), drawReq()]);
};

function permissionRequestCard(onDone) {
  const card = h("div", { class: "card stack" }, h("h3", {}, "Запитати дозвіл директора"));
  api.get("/api/settings/vacation-mode").then((v) => {
    if (!v.active) { card.append(h("p", { class: "muted" }, "Запит дозволу доступний, коли директори вмикають режим «Ми у відпустці».")); return; }
    const action = select(Object.entries(v.actions), "refund");
    const orderId = h("input", { inputmode: "numeric", placeholder: "№ замовлення" });
    const reason = h("input", { placeholder: "Причина" });
    const extra = h("input", { placeholder: "m:<id матеріалу>=<к-сть>, p:<id товару>=<к-сть> або сума=450" });
    card.append(field("Дія", action), h("div", { class: "form-grid" }, field("Замовлення", orderId), field("Причина", reason)), field("Деталі", extra),
      h("button", { class: "btn primary", onclick: async () => {
        const a = action.value;
        const payload = { order_id: parseInt(orderId.value, 10) || undefined, reason: reason.value.trim() || undefined };
        const m = /^(m|p):(\d+)=([\d.,]+)$/.exec(extra.value.trim());
        if (a === "inventory" && m) payload.items = [{ [m[1] === "m" ? "material_id" : "product_id"]: +m[2], actual: parseFloat(m[3].replace(",", ".")) }];
        if (a === "material_receipt" && m) { payload[m[1] === "m" ? "material_id" : "product_id"] = +m[2]; payload.qty = parseFloat(m[3].replace(",", ".")); }
        const amt = /сума\s*=\s*([\d.,]+)/i.exec(extra.value);
        if (a === "correction") { if (!amt) return toast("Для виправлення вкажіть у деталях: сума=450", true); payload.data = { amount: toKop(amt[1]) }; }
        if (a === "materials_after_close") { if (!m || m[1] !== "m") return toast("Вкажіть матеріал: m:<id>=<кількість>", true); payload.materials = [{ material_id: +m[2], qty: parseFloat(m[3].replace(",", ".")) }]; }
        await run(() => api.post("/api/permission-requests", { action: a, payload }), "Запит надіслано директорам");
        onDone && onDone();
      } }, "Надіслати запит"));
  }).catch(() => {});
  return card;
}

function twofaSettingsCard(st) {
  const roles = st.twofa_roles || [];
  const dir = h("input", { type: "checkbox", checked: roles.includes("director") });
  return h("div", { class: "card stack" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("lock"), "Безпека входу"),
    h("p", { class: "muted small", style: { margin: 0 } }, "Після правильного пароля на новому пристрої — 6-значний код у Telegram (дійсний 5 хв). Пристрій можна запам'ятати на 30 днів. Працює, якщо в людини підключено Telegram."),
    h("label", { class: "row" }, dir, "Директори"),
    h("button", { class: "btn primary", onclick: async () => {
      const v = dir.checked ? ["director"] : [];
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
  main.append(topbar("Генератор", "Внесіть мотогодини, якщо генератор працював — витрати порахуються самі"));
  const month = today().slice(0, 7) + "-01";
  const [g, runs] = await Promise.all([api.get("/api/generator"), api.get("/api/generator/runs?month=" + month)]);
  main.append(generatorCard(g, reload), h("div", { class: "card", style: { marginTop: "12px" } }, h("div", { class: "card-head" }, h("h3", {}, "Записи за " + monthTitle(month).toLowerCase())), genRunsTable(runs, null)));
};

VIEWS.utilities = async (main) => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const month = params.get("m") || today().slice(0, 7) + "-01";
  const go = (m) => { location.hash = "#/utilities?m=" + m; };
  main.append(topbar("Комунальні платежі", monthTitle(month),
    h("button", { class: "btn", "aria-label": "Попередній місяць", onclick: () => go(addMonths(month, -1)) }, icon("left", "sm")),
    h("button", { class: "btn", onclick: () => go(today().slice(0, 7) + "-01") }, "Цей місяць"),
    h("button", { class: "btn", "aria-label": "Наступний місяць", onclick: () => go(addMonths(month, 1)) }, icon("right", "sm"))));
  const [g, sum, readings, runs, hist] = await Promise.all([
    api.get("/api/generator"), api.get("/api/utilities/summary?month=" + month), api.get("/api/utilities/readings"),
    api.get("/api/generator/runs?month=" + month), api.get("/api/generator/history?months=6").catch(() => [])]);
  const st = state.settings || {};

  const e = sum.electricity, gen = sum.generator;
  const kpis = h("div", { class: "grid kpi", style: { marginTop: "12px" } },
    kpi("Електроенергія", e.cost, "", { ico: "zap", sub: `${fmtNum(e.kwh)}\u00a0кВт·год${e.has_reading && !e.confirmed ? " · не підтверджено" : ""}` }),
    kpi("Генератор", gen.total, "", { ico: "gauge", sub: `${fmtNum(gen.hours)}\u00a0мотогод. · ${fmtNum(gen.fuel_l)}\u00a0л` }),
    kpi("Разом витрати", sum.total, "dark", { ico: "wallet", sub: sum.charged_to_masters ? `з майстрів ${money(sum.charged_to_masters)}` : "" }),
    kpi("Вплив на чистий прибуток", sum.net_impact, "accent", { ico: "trend", sub: "витрати мінус компенсація майстрів" }));
  const detail = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Деталі місяця")),
    h("div", { class: "sum-line" }, h("span", {}, "Електроенергія"), h("span", {}, `${fmtNum(e.kwh)}\u00a0кВт·год × ${e.tariff ? money(e.tariff) : "—"}`), h("b", {}, money(e.cost))),
    e.paid !== null ? h("div", { class: "sum-line muted" }, h("span", {}, "Фактично сплачено"), h("span", {}, money(e.paid))) : null,
    h("div", { class: "sum-line" }, h("span", {}, "Бензин"), h("span", {}, fmtNum(gen.fuel_l) + "\u00a0л"), h("b", {}, money(gen.fuel_cost))),
    h("div", { class: "sum-line" }, h("span", {}, "Амортизація генератора"), h("span", {}, fmtNum(gen.hours) + "\u00a0мотогод."), h("b", {}, money(gen.amort_cost))),
    h("div", { class: "sum-line total" }, h("span", {}, "Разом"), h("b", {}, money(sum.total))),
    sum.by_master.map((m) => h("div", { class: "sum-line" }, h("span", { class: "muted" }, "− списано: " + m.master_name), h("span", {}, money(m.amount)))),
    sum.other_utilities ? h("p", { class: "muted small" }, `Інші комунальні витрати (внесені вручну): ${money(sum.other_utilities)}`) : null,
    null);

  const cur = readings.find((r) => r.month === month);
  const elecCard = h("div", { class: "card stack" }, h("div", { class: "card-head" }, h("h3", {}, "Лічильник електроенергії"),
    cur ? h("span", { class: "chip " + (cur.confirmed ? "st-completed" : "st-pending") }, cur.confirmed ? "підтверджено" : "не підтверджено") : null),
    cur ? h("div", { class: "stack" },
      h("div", { class: "sum-line" }, h("span", {}, "Показники"), h("b", {}, `${fmtNum(cur.prev)} → ${fmtNum(cur.curr)} = ${fmtNum(cur.used)}\u00a0кВт·год`)),
      h("div", { class: "sum-line" }, h("span", {}, "Тариф"), h("b", {}, money(cur.tariff) + "\u00a0/\u00a0кВт·год")),
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
      preview.replaceChildren(h("span", {}, used >= 0 ? `${fmtNum(used)}\u00a0кВт·год` : "Перевірте показники"), h("b", {}, used >= 0 && t > 0 ? money(Math.round(used * t)) : "—"));
    };
    [prevIn, currIn, tariffIn].forEach((x) => x.addEventListener("input", calc));
    calc();
    openSheet(r ? "Показники: " + monthTitle(month) : "Нові показники: " + monthTitle(month), h("div", { class: "stack" },
      h("div", { class: "ap-grid ap-grid-2" }, field("Попередній показник", prevIn), field("Поточний показник", currIn)),
      h("div", { class: "ap-grid ap-grid-2" }, field("Ціна за 1\u00a0кВт·год, ₴", tariffIn), field("Дата зняття", readOn)),
      preview, field("Фактично сплачено, ₴", paid), r && r.confirmed ? field("Причина виправлення", reason) : null,
      h("button", { class: "btn primary big", onclick: async () => {
        const body = { prev: prevIn.value.replace(",", "."), curr: currIn.value.replace(",", "."), tariff: toKop(tariffIn.value), read_on: readOn.value,
          paid_amount: paid.value.trim() ? toKop(paid.value) : null };
        if (!(body.tariff > 0)) return toast("Вкажіть ціну за 1\u00a0кВт·год", true);
        if (r) await run(() => api.patch(`/api/utilities/readings/${r.id}`, { ...body, reason: reason.value.trim() || null }), "Збережено");
        else await run(() => api.post("/api/utilities/readings", { ...body, month }), "Показники внесено");
        closeSheet(); reload();
      } }, "Зберегти")));
  }

  const f = g.forecast;
  const genCard = h("div", { class: "card stack" },
    h("div", { class: "card-head" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("gauge"), "Генератор: " + monthTitle(month).toLowerCase())),
    h("div", { class: "gen-stats" },
      h("div", {}, h("span", {}, "Мотогодини"), h("b", {}, fmtNum(gen.hours))),
      h("div", {}, h("span", {}, "Бензин"), h("b", {}, `${fmtNum(gen.fuel_l)}\u00a0л`), h("small", {}, money(gen.fuel_cost))),
      h("div", {}, h("span", {}, "Амортизація"), h("b", {}, money(gen.amort_cost))),
      h("div", {}, h("span", {}, "Разом"), h("b", {}, money(gen.total))),
      h("div", {}, h("span", {}, "Записів"), h("b", {}, String(gen.runs))),
      gen.hours ? h("div", {}, h("span", {}, "Середня мотогодина"), h("b", {}, money(Math.round(gen.total / gen.hours)))) : null),
    f && f.hours_so_far && month === today().slice(0, 7) + "-01" ? h("p", { class: "muted small" }, `Прогноз на місяць: ${fmtNum(f.projected_hours)}\u00a0мотогод. ≈ ${money(f.projected_cost)}.`) : null,
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

  main.append(generatorCard(g, reload), kpis,
    h("div", { class: "grid two", style: { marginTop: "12px" } }, detail, elecCard),
    h("div", { style: { marginTop: "12px" } }, genCard));
};

VIEWS.shifts = async (main) => {
  const [closed, all, payouts] = await Promise.all([api.get("/api/shifts?status=closed"), api.get("/api/shifts"), api.get("/api/payouts")]);
  main.append(topbar("Зміни й виплати", "Каса змін, нарахування майстрам і виплати",
    h("button", { class: "btn", onclick: () => bonusSheet() }, icon("box", "sm"), "Бонуси за товари"),
    h("button", { class: "btn", onclick: () => payoutForm(null) }, icon("plus", "sm"), "Інша виплата / аванс")));
  async function bonusSheet() {
    const rows = await Promise.all(state.masters.map((m) => api.get(`/api/bonuses?master_id=${m.id}`).then((b) => [m, b]).catch(() => [m, null])));
    openSheet("Бонуси за товари (10%)", h("div", { class: "stack" },
      h("p", { class: "muted small" }, "Виплачуються раз на місяць. Повернення товару після виплати віднімається з наступного бонусу."),
      rows.map(([m, b]) => h("div", { class: "sum-line" }, h("span", {}, m.full_name, h("span", { class: "muted small" }, ` · накопичено ${money(b ? b.unpaid : 0)}`)),
        b && b.unpaid > 0 ? h("button", { class: "btn sm primary", onclick: async () => {
          const r = await run(() => api.post("/api/bonuses/pay", { master_id: m.id }), "Бонус виплачено");
          if (r.paid) { closeSheet(); reload(); }
        } }, "Виплатити") : h("span", { class: "muted small" }, "—")))));
  }
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
        r.revenue !== undefined ? h("span", { class: "chip plain" }, `Виручка: ${money(r.revenue)}`) : null, r.cancellations ? h("span", { class: "chip st-pending" }, `Скасувань: ${r.cancellations}`) : null),
      r.cash ? h("div", { class: "row small" },
        h("span", { class: "chip st-completed" }, `Заробіток: ${money(r.master_earning)}`),
        r.cash.master_takes ? h("span", { class: "chip st-confirmed" }, `Взяв з каси: ${money(r.cash.master_takes)}`) : null,
        h("span", { class: "chip st-in_progress" }, `У касі лишилось: ${money(r.cash.leave_in_till)}`)) : null,
      h("div", { class: "row" },
        h("button", { class: "btn grow", onclick: () => openShift(s.id) }, icon("receipt", "sm"), "Звіт"),
        h("button", { class: "btn primary grow", onclick: () => payoutForm(s) }, icon("banknote", "sm"), "Виплатити"))));
  }
  main.append(h("h2", { style: { marginTop: "22px" } }, "Усі зміни"));
  main.append(h("div", { class: "card table-wrap scroll-box" }, h("table", {},
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
    const tag = select([["advance", "Аванс"], ["bonus", "Премія"], ["correction_overpayment", "Повернення переплати"]], "advance");
    const idem = uuid();
    const btn = h("button", { class: "btn gold big", onclick: async () => {
      btn.disabled = true;
      try {
        await run(() => api.post("/api/payouts", { master_id: +mst.value, amount: toKop(amount.value), shift_id: shift ? shift.id : null, method: method.value,
          note: note.value || null, tag: shift ? null : tag.value, __idem: idem }), "Виплату підтверджено");
        closeSheet(); reload();
      } catch (_) { btn.disabled = false; }
    } }, "Підтвердити виплату");
    openSheet(shift ? "Виплата за зміну" : "Виплата майстру", h("div", { class: "stack" }, field("Майстер", mst), field("Сума, ₴", amount), field("Спосіб", method),
      shift ? null : field("Тип", tag), field("Примітка", note), btn));
  }
};

VIEWS.orders = async (main) => {
  const body = h("div");
  const mst = select([["", "Усі майстри"], ...state.masters.map((m) => [m.id, m.full_name])], "", { style: { width: "auto" } });
  const search = h("input", { type: "search", placeholder: "Клієнт, послуга або №", "aria-label": "Пошук: клієнт, послуга або номер замовлення", oninput: () => render() });
  let q = "period=month", pay = "", list = [];
  const fCount = h("i", { class: "filt-n" });
  const extra = h("div", { class: "ord-extra" });
  const fBtn = h("button", { class: "btn filt-btn", type: "button", "aria-expanded": "false", "aria-label": "Фільтри: майстер і оплата",
    onclick: () => { const open = extra.classList.toggle("open"); fBtn.classList.toggle("on", open); fBtn.setAttribute("aria-expanded", String(open)); } },
  icon("sliders", "sm"), fCount);
  const syncCount = () => { const n = (mst.value ? 1 : 0) + (pay ? 1 : 0); fCount.textContent = n ? String(n) : ""; fBtn.classList.toggle("has", !!n); };
  const paySeg = seg([["", "Усі"], ["cash", "Готівка"], ["card", "Картка"]], "", (v) => { pay = v; syncCount(); render(); });
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
    const sum = (f) => rows.filter((o) => o.status === "completed").reduce((a, o) => a + f(o), 0);
    const cash = sum((o) => (o.payment_method === "cash" ? o.amount : 0));
    const total = sum((o) => o.amount);
    const kpis = h("div", { class: "grid kpi kpi-6" },
      kpi("Замовлень", rows.length, "", { ico: "receipt", raw: true, sub: rows.length ? `середній чек ${money(Math.round(total / rows.length))}` : null }),
      kpi("Виручка", total, "dark", { ico: "trend", spark: dailySeries(rows, (o) => o.closed_at, (o) => o.amount) }),
      kpi("Готівка", cash, "", { ico: "banknote" }),
      kpi("Картка", total - cash, "", { ico: "card" }),
      kpi("Майстрам", sum((o) => o.master_earning), "", { ico: "coins" }),
      kpi("Салону", sum((o) => o.salon_earning), "accent", { ico: "home", spark: dailySeries(rows, (o) => o.closed_at, (o) => o.salon_earning) }));
    if (!rows.length) { body.replaceChildren(kpis, h("div", { class: "card" }, emptyState(list.length ? "Нічого не знайдено — змініть пошук чи фільтр" : "Немає замовлень за період", "receipt"))); return; }
    const days = [];
    for (const o of rows) {
      const d = o.closed_at.slice(0, 10);
      if (!days.length || days[days.length - 1].d !== d) days.push({ d, items: [] });
      days[days.length - 1].items.push(o);
    }
    const dayTitle = (d) => `${WD_LONG[(new Date(d + "T12:00").getDay() + 6) % 7]}, ${Number(d.slice(8, 10))} ${MONTHS_GEN[+d.slice(5, 7) - 1]}`;
    const dayTotal = (g) => g.items.filter((o) => o.status === "completed").reduce((a, o) => a + o.amount, 0);
    const table = h("div", { class: "card table-wrap only-desk" }, h("table", { class: "orders-table" },
      h("tr", {}, h("th", {}, "Час"), h("th", {}, "Клієнт і послуга"), h("th", {}, "Майстер"), h("th", {}, "Оплата"),
        h("th", { class: "num" }, "Сума"), h("th", { class: "num" }, "Матеріали"), h("th", { class: "num" }, "Майстру"), h("th", { class: "num" }, "Салону")),
      days.map((g) => [
        h("tr", { class: "day-row" }, h("td", { colspan: 4 }, h("b", {}, dayTitle(g.d)), h("span", { class: "muted" }, ` · ${g.items.length} ${plural(g.items.length, "замовлення", "замовлення", "замовлень")}`)),
          h("td", { class: "num" }, h("b", {}, money(dayTotal(g)))), h("td", { colspan: 3 })),
        g.items.map((o) => h("tr", { class: "click" + (o.needs_materials ? " no-mat" : ""), onclick: () => (o.needs_materials ? orderMaterialsSheet(o, load) : orderSheet(o.id, load)) },
          h("td", {}, h("b", {}, hm(o.closed_at)), h("div", { class: "muted small" }, "#", o.id, o.revision > 1 ? " · виправлено" : "")),
          h("td", {}, h("div", {}, o.client_name, o.needs_materials ? h("span", { class: "chip st-overdue", style: { marginLeft: "6px" } }, "без матеріалів") : null,
            o.status !== "completed" ? h("span", { class: "chip " + (o.status === "unpaid" ? "st-pending" : "st-cancelled"), style: { marginLeft: "6px" } }, ORDER_STATUS[o.status]) : null), h("div", { class: "muted small" }, o.service_name)),
          h("td", {}, h("span", { class: "row nowrap", style: { gap: "8px" } }, avatar(o.master_name, "xs"), h("span", {}, o.master_name))),
          h("td", {}, h("span", { class: "row nowrap", style: { gap: "6px" } }, icon(payIco(o.payment_method), "sm"), PAY[o.payment_method])),
          h("td", { class: "num" }, h("b", {}, money(o.amount))), h("td", { class: "num muted" }, money(o.materials_cost)),
          h("td", { class: "num" }, money(o.master_earning)), h("td", { class: "num" }, money(o.salon_earning))))]),
      h("tr", { class: "total-row" }, h("td", { colspan: 4 }, h("b", {}, `Разом: ${rows.length}`)), h("td", { class: "num" }, h("b", {}, money(total))),
        h("td", { class: "num" }, money(sum((o) => o.materials_cost))), h("td", { class: "num" }, money(sum((o) => o.master_earning))), h("td", { class: "num" }, money(sum((o) => o.salon_earning))))));
    const cards = h("div", { class: "only-mob anim-in" }, days.map((g) => h("div", { class: "ord-day" },
      h("div", { class: "ord-day-head" }, h("span", {}, dayTitle(g.d)), h("b", {}, money(dayTotal(g)))),
      h("div", { class: "card ord-list" }, g.items.map((o) => h("button", { class: "ord-item" + (o.needs_materials ? " no-mat" : ""), onclick: () => (o.needs_materials ? orderMaterialsSheet(o, load) : orderSheet(o.id, load)) },
        h("div", { class: "ord-time" }, hm(o.closed_at)),
        h("div", { class: "grow ord-main" }, h("b", {}, o.client_name,
          o.status !== "completed" ? h("span", { class: "chip " + (o.status === "unpaid" ? "st-pending" : "st-cancelled"), style: { marginLeft: "6px" } }, ORDER_STATUS[o.status]) : null),
          h("div", { class: "muted small" }, o.service_name),
          h("div", { class: "muted small ord-master" }, avatar(o.master_name, "xs"), h("span", {}, o.master_name + (o.revision > 1 ? " · виправлено" : "")))),
        h("div", { class: "ord-amt" }, h("b", {}, money(o.amount)), h("span", { class: "small muted row", style: { gap: "4px" } }, icon(payIco(o.payment_method), "sm"), PAY[o.payment_method]))))))));
    body.replaceChildren(kpis, table, cards);
  };
  mst.addEventListener("change", () => { syncCount(); load(); });
  main.append(topbar("Замовлення", "Закриті замовлення: оплата, частка майстра і салону"),
    h("div", { class: "filters" }, periodPicker((x) => { q = x; load(); }),
      h("div", { class: "row filters-2 ord-filters" }, (extra.append(mst, paySeg), extra), h("div", { class: "search-box grow" }, icon("search", "sm"), search), fBtn)), body);
  await load();
};

const ORDER_STATUS = { completed: "Оплачено", unpaid: "Не оплачено", cancelled: "Скасовано", refunded: "Повернено" };
async function orderSheet(id, onChange) {
  const o = await run(() => api.get("/api/orders/" + id));
  const isDir = state.user && state.user.role === "director";
  const amount = h("input", { inputmode: "decimal", value: kopToInput(o.amount) });
  const price = h("input", { inputmode: "decimal", value: kopToInput(o.price_snapshot) });
  const method = select(Object.entries(o.payment_method === "transfer" ? PAY : PAY_CHOICES), o.payment_method);
  const reason = h("input", { placeholder: "Обов'язково: чому виправляємо" });
  const done = () => { closeSheet(); onChange && onChange(); };
  const live = o.status === "completed" || o.status === "unpaid";
  const voidBtn = (kind, label, danger) => h("button", { class: "btn" + (danger ? " danger" : ""), onclick: () => confirmSheet(
    label + "?", kind === "refund" ? "Гроші повертаються клієнту; заробіток майстра сторнується. Якщо вже виплачено — утримається з наступної виплати."
      : "Замовлення анулюється, матеріали повертаються на склад, запис стає скасованим.", label,
    async (why) => { await run(() => api.post(`/api/orders/${id}/${kind}`, { reason: why, version: o.version }), "Готово"); done(); },
    { danger: true, withReason: true }) }, label);
  openSheet(`Замовлення #${o.id}`,
    h("div", { class: "stack" },
      h("div", {}, h("b", {}, o.service_name), " · ", o.client_name, " · ", o.master_name),
      h("div", { class: "muted small" }, dmy(o.closed_at), " ", hm(o.closed_at), " · редакція ", o.revision, " · ",
        h("span", { class: "status st-" + (o.status === "completed" ? "completed" : o.status === "unpaid" ? "pending" : "cancelled") }, ORDER_STATUS[o.status] || o.status)),
      orderAuthors(o),
      fiscalBlock(o),
      h("div", { class: "card" },
        h("div", { class: "sum-line" }, h("span", {}, "Ціна (P)"), h("span", {}, money(o.price_snapshot))),
        o.discount ? h("div", { class: "sum-line" }, h("span", {}, "Знижка (за рахунок салону)"), h("span", {}, money(o.discount))) : null,
        h("div", { class: "sum-line" }, h("span", {}, o.status === "unpaid" ? "До сплати (A)" : "Сплачено (A)"), h("b", {}, money(o.amount))),
        h("div", { class: "sum-line" }, h("span", {}, "Матеріали"), h("span", {}, money(o.materials_cost))),
        o.materials.map((m) => h("div", { class: "sum-line muted small" }, h("span", {}, "· ", m.name, " × ", m.qty), h("span", {}, money(m.total)))),
        h("div", { class: "sum-line" }, h("span", {}, `Майстру (${pct(o.master_rate_bp)} від ціни)`), h("b", {}, money(o.master_earning))),
        isDir ? h("div", { class: "sum-line total" }, h("span", {}, "Салону"), h("span", { class: o.salon_earning < 0 ? "neg" : "" }, money(o.salon_earning))) : null),
      isDir && o.status === "unpaid" ? h("div", { class: "card stack" }, h("b", {}, "Клієнт оплатив пізніше"),
        h("div", { class: "row" }, ...Object.entries(PAY_CHOICES).map(([k, v]) => h("button", { class: "btn primary", onclick: async () => {
          await run(() => api.post(`/api/orders/${id}/mark-paid`, { payment_method: k, amount: toKop(amount.value), version: o.version }), "Оплату зараховано"); done();
          fiscalizeOrder(id);
        } }, v)))) : null,
      isDir && live ? h("details", {}, h("summary", { class: "muted", style: { cursor: "pointer" } }, "Виправити замовлення (з записом у журнал)"),
        h("div", { class: "stack", style: { marginTop: "10px" } },
          h("div", { class: "form-grid" }, field("Ціна (P), ₴", price), field("Сплачено (A), ₴", amount), field("Оплата", method)),
          field("Причина", reason),
          h("button", { class: "btn gold", onclick: async () => {
            if (!reason.value.trim()) return toast("Вкажіть причину", true);
            await run(() => api.patch("/api/orders/" + id, { price: toKop(price.value), amount: toKop(amount.value), payment_method: method.value,
              reason: reason.value.trim(), version: o.version }), "Замовлення виправлено");
            done();
          } }, "Зберегти виправлення"),
          h("div", { class: "row" }, voidBtn("refund", "Повернення клієнту", true), voidBtn("cancel", "Анулювати замовлення", true)))) : null,
      orderHistory(o)));
}

function fiscalBlock(o) {
  const box = h("div", { class: "row small", "data-testid": "fiscal-block" });
  loadFiscalCfg().then((cfg) => {
    if (!cfg.enabled || o.status !== "completed") return;
    box.append(icon("receipt", "sm"), h("span", { class: "muted grow" }, o.fiscal_receipt_id ? "Фіскальний чек є" : "Фіскального чека ще немає"),
      h("button", { class: "btn sm", onclick: async (e) => { const b = e.currentTarget; b.disabled = true; await fiscalizeOrder(o.id); b.disabled = false; } },
        o.fiscal_receipt_id ? "Друкувати чек" : "Створити чек"));
  });
  return box;
}

const MISSING = { materials: "без матеріалів", phone: "без телефону" };
function orderAuthors(o) {
  return h("div", { class: "muted small" }, "Закрив: ", o.closed_by_name || "—",
    o.edited_by_name ? ` · редагував: ${o.edited_by_name}, ${dm(o.edited_at)} ${hm(o.edited_at)}` : "");
}
function orderHistory(o) {
  const label = { close: "Закрито", correct: "Виправлено", set_phone: "Вказано телефон" };
  return h("div", {}, h("h3", {}, "Історія змін"),
    (o.history || []).map((x) => h("div", { class: "small", style: { padding: "6px 0", borderBottom: "1px solid var(--line)" } },
      h("b", {}, label[x.action] || x.action), " · ", dmy(x.ts), " ", hm(x.ts), " · ", x.actor,
      x.reason ? h("div", { class: "muted" }, x.action === "correct" ? "Причина: " : "", x.reason) : null,
      x.action === "correct" && x.before ? h("div", { class: "muted" }, `Було: ${money(x.before.amount)} / майстру ${money(x.before.master_earning)} → Стало: ${money(x.after.amount)} / майстру ${money(x.after.master_earning)}`) : null,
      x.action === "set_phone" && x.after ? h("div", { class: "muted" }, `${x.after.client_name}: ${x.after.phone}`) : null)));
}
function missingChips(o) {
  return (o.missing || []).map((k) => h("span", { class: "chip st-overdue", style: { marginLeft: "6px" } }, MISSING[k] || k));
}
function incompleteBlock(list, onChange) {
  return h("div", { class: "card unc-card mat-alert" },
    h("div", { class: "card-head" }, h("h3", { class: "row", style: { gap: "8px" } }, icon("receipt"), `Незаповнені замовлення: ${list.length}`),
      h("span", { class: "muted small" }, "Поки їх не доповнено, зміну не закрити")),
    list.map((o) => h("div", { class: "unc-row" },
      h("div", { class: "grow" }, h("b", {}, o.client_name), ...missingChips(o),
        h("div", { class: "muted small" }, `${dm(o.closed_at)} ${hm(o.closed_at)} · ${o.service_name} · ${money(o.amount)}`)),
      h("div", { class: "unc-actions" }, h("button", { class: "btn sm primary", onclick: () => myOrderSheet(o.id, onChange) }, icon("edit", "sm"), "Доповнити")))));
}

VIEWS.myorders = async (main) => {
  const body = h("div");
  let q = "period=day";
  const load = async () => {
    body.replaceChildren(h("div", { class: "skeleton" }, h("i"), h("i"), h("i")));
    const list = await api.get(`/api/orders?${q}`);
    const bad = list.filter((o) => o.missing.length && o.shift_open);
    const total = list.reduce((a, o) => a + o.amount, 0);
    const earn = list.reduce((a, o) => a + o.master_earning, 0);
    body.replaceChildren(
      h("div", { class: "grid kpi", style: { marginBottom: "12px" } },
        kpi("Замовлень", list.length, "", { ico: "receipt", raw: true }), kpi("Виручка", total, "", { ico: "banknote" }), kpi("Мій заробіток", earn, "dark", { ico: "wallet", spark: dailySeries(list, (o) => o.closed_at, (o) => o.master_earning) })),
      bad.length ? incompleteBlock(bad, load) : h("div"),
      list.length ? h("div", { class: "card ord-list" }, list.map((o) => h("button", { class: "ord-item" + (o.missing.length ? " no-mat" : ""), onclick: () => myOrderSheet(o.id, load) },
        h("div", { class: "ord-time" }, dm(o.closed_at), h("br"), hm(o.closed_at)),
        h("div", { class: "grow ord-main" }, h("b", {}, o.client_name), ...missingChips(o), h("div", { class: "muted small" }, o.service_name),
          o.edited_by_name ? h("div", { class: "muted small" }, "редагував: ", o.edited_by_name) : null),
        h("div", { class: "ord-amt" }, h("b", {}, money(o.amount)), h("span", { class: "small muted" }, "мені ", money(o.master_earning))))))
        : h("div", { class: "card" }, emptyState("Немає закритих замовлень за період", "receipt")));
  };
  main.append(topbar("Мої замовлення", "Незаповнені замовлення можна доповнити, поки зміна відкрита"),
    periodPicker((x) => { q = x; load(); }, "day"), body);
  await load();
};

async function myOrderSheet(id, onChange) {
  const o = await run(() => api.get("/api/orders/" + id));
  const done = () => { closeSheet(); onChange && onChange(); };
  const parts = [];
  if (o.shift_open && o.missing.includes("phone")) {
    const phone = h("input", { type: "tel", inputmode: "tel", placeholder: "+380…", autocomplete: "off" });
    parts.push(h("div", { class: "card", style: { padding: "12px" } }, h("div", { class: "row small", style: { marginBottom: "8px" } }, icon("phone", "sm"), h("b", {}, "Телефон клієнта")),
      h("div", { class: "row" }, h("div", { class: "grow" }, phone), h("button", { class: "btn primary", onclick: async (e) => {
        if (phone.value.replace(/\D/g, "").length < 9) { phone.focus(); return toast("Вкажіть номер телефону", true); }
        e.currentTarget.disabled = true;
        try { await run(() => api.put(`/api/orders/${o.id}/phone`, { phone: phone.value.trim() }), "Телефон збережено"); done(); } catch (_) { e.currentTarget.disabled = false; }
      } }, "Зберегти"))));
  }
  if (o.shift_open) {
    state.materials = await api.get("/api/materials").catch(() => state.materials);
    const mats = new Map(), custom = [];
    for (const m of o.materials || []) if (m.material_id) mats.set(m.material_id, m.qty); else custom.push({ name: m.name, unit_cost: m.unit_cost, qty: 1 });
    parts.push(materialsPicker(mats, custom, { open: o.missing.includes("materials") }),
      h("button", { class: "btn" + (o.missing.includes("materials") ? " primary" : ""), onclick: async (e) => {
        const materials = [...mats.entries()].filter(([, q]) => q > 0).map(([mid, q]) => ({ material_id: mid, qty: q })).concat(custom);
        if (!materials.length && o.missing.includes("materials")) return toast("Оберіть використані матеріали", true);
        e.currentTarget.disabled = true;
        try { await run(() => api.patch(`/api/orders/${o.id}`, { materials, reason: "Вказано використані матеріали" }), "Матеріали записано, склад оновлено"); done(); } catch (_) { e.currentTarget.disabled = false; }
      } }, icon("ok", "sm"), "Зберегти матеріали"));
  } else {
    parts.push(h("div", { class: "muted small" }, icon("lock", "sm"), " Зміну закрито — виправлення робить директор."));
  }
  openSheet(`Замовлення #${o.id}`, h("div", { class: "stack" },
    h("div", {}, h("b", {}, o.service_name), " · ", o.client_name, " · ", o.client_phone || "без номера", ...missingChips(o)),
    h("div", { class: "muted small" }, dmy(o.closed_at), " ", hm(o.closed_at)),
    orderAuthors(o),
    h("div", { class: "card" },
      h("div", { class: "sum-line" }, h("span", {}, "Сума"), h("b", {}, money(o.amount))),
      h("div", { class: "sum-line" }, h("span", {}, "Оплата"), h("span", {}, PAY[o.payment_method] || o.payment_method)),
      h("div", { class: "sum-line" }, h("span", {}, "Матеріали"), h("span", {}, money(o.materials_cost))),
      o.materials.map((m) => h("div", { class: "sum-line muted small" }, h("span", {}, "· ", m.name, " × ", m.qty), h("span", {}, money(m.total)))),
      h("div", { class: "sum-line total" }, h("span", {}, "Мій заробіток"), h("span", {}, money(o.master_earning)))),
    ...parts, orderHistory(o)));
}

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
          h("td", { class: "right" }, h("button", { class: "icon-btn del-ico", title: "Видалити", "aria-label": "Видалити витрату", onclick: () => confirmSheet("Видалити витрату?", `${expCat(e.category)} ${money(e.amount)}`, "Видалити",
            async (reason) => { await run(() => api.del(`/api/expenses/${e.id}?reason=${encodeURIComponent(reason)}`), "Видалено"); load(); }, { danger: true, withReason: true }) }, icon("trash", "sm")))))))
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

VIEWS.staff = async (main) => {
  const users = await api.get("/api/users");
  main.append(topbar("Персонал", "Співробітники, ролі і доступ", h("button", { class: "btn primary", onclick: () => userForm(null) }, icon("plus", "sm"), "Співробітник")));
  const def = state.settings;
  main.append(h("div", { class: "card table-wrap" }, h("table", {},
    h("tr", {}, h("th", {}, "Ім'я"), h("th", {}, "Логін"), h("th", {}, "Роль"), h("th", {}, "Telegram"), h("th", {}, "PIN бота"), h("th", {}, "")),
    users.map((u) => h("tr", { class: "click", onclick: () => userForm(u) }, h("td", {}, u.full_name,
      u.role === "master" && u.master_rate_warning ? h("span", { class: "chip", title: "Ставка відрізняється від канонічних 50%" }, ` ставка ${pct(u.master_rate_bp)}`) : null), h("td", {}, u.username), h("td", {}, ROLES[u.role]),
      h("td", {}, u.telegram_linked ? "✓" : ""), h("td", {}, u.bot_pin_set ? "✓" : ""), h("td", {}, u.is_active ? "" : h("span", { class: "chip" }, "неактивний")))))));

  function userForm(u) {
    const isNew = !u;
    const username = h("input", { value: u?.username || "", disabled: !isNew, autocapitalize: "none" });
    const password = h("input", { type: "password", autocomplete: "new-password", placeholder: isNew ? "" : "залиште порожнім" });
    const fullName = h("input", { value: u?.full_name || "" });
    const phone = h("input", { value: u?.phone || "", type: "tel" });
    const role = select(Object.entries(ROLES), u?.role || "master");
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
        service_ids: [...svcIds],
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
    const needsMat = h("input", { type: "checkbox", checked: s ? !!s.needs_materials : false });
    openSheet(s ? s.name : "Нова послуга", h("div", { class: "stack" }, dls, field("Назва", name),
      h("div", { class: "form-grid" }, field("Розділ (крок 1 у боті)", sec), field("Тип послуги (крок 2)", cat), field("Вид (крок 3)", sub)),
      h("div", { class: "form-grid" }, field("Тривалість, хв (для календаря)", dur), field("Ціна, ₴", price), field("Порядок", order)),
      h("label", { class: "row" }, from, "Ціна «від» (остаточна сума — після послуги)"),
      h("label", { class: "row" }, needsMat, "Завжди витрачає матеріали (фарбування) — без них замовлення світиться червоним"),
      h("label", { class: "row" }, active, "Доступна для запису"),
      h("button", { class: "btn gold big", onclick: async () => {
        const body = { name: name.value, section: sec.value.trim() || null, category: cat.value.trim() || null, subcategory: sub.value.trim() || null, duration_min: +dur.value, price: toKop(price.value), price_from: from.checked, needs_materials: needsMat.checked, sort_order: +order.value, is_active: active.checked };
        await run(() => (s ? api.patch("/api/services/" + s.id, body) : api.post("/api/services", body)), "Збережено");
        closeSheet(); await loadRefs(); reload();
      } }, "Зберегти")));
  }
};

const MOVE_KIND = { receipt: "Прихід", order: "Списано на замовлення", order_return: "Повернуто (виправлення)", adjust: "Інвентаризація", writeoff: "Списання", sale: "Продаж товару", return: "Повернення товару", inventory: "Інвентаризація" };

const UNIT_NAMES = { г: "грами (г)", мл: "мілілітри (мл)", шт: "штуки (шт)" };
const PACK_FORMS = {
  шт: ["шт", "шт", "шт"], туба: ["туба", "туби", "туб"], тюбик: ["тюбик", "тюбики", "тюбиків"], банка: ["банка", "банки", "банок"],
  флакон: ["флакон", "флакони", "флаконів"], пляшка: ["пляшка", "пляшки", "пляшок"], каністра: ["каністра", "каністри", "каністр"],
  упаковка: ["упаковка", "упаковки", "упаковок"], пачка: ["пачка", "пачки", "пачок"], набір: ["набір", "набори", "наборів"],
  коробка: ["коробка", "коробки", "коробок"], рулон: ["рулон", "рулони", "рулонів"], пара: ["пара", "пари", "пар"],
  ампула: ["ампула", "ампули", "ампул"], саше: ["саше", "саше", "саше"], балон: ["балон", "балони", "балонів"],
};
const PACK_LIST = Object.keys(PACK_FORMS);
function packWord(name, n) {
  const f = PACK_FORMS[name];
  if (!f) return name;
  if (!Number.isInteger(Math.round(n * 100) / 100)) return f[1];
  return plural(Math.abs(Math.round(n)), f[0], f[1], f[2]);
}
const fmtNum = (x) => (Math.round(x * 100) / 100).toLocaleString("uk-UA");
function fmtQty(m, q) {
  if (m.pack_size > 1) { const p = q / m.pack_size; return `${fmtNum(q)} ${m.unit} · ${fmtNum(p)} ${packWord(m.pack_name, p)}`; }
  if (m.unit === "шт" && m.pack_name && m.pack_name !== "шт") return `${fmtNum(q)} ${packWord(m.pack_name, q)}`;
  return `${fmtNum(q)} ${m.unit}`;
}
const matCost = (m, q) => Math.round((m.unit_cost * q) / (m.pack_size || 1));
const parseNum = (v) => parseFloat(String(v).replace(",", ".").replace(/\s/g, ""));
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
  main.append(topbar("Склад",
    canEdit ? h("button", { class: "btn primary", onclick: () => materialForm(null, list, refresh) }, icon("plus", "sm"), "Матеріал") : null));
  const search = h("input", { type: "search", placeholder: "Пошук: назва або тон", id: "stock-search", oninput: (e) => { q = e.target.value.trim().toLowerCase(); draw(); } });
  const tabs = seg([["left", "Залишки"], ["products", "Товари"], ["moves", "Рух складу"]], tab, (v) => { tab = v; if (v === "left") movesFor = null; draw(); });
  const sToggle = searchToggle(search);
  main.append(h("div", { class: "row stock-bar" }, tabs, sToggle.btn), sToggle.box, body);
  function productForm(x) {
    const name = h("input", { value: x?.name || "" });
    const sku = h("input", { value: x?.sku || "" });
    const cost = h("input", { inputmode: "decimal", value: x ? kopToInput(x.cost_price_cents) : "" });
    const markup = h("input", { type: "number", min: 0, step: 5, value: x ? x.markup_bp / 100 : 100 });
    const sale = h("input", { inputmode: "decimal", value: x ? kopToInput(x.sale_price_cents) : "", placeholder: "порожньо = собівартість + націнка" });
    const qty = h("input", { type: "number", min: 0, value: 0 });
    const active = h("input", { type: "checkbox", checked: x ? x.active : true });
    openSheet(x ? x.name : "Новий товар", h("div", { class: "stack" },
      field("Назва", name), h("div", { class: "form-grid" }, field("Артикул", sku), field("Собівартість, ₴", cost)),
      h("div", { class: "form-grid" }, field("Націнка, % (підказка для ціни)", markup), field("Ціна продажу, ₴ (фіксована)", sale)),
      x ? h("label", { class: "row" }, active, "Продається") : field("Початковий залишок, шт", qty),
      h("p", { class: "muted small" }, "Майстру — 10% від ціни продажу, виплата раз на місяць."),
      h("button", { class: "btn primary big", onclick: async () => {
        const body2 = { name: name.value.trim(), sku: sku.value.trim() || null, cost_price_cents: toKop(cost.value) || 0,
          markup_bp: Math.round(parseFloat(markup.value || "0") * 100), sale_price_cents: sale.value.trim() ? toKop(sale.value) : null };
        if (x) { body2.active = active.checked; if (body2.sale_price_cents === null) delete body2.sale_price_cents; await run(() => api.patch(`/api/products/${x.id}`, body2), "Збережено"); }
        else await run(() => api.post("/api/products", { ...body2, stock_qty: parseInt(qty.value, 10) || 0 }), "Товар додано");
        closeSheet(); draw();
      } }, "Зберегти")));
  }
  function productQty(x, kind) {
    const qty = h("input", { type: "number", min: 0, value: kind === "inventory" ? x.stock_qty : 1 });
    const cost = h("input", { inputmode: "decimal", placeholder: "сума за накладною (необов'язково)" });
    openSheet(kind === "receive" ? `Прихід: ${x.name}` : `Інвентаризація: ${x.name}`, h("div", { class: "stack" },
      field(kind === "receive" ? "Кількість, шт" : "Фактичний залишок, шт", qty), kind === "receive" ? field("Сума, ₴", cost) : null,
      h("button", { class: "btn primary big", onclick: async () => {
        if (kind === "receive") await run(() => api.post(`/api/products/${x.id}/receive`, { qty: parseInt(qty.value, 10), cost: cost.value.trim() ? toKop(cost.value) : null }), "Прихід записано");
        else await run(() => api.post("/api/stock/inventory", { items: [{ product_id: x.id, actual: parseInt(qty.value, 10) }] }), "Залишок виправлено");
        closeSheet(); draw();
      } }, "Зберегти")));
  }

  async function draw() {
    if (tab === "products") {
      const prods = await api.get("/api/products?all=true");
      const shown = prods.filter((x) => !q || (x.name + " " + (x.sku || "") + " " + (x.barcode || "")).toLowerCase().includes(q));
      body.replaceChildren(
        canEdit ? h("div", { class: "row", style: { marginBottom: "10px" } }, h("button", { class: "btn primary", onclick: () => productForm(null) }, icon("plus", "sm"), "Товар")) : null,
        shown.length ? h("div", { class: "card table-wrap" }, h("table", {},
          h("tr", {}, h("th", {}, "Товар"), h("th", { class: "num" }, "Залишок"), h("th", { class: "num" }, "Собівартість"), h("th", { class: "num" }, "Ціна продажу"), canEdit ? h("th", {}, "") : null),
          shown.map((x) => h("tr", { class: x.active ? "" : "inactive" }, h("td", {}, h("b", {}, x.name), x.sku ? h("span", { class: "muted small" }, ` · ${x.sku}`) : null),
            h("td", { class: "num " + (x.stock_qty < 0 ? "neg" : "") }, `${x.stock_qty} шт`), h("td", { class: "num" }, money(x.cost_price_cents)),
            h("td", { class: "num" }, money(x.sale_price_cents)),
            canEdit ? h("td", { class: "right" }, h("div", { class: "stock-actions" },
              h("button", { class: "btn sm", onclick: () => productQty(x, "receive") }, icon("plus", "sm"), "Прихід"),
              h("button", { class: "btn sm", onclick: () => productQty(x, "inventory") }, "Інвентаризація"),
              h("button", { class: "btn sm", onclick: () => productForm(x) }, icon("edit", "sm")))) : null))))
          : h("div", { class: "card" }, emptyState("Товарів ще немає", "box")));
      return;
    }
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

VIEWS.schedule = async (main) => {
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
    h("span", {}, "Години замість галочки — якщо не звичайні.")));

  const counters = {};
  const workCount = (m) => g.days.filter((d) => !g.cells[m.id][d].off).length;
  const dayBtn = (m, d) => {
    const c = g.cells[m.id][d];
    const usual = m.usual_hours;
    const unusual = !c.off && (c.start !== usual[0] || c.end !== usual[1]);
    const btn = h("button", {
      type: "button", disabled: !canEdit || null, "aria-pressed": String(!c.off),
      class: "sched-day " + (c.off ? "off" : "on") + (d === today() ? " today" : ""),
      "aria-label": `${weekday(d)} ${dm(d)}: ${c.off ? "вихідний" : `${c.start}–${c.end}`}`,
      onclick: () => (mode === "hours" ? dayForm(m, d, c) : toggle(m, d, c, btn)),
    },
    h("span", { class: "num" }, String(+d.slice(8, 10))),
    c.off ? null : unusual ? h("span", { class: "hrs" }, `${short(c.start)}–${short(c.end)}`) : icon("ok"));
    return btn;
  };
  const card = (m) => {
    const usual = m.usual_hours;
    counters[m.id] = h("div", { class: "muted small" });
    const setCount = () => { counters[m.id].textContent = `робочих днів: ${workCount(m)} · зазвичай ${short(usual[0])}–${short(usual[1])}`; };
    counters[m.id].refresh = setCount;
    setCount();
    return h("div", { class: "card stack" },
      h("div", { class: "row sched-head" }, avatar(m.full_name, "sm"),
        h("div", { class: "grow" }, h("b", {}, m.full_name), counters[m.id]),
        canEdit ? h("div", { class: "sched-actions" }, h("button", { class: "btn sm", onclick: () => parityForm(m) }, "Парні / непарні…"),
          h("button", { class: "btn sm ghost", onclick: () => copyForm(m) }, "Копіювати тиждень…")) : null),
      h("div", { class: "sched-grid" },
        ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"].map((w) => h("div", { class: "sched-wd" }, w)),
        g.days.map((d) => dayBtn(m, d))));
  };
  const draw = () => body.replaceChildren(...g.masters.map(card));
  draw();

  let busy = Promise.resolve(), pending = 0, failed = false;
  function toggle(m, d, c, btn) {
    const api_url = `/api/schedule/${m.id}/${d}`;
    const tpl = c.template;
    let req;
    if (!c.off) {
      req = !tpl && c.source === "day" ? () => api.del(api_url) : () => api.put(api_url, { off: true });
      Object.assign(c, { off: true, start: null, end: null });
    } else {
      const [s1, e1] = tpl || m.usual_hours;
      req = tpl && c.source === "day" ? () => api.del(api_url) : () => api.put(api_url, { start: s1, end: e1 });
      Object.assign(c, { off: false, start: s1, end: e1 });
    }
    c.source = "day";
    btn.replaceWith(dayBtn(m, d));
    counters[m.id].refresh();
    pending++;
    busy = busy.then(async () => {
      try { await req(); } catch (e) { failed = true; toast(e.message, true); }
      pending--;
      if (pending === 0 && failed) {
        const fresh = await api.get(url).catch(() => null);
        if (fresh && pending === 0) { g = fresh; failed = false; draw(); }
      }
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
  function parityForm(m) {
    let parity = "even";
    const from = h("input", { type: "date", value: start });
    const to = h("input", { type: "date", value: end });
    const s1 = h("input", { type: "time", value: m.usual_hours[0] });
    const e1 = h("input", { type: "time", value: m.usual_hours[1] });
    const monthEnd = () => { const d = new Date(today() + "T12:00"); return isoDate(new Date(d.getFullYear(), d.getMonth() + 2, 0, 12)); };
    openSheet(`Парні / непарні дні: ${m.full_name}`, h("div", { class: "stack" },
      seg([["even", "Парні дати (2, 4, 6…)"], ["odd", "Непарні (1, 3, 5…)"]], parity, (v) => { parity = v; }),
      h("div", { class: "form-grid" }, field("З", from), field("По", to)),
      h("button", { class: "btn sm ghost", type: "button", onclick: () => { from.value = today(); to.value = monthEnd(); } }, "З сьогодні до кінця наступного місяця"),
      h("div", { class: "form-grid" }, field("Початок дня", s1), field("Кінець дня", e1)),
      h("p", { class: "muted small", style: { margin: 0 } }, "Обрані дати стануть робочими, решта — вихідними. Записи, що вже є, не скасовуються — перевірте календар."),
      h("button", { class: "btn primary big", onclick: async () => {
        const r = await run(() => api.post(`/api/schedule/${m.id}/parity`, { parity, date_from: from.value, date_to: to.value, start: s1.value, end: e1.value }), "Графік оновлено");
        if (r) { closeSheet(); reload(); }
      } }, "Застосувати")));
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

const SETTINGS_GROUPS = [
  ["salon", "home", "Салон", "Загальне", "Назва, адреса, контакти, графік роботи"],
  ["booking", "calendar", "Запис клієнтів", "Робота", "Слоти, неробочий час, нагадування, захист онлайн-запису"],
  ["staff", "users", "Майстри і доступ", "Робота", "Оплата майстрів, режим «Ми у відпустці»"],
  ["payments", "card", "Оплата і каса", "Гроші", "POS-термінал, Checkbox (ПРРО), чековий принтер"],
  ["stock", "box", "Склад і генератор", "Гроші", "Автосписання залишків, вартість мотогодини"],
  ["bot", "send", "Telegram-бот і сповіщення", "Зв'язок", "Код входу персоналу, сповіщення директору"],
  ["security", "lock", "Безпека входу", "Система", "Код у Telegram на новому пристрої"],
  ["data", "history", "Копії і швидкодія", "Система", "Резервні копії, Google Drive, швидкодія"],
];

function settingsNav(active, { index = false } = {}) {
  let section = null;
  const out = [];
  for (const [id, ico, label, sec, desc] of SETTINGS_GROUPS) {
    if (sec !== section) { out.push(h("div", { class: "set-section" }, sec)); section = sec; }
    out.push(h("a", { href: `#/settings?g=${id}`, class: "set-link" + (id === active ? " active" : ""), "data-testid": `set-${id}`,
      "aria-current": id === active ? "page" : null },
      h("span", { class: "set-ico" }, icon(ico)), h("span", { class: "set-text" }, h("b", {}, label), index ? h("small", {}, desc) : null),
      index ? icon("right", "sm") : null));
  }
  return h("nav", { class: "set-nav" + (index ? " set-index" : ""), "aria-label": "Розділи налаштувань" }, out);
}

const saveSettingsBtn = (body, label = "Зберегти") => h("button", { class: "btn primary", onclick: async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try { state.settings = await run(() => api.put("/api/settings", body()), "Налаштування збережено"); } catch (_) {  } finally { btn.disabled = false; }
} }, label);

const SETTINGS_BUILD = {
  salon: (s) => {
    const name = h("input", { value: s.salon_name || "" });
    const addr = h("input", { value: s.salon_address || "" });
    const phone = h("input", { value: s.salon_phone || "", type: "tel" });
    const insta = h("input", { value: s.salon_instagram || "", placeholder: "@md.salon" });
    const days = WD_LONG.map((n, i) => {
      const sp = (s.schedule || {})[String(i)];
      const on = h("input", { type: "checkbox", checked: !!sp });
      const a = h("input", { type: "time", value: sp ? sp[0] : "09:00" });
      const b = h("input", { type: "time", value: sp ? sp[1] : "20:00" });
      return { i, on, a, b, row: h("div", { class: "row small sched-day" }, h("label", { class: "row", style: { width: "130px", gap: "6px" } }, on, n),
        h("div", { style: { width: "110px" } }, a), "—", h("div", { style: { width: "110px" } }, b)) };
    });
    return [
      h("div", { class: "card stack" }, h("h3", {}, "Дані салону"), field("Назва салону", name), field("Адреса салону", addr),
        h("div", { class: "form-grid" }, field("Телефон", phone), field("Instagram", insta)),
        h("p", { class: "muted small", style: { margin: 0 } }, "Показуються клієнтам у Telegram-боті й на чеках."),
        saveSettingsBtn(() => ({ salon_name: name.value, salon_address: addr.value.trim(), salon_phone: phone.value.trim(), salon_instagram: insta.value.trim() }))),
      h("div", { class: "card stack" }, h("h3", {}, "Графік салону"), days.map((d) => d.row),
        saveSettingsBtn(() => ({ schedule: Object.fromEntries(days.map((d) => [String(d.i), d.on.checked ? [d.a.value, d.b.value] : null])) }))),
    ];
  },
  booking: (s) => {
    const step = h("input", { type: "number", min: 5, step: 5, value: s.slot_step_min });
    const horizon = h("input", { type: "number", min: 1, value: s.booking_horizon_days });
    const lead = h("input", { type: "number", min: 0, value: s.booking_min_lead_min });
    const offPolicy = select([["forbid", "Заборонити"], ["if_free_place", "Лише якщо є вільне робоче місце"], ["allow", "Дозволити (з підтвердженням)"]], s.off_hours_booking || "if_free_place");
    const places = h("input", { type: "number", min: 1, max: 20, value: s.workplaces || 2 });
    const maxM = h("input", { type: "number", min: 1, max: 50, value: s.max_masters || 4 });
    const auto = h("input", { type: "checkbox", checked: s.telegram_auto_confirm });
    const favVisits = h("input", { type: "number", min: 1, max: 50, value: s.favorite_master_visits || 3 });
    const remind = h("input", { type: "checkbox", checked: s.reminders_enabled !== false });
    return [
      h("div", { class: "card stack" }, h("h3", {}, "Запис клієнтів"),
        h("div", { class: "form-grid" }, field("Крок слотів, хв", step), field("Запис наперед, днів", horizon), field("Мін. хв до запису (бот)", lead)),
        field("Запис персоналом у неробочий час", offPolicy),
        h("div", { class: "form-grid" }, field("Робочих місць (одночасно майстрів)", places), field("Майстрів у салоні, максимум", maxM)),
        h("label", { class: "row" }, auto, "Записи з Telegram одразу підтверджені"),
        field("Улюблений майстер клієнта — після скількох візитів", favVisits),
        h("label", { class: "row" }, remind, "Нагадування клієнтам у Telegram за добу і за 2 години (бот питає згоду)"),
        saveSettingsBtn(() => ({ slot_step_min: +step.value, booking_horizon_days: +horizon.value, booking_min_lead_min: +lead.value,
          off_hours_booking: offPolicy.value, workplaces: Math.max(1, parseInt(places.value || "2", 10)), max_masters: Math.max(1, parseInt(maxM.value || "4", 10)),
          telegram_auto_confirm: auto.checked, favorite_master_visits: Math.max(1, parseInt(favVisits.value || "3", 10)), reminders_enabled: remind.checked }))),
      antispamSettingsCard(s),
    ];
  },
  staff: (s) => {
    const takesCash = h("input", { type: "checkbox", checked: s.master_takes_cash !== false });
    return [
      h("div", { class: "card stack" }, h("h3", {}, "Оплата майстрів"),
        h("p", { class: "muted small", style: { margin: 0 } }, "Майстру — 50% від ціни послуги (до знижки). Знижки й матеріали — за рахунок салону. Закриті замовлення не перераховуються."),
        h("label", { class: "row" }, takesCash, "Після зміни майстер бере заробіток з готівки в касі (решту доплачує директор)"),
        saveSettingsBtn(() => ({ master_takes_cash: takesCash.checked }))),
      vacationCard(),
      h("a", { href: "#/staff", class: "small" }, "Персонал, PIN і ставки майстрів →"),
    ];
  },
  payments: (s) => {
    const termMode = select([["off", "Вимкнено"], ["mock", "Імітація (без списання грошей)"], ["live", "Реальні оплати (ключі на сервері)"]], s.card_terminal_mode || "off");
    return [
      posTerminalCard(),
      checkboxCard(),
      h("details", { class: "card" }, h("summary", { class: "row", style: { cursor: "pointer" } }, h("b", { class: "grow" }, "«Термінал» ПриватБанку (NFC, застаріле)"), icon("down", "sm")),
        h("div", { class: "stack", style: { marginTop: "10px" } }, field("Режим", termMode),
          h("p", { class: "muted small", style: { margin: 0 } }, "Хмарна інтеграція із застосунком «Термінал» на телефоні відкладена; окремої кнопки в закритті замовлення немає. " +
            "Для оплати карткою використовуйте «POS Термінал» або ручне підтвердження."),
          saveSettingsBtn(() => ({ card_terminal_mode: termMode.value })))),
    ];
  },
  stock: (s) => [stockSettingsCard(s), generatorSettingsCard(s)],
  bot: (s) => {
    const staffCode = h("input", { inputmode: "numeric", maxlength: 8, value: s.bot_staff_code || "" });
    return [
      h("div", { class: "card stack" }, h("h3", {}, "Telegram-бот"),
        field("Код входу в розділ «Для майстрів»", staffCode),
        h("p", { class: "muted small", style: { margin: 0 } }, "Після коду кожен обирає себе й вводить особистий PIN (задається в розділі «Персонал»)."),
        saveSettingsBtn(() => ({ bot_staff_code: staffCode.value.trim() }))),
      alertsSettingsCard(),
    ];
  },
  security: (s) => [twofaSettingsCard(s), h("a", { href: "#/security", class: "small" }, "Журнал входів і запити дозволу →")],
  data: async () => {
    const [backups, drive] = await Promise.all([api.get("/api/backups").catch(() => []), api.get("/api/gdrive").catch(() => null)]);
    return [
      h("div", { class: "card stack" }, h("h3", {}, "Резервні копії"),
        driveBlock(drive),
        h("p", { class: "muted small" }, "Автоматично кожні 6 годин. Останні копії на сервері:"),
        backups.slice(0, 5).map((b) => h("div", { class: "sum-line small" }, h("span", {}, b.name), h("span", { class: "muted" }, (b.size / 1024).toFixed(0), " КБ"))),
        h("button", { class: "btn", onclick: async () => { await run(() => api.post("/api/backups"), "Копію створено"); reload(); } }, "Створити копію зараз")),
      perfCard(),
    ];
  },
};

VIEWS.settings = async (main) => {
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const gq = q.get("gdrive");
  let group = q.get("g");
  if (gq) {
    const msg = { ok: "Google Drive підключено — копії зберігатимуться автоматично", cancelled: "Підключення Google Drive скасовано",
      denied: "Посилання застаріло — натисніть «Підключити» ще раз", error: "Google не дав доступ. Спробуйте ще раз" }[gq];
    if (msg) toast(msg, gq !== "ok");
    group = "data";
    history.replaceState(null, "", "#/settings?g=data");
  }
  if (group && !SETTINGS_GROUPS.some((g) => g[0] === group)) group = null;
  const phone = window.matchMedia("(max-width: 900px)").matches;
  if (!group && phone) {
    main.append(topbar("Налаштування", "Оберіть розділ"), settingsNav(null, { index: true }));
    return;
  }
  group = group || SETTINGS_GROUPS[0][0];
  const [, , label, , desc] = SETTINGS_GROUPS.find((g) => g[0] === group);
  const s = await api.get("/api/settings");
  const cards = await SETTINGS_BUILD[group](s);
  main.append(phone ? topbar("Налаштування") : topbar("Налаштування", label),
    h("div", { class: "set-layout" }, settingsNav(group),
      h("section", { class: "set-body", "data-testid": `set-body-${group}` },
        phone ? h("a", { href: "#/settings", class: "set-back small" }, icon("left", "sm"), "Усі налаштування") : null,
        h("div", { class: "set-head" }, h("h2", {}, label), h("p", { class: "muted small" }, desc)),
        h("div", { class: "stack" }, cards))));
};

function checkboxCard() {
  if (!state.user || state.user.role !== "director") return null;
  const on = h("input", { type: "checkbox", "data-testid": "cb-enabled" });
  const ptype = select([["none", "Немає"], ["wifi", "Wi-Fi (мережевий)"], ["bluetooth", "Bluetooth"]], "none", { "data-testid": "cb-printer-type" });
  const ip = h("input", { inputmode: "decimal", placeholder: "192.168.1.60", autocomplete: "off", "data-testid": "cb-printer-ip" });
  const port = h("input", { type: "number", min: 1, max: 65535, value: 9100, "data-testid": "cb-printer-port" });
  const bt = h("input", { placeholder: "Назва з налаштувань Bluetooth, напр. XP-58", autocomplete: "off", "data-testid": "cb-printer-bt" });
  const width = select([["32", "58 мм (32 символи)"], ["42", "80 мм (42 символи)"], ["48", "80 мм (48 символів)"]], "32");
  const cp = select([["cp1251", "Windows-1251 (уся українська абетка)"], ["cp866", "CP866 (без І, Ґ — замінюються)"]], "cp1251");
  const keys = h("div", { class: "small" });
  const queue = h("div", { class: "small muted" });
  const result = h("div", { class: "small", "aria-live": "polite", "data-testid": "cb-result" });
  const wifiBox = h("div", { class: "form-grid" }, field("IP-адреса принтера", ip), field("Порт", port));
  const btBox = field("Bluetooth-принтер", bt);
  const sync = () => { wifiBox.classList.toggle("hidden", ptype.value !== "wifi"); btBox.classList.toggle("hidden", ptype.value !== "bluetooth"); };
  ptype.addEventListener("change", sync);
  const fill = (c) => {
    on.checked = !!c.enabled; ptype.value = c.printer_type; ip.value = c.printer_ip || ""; port.value = c.printer_port || 9100;
    bt.value = c.printer_bt_name || ""; width.value = String(c.printer_width || 32); cp.value = c.printer_codepage || "cp1251";
    keys.replaceChildren(c.keys_configured ? h("span", { class: "pos" }, "Ключі касира задані на сервері")
      : h("span", { class: "neg" }, "Ключі не задані: MDS_CHECKBOX_LOGIN, MDS_CHECKBOX_PASSWORD, MDS_CHECKBOX_LICENSE_KEY у Railway → Variables"));
    queue.textContent = c.queue && (c.queue.pending || c.queue.error) ? `Чеки в черзі: ${c.queue.pending}, з помилкою: ${c.queue.error}` : "";
    sync();
  };
  api.get("/api/settings/checkbox").then(fill).catch(() => {});
  const busy = async (btn, fn) => { btn.disabled = true; try { await fn(); } catch (err) { result.replaceChildren(h("span", { class: "neg" }, err.message)); } btn.disabled = false; };
  const save = h("button", { class: "btn primary", "data-testid": "cb-save", onclick: (e) => busy(e.currentTarget, async () => {
    const c = await run(() => api.patch("/api/settings/checkbox", { enabled: on.checked, printer_type: ptype.value, printer_ip: ip.value.trim(),
      printer_port: parseInt(port.value || "9100", 10), printer_bt_name: bt.value.trim(), printer_width: +width.value, printer_codepage: cp.value }),
      "Налаштування Checkbox збережено");
    fiscalCfg = null;
    fill(c);
  }) }, "Зберегти налаштування");
  const check = h("button", { class: "btn", "data-testid": "cb-check", onclick: (e) => busy(e.currentTarget, async () => {
    result.replaceChildren(h("span", { class: "muted" }, "Перевіряємо Checkbox…"));
    const r = await api.post("/api/checkbox/check");
    const SH = { OPENED: "відкрита", OPENING: "відкривається", CREATED: "відкривається", CLOSING: "закривається", CLOSED: "закрита" };
    result.replaceChildren(r.ok ? h("span", { class: "pos" }, `Зв'язок є, вхід касира успішний. Зміна ПРРО: ${SH[r.shift] || r.shift}`)
      : h("span", { class: "neg" }, r.error));
  }) }, "Перевірити зв'язок");
  const shift = (action) => h("button", { class: "btn", "data-testid": `cb-shift-${action}`, onclick: (e) => busy(e.currentTarget, async () => {
    const r = await api.post("/api/checkbox/shift", { action });
    result.replaceChildren(h("span", { class: "pos" }, r.message));
  }) }, action === "open" ? "Відкрити зміну ПРРО" : "Закрити зміну ПРРО");
  const testPrint = h("button", { class: "btn", "data-testid": "cb-test-print", onclick: (e) => busy(e.currentTarget, async () => {
    const r = await api.post("/api/checkbox/test-print");
    printReceipt(r.print_base64, r.printer);
  }) }, "Тестовий друк");
  return h("div", { class: "card stack", "data-testid": "cb-card" }, h("h3", {}, "Checkbox (ПРРО)"),
    keys, h("label", { class: "row" }, on, "Фіскалізувати оплачені замовлення (чек Checkbox)"),
    field("Чековий принтер", ptype), wifiBox, btBox,
    h("div", { class: "form-grid" }, field("Ширина стрічки", width), field("Кодова сторінка", cp)),
    h("div", { class: "row" }, save, check), h("div", { class: "row" }, shift("open"), shift("close"), testPrint), result, queue,
    h("p", { class: "muted small" }, "Після закриття оплаченого замовлення створюється фіскальний чек (готівка — CASH, картка — CASHLESS). Якщо Checkbox " +
      "недоступний, замовлення все одно закривається, а чек створиться пізніше автоматично. Зміна ПРРО відкривається з першим чеком і " +
      "закривається Checkbox о 23:50. Друк — лише з планшета в застосунку MDS (принтер Wi-Fi у мережі салону або спарений Bluetooth). " +
      "Тестовий друк допоможе підібрати кодову сторінку, якщо замість літер друкуються символи."));
}

function posTerminalCard() {
  if (!state.user || state.user.role !== "director") return null;
  const on = h("input", { type: "checkbox", "data-testid": "pos-enabled" });
  const ip = h("input", { inputmode: "decimal", placeholder: "192.168.1.50", autocomplete: "off", "data-testid": "pos-ip" });
  const port = h("input", { type: "number", min: 1, max: 65535, value: 2000, "data-testid": "pos-port" });
  const result = h("div", { class: "small", "data-testid": "pos-check-result", "aria-live": "polite" });
  api.get("/api/settings/terminal").then((c) => { on.checked = !!c.enabled; ip.value = c.ip || ""; port.value = c.port || 2000; }).catch(() => {});
  const save = h("button", { class: "btn primary", "data-testid": "pos-save", onclick: async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    try {
      const c = await run(() => api.patch("/api/settings/terminal", { enabled: on.checked, ip: ip.value.trim(), port: parseInt(port.value || "2000", 10) }),
        "Налаштування термінала збережено");
      on.checked = c.enabled; ip.value = c.ip; port.value = c.port;
    } catch (_) {  } finally { b.disabled = false; }
  } }, "Зберегти налаштування");
  const check = h("button", { class: "btn", "data-testid": "pos-check", onclick: async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    result.replaceChildren(h("span", { class: "muted" }, "Перевіряємо зв'язок…"));
    try {
      const r = await api.post("/api/settings/terminal/check");
      result.replaceChildren(r.ok
        ? h("span", { class: "pos" }, `Зв'язок є: ${r.ip}:${r.port}, відповідь за ${r.ms} мс`)
        : h("span", { class: "neg" }, `${r.error} (${r.ip}:${r.port})`));
    } catch (err) { result.replaceChildren(h("span", { class: "neg" }, err.message)); }
    b.disabled = false;
  } }, "Перевірити зв'язок");
  return h("div", { class: "card stack", "data-testid": "pos-card" }, h("h3", {}, "POS Термінал"),
    h("label", { class: "row" }, on, "Увімкнути інтеграцію з терміналом"),
    h("div", { class: "form-grid" }, field("IP-адреса термінала", ip), field("Порт", port)),
    h("div", { class: "row" }, save, check), result,
    h("p", { class: "muted small" }, "Термінал ПриватБанку (PAX/Newland) у локальній мережі салону. Коли інтеграцію ввімкнено, кнопка «Картка» " +
      "надсилає суму на термінал і закриває замовлення після оплати. Вимкнено — після оплати на терміналі підтвердіть її вручну. " +
      "Сервер має бути в одній мережі з терміналом (docs/TERMINAL.md)."));
}

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

async function declarationCard(year) {
  const d = await api.get("/api/reports/declaration?year=" + year);
  const st = state.settings || await api.get("/api/settings");
  const F = [
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

  const tr = !!t.transfer;
  const row = (label, b, strong) => h("tr", strong ? { class: "total" } : {}, h("td", {}, strong ? h("b", {}, label) : label),
    h("td", { class: "num" }, money(b.cash)), h("td", { class: "num" }, money(b.card)),
    tr ? h("td", { class: "num" }, money(b.transfer)) : null, h("td", { class: "num" }, h("b", {}, money(b.total))), h("td", { class: "num" }, String(b.count)));
  const head = () => h("tr", {}, h("th", {}, ""), h("th", {}, "Готівка"), h("th", {}, "Картка"), tr ? h("th", {}, "Переказ") : null, h("th", {}, "Разом"), h("th", {}, "Замовл."));
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

  const [data, emps] = await Promise.all([api.get("/api/payroll?month=" + month), api.get("/api/employees")]);
  const staff = emps.filter((u) => u.is_employee).map((e) => ({ id: e.user_id, full_name: e.full_name }));
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
