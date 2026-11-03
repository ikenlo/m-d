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
    if (!res.ok) throw new Error((data && data.detail) || "Помилка " + res.status);
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
const PAY = { cash: "Готівка", card: "Картка", transfer: "Переказ" };
const ROLES = { director: "Директор", admin: "Адміністратор", master: "Майстер" };
const MAT_MODES = {
  salon: "Матеріали за рахунок салону",
  master: "Матеріали за рахунок майстра",
  split: "Матеріали ділимо пропорційно",
};
const EXP_CAT = { rent: "Оренда", utilities: "Комунальні", advertising: "Реклама", materials: "Закупівля матеріалів", other: "Інше" };

const chip = (st, map = STATUS) => h("span", { class: "chip st-" + st }, map[st] || st);

function toast(msg, err = false) {
  const t = h("div", { class: "toast" + (err ? " err" : "") }, msg);
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
  const wrap = h("div", { class: "seg" });
  for (const [v, t] of options) {
    wrap.append(h("button", {
      class: v === value ? "on" : "", type: "button",
      onclick: () => { [...wrap.children].forEach((b) => b.classList.remove("on")); wrap.children[options.findIndex((o) => o[0] === v)].classList.add("on"); onChange(v); },
    }, t));
  }
  return wrap;
}

function kpi(label, value, cls = "") {
  return h("div", { class: "card kpi-tile " + cls }, h("div", { class: "kpi-label" }, label),
    h("div", { class: "kpi-value" + (value < 0 ? " neg" : "") }, money(value)));
}

function emptyState(text) { return h("div", { class: "empty" }, text); }

// ============================================================ модальні вікна
function openSheet(title, ...content) {
  closeSheet();
  const ov = h("div", { class: "overlay", id: "overlay", onclick: (e) => { if (e.target === ov) closeSheet(); } },
    h("div", { class: "sheet" },
      h("div", { class: "sheet-head" }, h("h2", {}, title), h("button", { class: "x", onclick: closeSheet, "aria-label": "Закрити" }, "×")),
      ...content));
  document.body.append(ov);
  return ov;
}
function closeSheet() { const o = document.getElementById("overlay"); if (o) o.remove(); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

function confirmSheet(title, text, okText, onOk, { danger = false, withReason = false } = {}) {
  const reason = h("input", { placeholder: "Причина" });
  openSheet(title,
    h("p", {}, text),
    withReason ? field("Причина", reason) : null,
    h("div", { class: "row", style: { marginTop: "16px" } },
      h("button", { class: "btn ghost grow", onclick: closeSheet }, "Назад"),
      h("button", { class: "btn grow " + (danger ? "danger" : "gold"), onclick: async (e) => {
        e.target.disabled = true;
        try { await onOk(reason.value.trim()); closeSheet(); } catch (_) { e.target.disabled = false; }
      } }, okText)));
}

// ============================================================ каркас
const NAV = {
  master: [
    ["today", "◷", "Сьогодні"], ["calendar", "▦", "Календар"], ["earnings", "₴", "Заробіток"],
    ["shift", "✓", "Зміна"], ["profile", "◉", "Профіль"],
  ],
  admin: [["calendar", "▦", "Календар"], ["clients", "☺", "Клієнти"], ["profile", "◉", "Профіль"]],
  director: [
    ["dashboard", "◆", "Панель"], ["calendar", "▦", "Календар"], ["shifts", "✓", "Зміни й виплати"],
    ["orders", "≡", "Замовлення"], ["expenses", "−", "Витрати"], ["clients", "☺", "Клієнти"],
    ["staff", "♛", "Персонал"], ["catalog", "✦", "Послуги"], ["settings", "⚙", "Налаштування"],
    ["journal", "☰", "Журнал"], ["profile", "◉", "Профіль"],
  ],
};
const MOBILE_TABS = { director: ["dashboard", "calendar", "shifts", "orders", "more"] };

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

function renderLogin() {
  window.onhashchange = null;
  const user = h("input", { autocomplete: "username", autocapitalize: "none", placeholder: "логін" });
  const pass = h("input", { type: "password", autocomplete: "current-password", placeholder: "пароль" });
  const btn = h("button", { class: "btn gold big", type: "submit" }, "Увійти");
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
    h("div", { class: "login" }, h("div", { class: "card" },
      h("div", { class: "brand" }, h("div", { class: "brand-mark" }, "M&D"),
        h("div", {}, h("div", { class: "brand-name" }, "MDS Connect"), h("div", { class: "brand-sub" }, "Beauty salon M&D"))),
      form)));
  user.focus();
}

function renderApp() {
  const role = state.user.role;
  const items = NAV[role];
  let route = currentRoute();
  if (route === "more") route = "more";
  else if (!items.find((i) => i[0] === route)) route = items[0][0];

  const nav = h("nav", { class: "nav" }, items.map(([id, ico, label]) =>
    h("a", { href: "#/" + id, class: id === route ? "active" : "" }, h("span", { class: "ico" }, ico), label)));
  const sidebar = h("aside", { class: "sidebar" },
    h("div", { class: "brand" }, h("div", { class: "brand-mark" }, "M&D"),
      h("div", {}, h("div", { class: "brand-name" }, "MDS Connect"), h("div", { class: "brand-sub" }, ROLES[role]))),
    nav,
    h("div", { class: "muted small", style: { padding: "18px 12px" } }, state.user.full_name));

  const tabIds = MOBILE_TABS[role] || items.map((i) => i[0]);
  const tabbar = h("nav", { class: "tabbar" }, tabIds.map((id) => {
    const it = items.find((i) => i[0] === id) || ["more", "⋯", "Ще"];
    const active = id === route || (id === "more" && !tabIds.includes(route));
    return h("a", { href: "#/" + id, class: active ? "active" : "" }, h("span", { class: "ico" }, it[1]), it[2]);
  }));
  const main = h("main", { class: "main" });
  document.getElementById("root").replaceChildren(h("div", { class: "app" }, sidebar, main), tabbar);
  closeSheet();
  const view = route === "more" ? viewMore : VIEWS[route];
  Promise.resolve(view(main)).catch((e) => { main.replaceChildren(emptyState(e.message)); });
}

function topbar(title, ...actions) {
  return h("div", { class: "topbar" }, h("h1", {}, title), h("div", { class: "row" }, actions));
}

function viewMore(main) {
  main.append(topbar("Меню"));
  main.append(h("div", { class: "card nav" }, NAV[state.user.role].map(([id, ico, label]) =>
    h("a", { href: "#/" + id }, h("span", { class: "ico" }, ico), label))));
}

const reload = () => renderApp();

// ============================================================ спільні форми
function apptSheet(a, onChange) {
  const isMgr = state.user.role !== "master";
  const canEdit = ["pending", "confirmed", "in_progress"].includes(a.status);
  const actions = h("div", { class: "grid", style: { gridTemplateColumns: "1fr 1fr", marginTop: "16px" } });
  const done = async (p, msg) => { await run(p, msg); closeSheet(); onChange(); };
  if (canEdit) {
    if (a.status !== "in_progress") {
      actions.append(h("button", { class: "btn", onclick: () => done(api.post(`/api/appointments/${a.id}/status`, { status: "in_progress" }), "Клієнта прийнято") }, "Прийняти клієнта"));
    }
    actions.append(h("button", { class: "btn gold", onclick: () => closeOrderSheet(a, onChange) }, "Закрити замовлення"));
    if (a.status === "pending") {
      actions.append(h("button", { class: "btn", onclick: () => done(api.post(`/api/appointments/${a.id}/status`, { status: "confirmed" }), "Підтверджено") }, "Підтвердити"));
    }
    actions.append(h("button", { class: "btn", onclick: () => apptForm({ appt: a }, onChange) }, "Перенести / змінити"));
    if (a.status !== "in_progress") {
      actions.append(h("button", { class: "btn ghost", onclick: () => confirmSheet("Клієнт не прийшов?", `${a.client.name}, ${hm(a.starts_at)}`, "Так, не прийшов",
        async () => { await run(api.post(`/api/appointments/${a.id}/status`, { status: "no_show" })); onChange(); }) }, "Не прийшов"));
    }
    actions.append(h("button", { class: "btn danger", onclick: () => confirmSheet("Скасувати запис?", `${a.client.name}, ${dm(a.starts_at)} ${hm(a.starts_at)}`, "Скасувати запис",
      async (reason) => { await run(api.post(`/api/appointments/${a.id}/status`, { status: "cancelled", reason: reason || null }), "Запис скасовано"); onChange(); },
      { danger: true, withReason: true }) }, "Скасувати"));
  }
  openSheet(`${hm(a.starts_at)}–${hm(a.ends_at)} · ${dm(a.starts_at)}`,
    h("div", { class: "stack" },
      h("div", { class: "row" }, h("h3", { style: { margin: 0 } }, a.service_name), chip(a.status)),
      h("div", {}, "Клієнт: ", h("b", {}, a.client.name), " · ", h("a", { href: "tel:" + a.client.phone }, a.client.phone)),
      h("div", {}, "Майстер: ", a.master_name),
      h("div", {}, "Ціна: ", h("b", {}, money(a.price)), " · ", a.duration_min, " хв",
        a.source === "telegram" ? h("span", { class: "chip", style: { marginLeft: "8px" } }, "Telegram") : null),
      a.comment ? h("div", { class: "muted" }, "Коментар: ", a.comment) : null,
      a.cancel_reason ? h("div", { class: "muted" }, "Причина скасування: ", a.cancel_reason) : null,
      a.order_id ? h("div", { class: "muted" }, "Замовлення #", a.order_id) : null,
      h("button", { class: "btn ghost sm", onclick: () => clientSheet(a.client.id) }, "Історія клієнта")),
    actions);
}

function closeOrderSheet(a, onChange) {
  const key = uuid(); // ідемпотентність: повторне натискання не задвоїть оплату
  let method = null;
  const mats = new Map(); // material_id -> qty
  const custom = [];
  const amount = h("input", { class: "huge", inputmode: "decimal", value: kopToInput(a.price) });
  const methods = h("div", { class: "choice" }, Object.entries(PAY).map(([k, t]) =>
    h("button", { type: "button", onclick: (e) => { method = k; [...methods.children].forEach((b) => b.classList.remove("on")); e.currentTarget.classList.add("on"); } }, t)));

  const matList = h("div");
  const drawMats = () => {
    matList.replaceChildren(...state.materials.map((m) => {
      const q = mats.get(m.id) || 0;
      const qty = h("span", { style: { minWidth: "22px", textAlign: "center" } }, q);
      return h("div", { class: "mat-row" },
        h("div", { class: "grow" }, m.name, h("div", { class: "muted small" }, money(m.unit_cost), " / ", m.unit)),
        h("div", { class: "qty" },
          h("button", { type: "button", onclick: () => { mats.set(m.id, Math.max(0, q - 1)); drawMats(); } }, "−"), qty,
          h("button", { type: "button", onclick: () => { mats.set(m.id, q + 1); drawMats(); } }, "+")));
    }), ...custom.map((c, i) => h("div", { class: "mat-row" }, h("div", { class: "grow" }, c.name), money(c.unit_cost),
      h("button", { class: "btn sm ghost", type: "button", onclick: () => { custom.splice(i, 1); drawMats(); } }, "×"))));
  };
  drawMats();
  const cName = h("input", { placeholder: "Інший матеріал" });
  const cCost = h("input", { placeholder: "Собівартість, ₴", inputmode: "decimal" });
  const matsBox = h("details", {},
    h("summary", { class: "muted", style: { cursor: "pointer", padding: "6px 0" } }, "Використані матеріали (за потреби)"),
    matList,
    h("div", { class: "row", style: { marginTop: "8px" } }, h("div", { class: "grow" }, cName), h("div", { style: { width: "130px" } }, cCost),
      h("button", { class: "btn sm", type: "button", onclick: () => {
        const cost = toKop(cCost.value);
        if (!cName.value.trim() || !(cost >= 0)) return toast("Вкажіть назву і собівартість", true);
        custom.push({ name: cName.value.trim(), unit_cost: cost, qty: 1 }); cName.value = ""; cCost.value = ""; drawMats();
      } }, "Додати")));

  const btn = h("button", { class: "btn gold big", onclick: async () => {
    const kop = toKop(amount.value);
    if (!(kop >= 0)) return toast("Вкажіть фактичну вартість", true);
    if (!method) return toast("Оберіть спосіб оплати", true);
    const materials = [...mats.entries()].filter(([, q]) => q > 0).map(([id, q]) => ({ material_id: id, qty: q })).concat(custom);
    btn.disabled = true;
    try {
      const o = await run(() => api.post(`/api/appointments/${a.id}/close`, { amount: kop, payment_method: method, materials, idempotency_key: key }));
      closeSheet();
      toast(`Замовлення закрито. Ваш заробіток: ${money(o.master_earning)}`);
      onChange();
    } catch (_) { btn.disabled = false; }
  } }, "Закрити замовлення");

  openSheet("Закрити замовлення",
    h("div", { class: "muted", style: { marginBottom: "10px" } }, `${a.service_name} · ${a.client.name}`),
    h("div", { class: "stack" }, field("Фактична вартість, ₴", amount), field("Спосіб оплати", methods), matsBox, btn));
  amount.select();
}

function apptForm({ appt = null, master_id = null, date = today(), time = "10:00" } = {}, onChange) {
  const isMaster = state.user.role === "master";
  const masters = isMaster ? [{ id: state.user.id, full_name: state.user.full_name }] : state.masters;
  const activeServices = state.services.filter((s) => s.is_active);
  const svc = select(activeServices.map((s) => [s.id, `${s.name} · ${money(s.price)} · ${s.duration_min} хв`]), appt ? appt.service_id : activeServices[0]?.id);
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
        if (list.length === 1) { clientId = list[0].id; name.value = list[0].name; found.textContent = "Постійний клієнт: " + list[0].name; }
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

// ---------- Сьогодні (майстер)
VIEWS.today = async (main) => {
  const d = today();
  const [list, my, shift] = await Promise.all([
    api.get(`/api/appointments?date_from=${d}`), api.get("/api/finance/my"), api.get("/api/shifts/current"),
  ]);
  main.append(topbar(`${WD_LONG[(new Date().getDay() + 6) % 7]}, ${dm(d)}`,
    h("button", { class: "btn gold", onclick: () => apptForm({ date: d }, reload) }, "+ Запис")));
  main.append(h("div", { class: "grid kpi", style: { marginBottom: "14px" } },
    kpi("Заробіток сьогодні", my.day.earning, "accent"),
    kpi("Виручка сьогодні", my.day.revenue),
    h("div", { class: "card kpi-tile" }, h("div", { class: "kpi-label" }, "Зміна"),
      h("div", { class: "kpi-value", style: { fontSize: "20px" } }, shift ? "Відкрита з " + hm(shift.opened_at) : "Не відкрита"))));
  const active = list.filter((a) => !["cancelled", "no_show"].includes(a.status));
  if (!active.length) main.append(h("div", { class: "card" }, emptyState("На сьогодні записів немає")));
  for (const a of active) {
    const done = a.status === "completed";
    main.append(h("div", { class: "card appt" + (done ? " done" : ""), onclick: () => (done ? null : apptSheet(a, reload)) },
      h("div", { class: "appt-time" }, hm(a.starts_at), h("small", {}, "до " + hm(a.ends_at))),
      h("div", { class: "appt-body" },
        h("div", { class: "row" }, h("span", { class: "appt-title grow" }, a.client.name), chip(a.status)),
        h("div", {}, h("a", { href: "tel:" + a.client.phone, onclick: (e) => e.stopPropagation() }, a.client.phone)),
        h("div", { class: "muted" }, a.service_name, " · ", money(a.price))),
      done ? null : h("button", { class: "btn gold", style: { alignSelf: "center" }, onclick: (e) => { e.stopPropagation(); closeOrderSheet(a, reload); } }, "Закрити")));
  }
};

// ---------- Календар
VIEWS.calendar = async (main) => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  let day = params.get("d") || today();
  const isMaster = state.user.role === "master";
  const go = (dd) => { location.hash = "#/calendar?d=" + dd; };
  const dateInput = h("input", { type: "date", value: day, style: { width: "auto" }, onchange: (e) => go(e.target.value) });
  main.append(topbar("Календар",
    h("button", { class: "btn", onclick: () => go(addDays(day, -1)) }, "‹"),
    dateInput,
    h("button", { class: "btn", onclick: () => go(addDays(day, 1)) }, "›"),
    h("button", { class: "btn ghost", onclick: () => go(today()) }, "Сьогодні"),
    h("button", { class: "btn gold", onclick: () => apptForm({ date: day }, reload) }, "+ Запис")));

  const list = await api.get(`/api/appointments?date_from=${day}`);
  const masters = isMaster ? [{ id: state.user.id, full_name: state.user.full_name, schedule: state.user.schedule }] : state.masters;
  if (!masters.length) { main.append(h("div", { class: "card" }, emptyState("Додайте майстрів у розділі «Персонал»"))); return; }

  const wd = String((new Date(day + "T12:00").getDay() + 6) % 7);
  const salonSched = state.settings.schedule || {};
  const spanOf = (m) => ((m.schedule || salonSched)[wd] || null);
  const toMin = (s) => { const [a, b] = s.split(":").map(Number); return a * 60 + b; };
  let startMin = 24 * 60, endMin = 0;
  for (const m of masters) { const sp = spanOf(m); if (sp) { startMin = Math.min(startMin, toMin(sp[0])); endMin = Math.max(endMin, toMin(sp[1])); } }
  for (const a of list) { startMin = Math.min(startMin, toMin(hm(a.starts_at))); endMin = Math.max(endMin, toMin(hm(a.ends_at))); }
  if (startMin >= endMin) { startMin = 9 * 60; endMin = 20 * 60; }
  startMin = Math.floor(startMin / 30) * 30; endMin = Math.ceil(endMin / 30) * 30;
  const STEP = 30, ROW = 44, HEAD = 42, TIMEW = 56;
  const rows = (endMin - startMin) / STEP;
  const colW = isMaster ? "minmax(220px, 1fr)" : "minmax(170px, 1fr)";

  const grid = h("div", { class: "cal-grid", style: { gridTemplateColumns: `${TIMEW}px repeat(${masters.length}, ${colW})`, gridTemplateRows: `${HEAD}px repeat(${rows}, ${ROW}px)` } });
  grid.append(h("div", { class: "cal-head", style: { borderLeft: 0 } }, weekday(day)));
  masters.forEach((m) => grid.append(h("div", { class: "cal-head" }, m.full_name)));
  for (let r = 0; r < rows; r++) {
    const min = startMin + r * STEP;
    const time = `${pad(Math.floor(min / 60))}:${pad(min % 60)}`;
    grid.append(h("div", { class: "cal-time" }, min % 60 === 0 ? time : ""));
    masters.forEach((m) => {
      const sp = spanOf(m);
      const off = !sp || min < toMin(sp[0]) || min >= toMin(sp[1]);
      grid.append(h("div", { class: "cal-cell" + (off ? " off" : ""), title: off ? "Неробочий час" : "Новий запис " + time,
        onclick: () => apptForm({ master_id: m.id, date: day, time }, reload) }));
    });
  }
  const wrap = h("div", { class: "cal" }, grid);
  main.append(wrap);
  // події позиціонуємо після рендеру, коли відомі ширини колонок
  requestAnimationFrame(() => {
    const heads = grid.querySelectorAll(".cal-head");
    list.filter((a) => !["cancelled", "no_show"].includes(a.status)).forEach((a) => {
      const col = masters.findIndex((m) => m.id === a.master_id);
      if (col < 0) return;
      const head = heads[col + 1];
      const top = HEAD + ((toMin(hm(a.starts_at)) - startMin) / STEP) * ROW;
      const height = Math.max(((toMin(hm(a.ends_at)) - toMin(hm(a.starts_at))) / STEP) * ROW - 3, 22);
      grid.append(h("div", { class: "cal-ev " + a.status, style: { top: top + 1 + "px", height: height + "px", left: head.offsetLeft + 3 + "px", width: head.offsetWidth - 6 + "px" },
        onclick: () => apptSheet(a, reload) },
        h("b", {}, hm(a.starts_at), " ", a.client.name), a.service_name, a.status === "pending" ? " · очікує" : ""));
    });
  });
  const cancelled = list.filter((a) => ["cancelled", "no_show"].includes(a.status));
  if (cancelled.length) {
    main.append(h("details", { class: "card", style: { marginTop: "12px" } },
      h("summary", { class: "muted", style: { cursor: "pointer" } }, `Скасовані та неявки: ${cancelled.length}`),
      cancelled.map((a) => h("div", { class: "row", style: { padding: "6px 0" } }, hm(a.starts_at), a.client.name, h("span", { class: "muted" }, a.master_name, " · ", a.service_name), chip(a.status)))));
  }
};

// ---------- Заробіток (майстер)
VIEWS.earnings = async (main) => {
  const [my, orders, payouts] = await Promise.all([api.get("/api/finance/my"), api.get("/api/orders?period=month"), api.get("/api/payouts")]);
  main.append(topbar("Мій заробіток"));
  main.append(h("div", { class: "grid kpi" },
    kpi("Сьогодні", my.day.earning, "accent"), kpi("Цей тиждень", my.week.earning), kpi("Цей місяць", my.month.earning),
    kpi("До виплати", my.balance.balance, "accent")));
  main.append(h("div", { class: "card", style: { marginTop: "12px" } },
    h("div", { class: "sum-line" }, h("span", {}, "Нараховано за весь час"), h("b", {}, money(my.balance.accrued))),
    h("div", { class: "sum-line" }, h("span", {}, "Виплачено"), h("b", {}, money(my.balance.paid))),
    h("div", { class: "sum-line total" }, h("span", {}, "Залишок до виплати"), h("span", {}, money(my.balance.balance)))));
  main.append(h("h2", { style: { marginTop: "22px" } }, "Замовлення цього місяця"));
  main.append(orders.length ? h("div", { class: "card table-wrap" }, h("table", {},
    h("tr", {}, h("th", {}, "Дата"), h("th", {}, "Послуга"), h("th", {}, "Клієнт"), h("th", { class: "num" }, "Сума"), h("th", { class: "num" }, "Мій заробіток")),
    orders.map((o) => h("tr", {}, h("td", {}, dm(o.closed_at), " ", hm(o.closed_at)), h("td", {}, o.service_name), h("td", {}, o.client_name),
      h("td", { class: "num" }, money(o.amount)), h("td", { class: "num" }, h("b", {}, money(o.master_earning))))))) : h("div", { class: "card" }, emptyState("Ще немає закритих замовлень")));
  if (payouts.length) {
    main.append(h("h2", { style: { marginTop: "22px" } }, "Виплати"));
    main.append(h("div", { class: "card table-wrap" }, h("table", {},
      payouts.map((p) => h("tr", {}, h("td", {}, dmy(p.paid_at)), h("td", {}, PAY[p.method]), h("td", { class: "muted" }, p.note || ""), h("td", { class: "num" }, money(p.amount)))))));
  }
};

function shiftReport(r) {
  return h("div", { class: "stack" },
    h("div", { class: "grid kpi" },
      h("div", { class: "card kpi-tile" }, h("div", { class: "kpi-label" }, "Клієнтів"), h("div", { class: "kpi-value" }, r.clients_count)),
      h("div", { class: "card kpi-tile" }, h("div", { class: "kpi-label" }, "Послуг"), h("div", { class: "kpi-value" }, r.services_count)),
      h("div", { class: "card kpi-tile" }, h("div", { class: "kpi-label" }, "Скасувань"), h("div", { class: "kpi-value" }, r.cancellations))),
    h("div", { class: "card" },
      (r.services || []).map((s) => h("div", { class: "sum-line" }, h("span", {}, s.service, " × ", s.count), h("span", {}, money(s.revenue)))),
      h("div", { class: "divider" }),
      h("div", { class: "sum-line" }, h("span", {}, "Загальна виручка"), h("b", {}, money(r.revenue))),
      Object.entries(r.by_payment || {}).map(([k, v]) => h("div", { class: "sum-line muted small" }, h("span", {}, PAY[k] || k), h("span", {}, money(v)))),
      h("div", { class: "sum-line" }, h("span", {}, "Заробіток майстра"), h("b", {}, money(r.master_earning))),
      h("div", { class: "sum-line total" }, h("span", {}, "Сума до виплати"), h("span", {}, money(r.payout_due)))));
}

// ---------- Зміна (майстер)
VIEWS.shift = async (main) => {
  const [cur, history] = await Promise.all([api.get("/api/shifts/current"), api.get("/api/shifts")]);
  main.append(topbar("Моя зміна"));
  if (cur) {
    main.append(h("p", { class: "muted" }, "Відкрита з ", dmy(cur.opened_at), " ", hm(cur.opened_at)));
    main.append(shiftReport(cur.report));
    main.append(h("button", { class: "btn gold big", style: { marginTop: "14px" }, onclick: () => confirmSheet("Закрити зміну?",
      "Буде сформовано звіт, директор отримає його на підтвердження виплати.", "Закрити зміну", async () => {
        const sh = await run(() => api.post("/api/shifts/close"), "Зміну закрито");
        openSheet("Звіт зміни", shiftReport(sh.report));
        renderApp();
      }) }, "Закрити зміну"));
  } else {
    main.append(h("div", { class: "card" }, h("p", {}, "Зміна відкриється автоматично при першому закритому замовленні."),
      h("button", { class: "btn", onclick: async () => { await run(() => api.post("/api/shifts/open"), "Зміну відкрито"); reload(); } }, "Відкрити зміну зараз")));
  }
  const past = history.filter((s) => s.status !== "open");
  if (past.length) {
    main.append(h("h2", { style: { marginTop: "22px" } }, "Попередні зміни"));
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
  const oldP = h("input", { type: "password", autocomplete: "current-password" });
  const newP = h("input", { type: "password", autocomplete: "new-password" });
  const code = h("div", { class: "kpi-value" });
  main.append(h("div", { class: "grid two" },
    h("div", { class: "card stack" }, h("h3", {}, u.full_name), h("div", { class: "muted" }, ROLES[u.role], " · ", u.username),
      u.role === "master" && u.share_bp !== null ? h("div", {}, "Ваш відсоток: ", pct(u.share_bp)) : null,
      h("button", { class: "btn danger", onclick: logout }, "Вийти")),
    h("div", { class: "card stack" }, h("h3", {}, "Сповіщення в Telegram"),
      h("p", { class: "muted" }, u.telegram_linked ? "Telegram підключено ✓" : "Отримайте код і надішліть боту салону: /link КОД"),
      code,
      h("button", { class: "btn", onclick: async () => { const r = await run(() => api.post("/api/auth/telegram-code")); code.textContent = "/link " + r.code; } }, "Отримати код")),
    h("div", { class: "card stack" }, h("h3", {}, "Змінити пароль"), field("Поточний пароль", oldP), field("Новий пароль (8+ символів, літери й цифри)", newP),
      h("button", { class: "btn gold", onclick: async () => {
        await run(() => api.post("/api/auth/change-password", { old_password: oldP.value, new_password: newP.value }), "Пароль змінено, увійдіть знову");
        logout();
      } }, "Змінити пароль"))));
};

// ---------- Клієнти
VIEWS.clients = async (main) => {
  main.append(topbar("Клієнти"));
  const q = h("input", { placeholder: "Пошук за ім'ям або телефоном", type: "search" });
  const box = h("div", { class: "card table-wrap", style: { marginTop: "12px" } });
  main.append(q, box);
  let timer;
  const load = async () => {
    const list = await api.get("/api/clients?q=" + encodeURIComponent(q.value.trim()));
    box.replaceChildren(list.length ? h("table", {}, h("tr", {}, h("th", {}, "Ім'я"), h("th", {}, "Телефон"), h("th", {}, "")),
      list.map((c) => h("tr", { class: "click", onclick: () => clientSheet(c.id) }, h("td", {}, c.name), h("td", {}, c.phone),
        h("td", {}, c.telegram_linked ? h("span", { class: "chip" }, "Telegram") : "")))) : emptyState("Нікого не знайдено"));
  };
  q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 300); });
  await load();
};

// ---------- Панель директора
function periodPicker(onChange, initial = "month") {
  const from = h("input", { type: "date", style: { width: "auto" } });
  const to = h("input", { type: "date", style: { width: "auto" } });
  const custom = h("span", { class: "row hidden" }, from, "—", to,
    h("button", { class: "btn sm", onclick: () => from.value && onChange(`date_from=${from.value}&date_to=${to.value || from.value}`) }, "OK"));
  const s = seg([["day", "День"], ["week", "Тиждень"], ["month", "Місяць"], ["year", "Рік"], ["custom", "Період…"]], initial, (v) => {
    custom.classList.toggle("hidden", v !== "custom");
    if (v !== "custom") onChange("period=" + v);
  });
  return h("div", { class: "row" }, s, custom);
}

VIEWS.dashboard = async (main) => {
  const body = h("div");
  main.append(topbar("Фінансова панель"), periodPicker((q) => load(q)), body);
  const load = async (q) => {
    body.replaceChildren(h("div", { class: "empty" }, "Завантаження…"));
    const d = await api.get("/api/finance/dashboard?" + q);
    const s = d.summary;
    const maxDay = Math.max(1, ...d.by_day.map((x) => x.revenue));
    body.replaceChildren(...[
      h("p", { class: "muted" }, `${dmy(s.from)} — ${dmy(s.to)} · замовлень: ${s.orders_count} · клієнтів: ${s.clients_count} · середній чек: ${money(s.average_check)} · скасувань: ${s.cancellations}`),
      h("div", { class: "grid kpi" },
        kpi("Виручка салону", s.revenue, "accent"), kpi("Частка майстрів", s.masters_share), kpi("Частка салону", s.salon_share),
        kpi("Витрати на матеріали", s.materials), kpi("Оренда", s.expenses.rent), kpi("Комунальні", s.expenses.utilities),
        kpi("Реклама", s.expenses.advertising), kpi("Інші витрати", s.expenses.other),
        kpi("Валовий прибуток", s.gross_profit, "accent"), kpi("Чистий прибуток", s.net_profit, "accent")),
      h("div", { class: "grid two", style: { marginTop: "12px" } },
        h("div", { class: "card" }, h("h3", {}, "Зарплата майстрів"),
          h("div", { class: "sum-line" }, h("span", {}, "Нараховано за період"), h("b", {}, money(s.masters_share))),
          h("div", { class: "sum-line" }, h("span", {}, "Фактично виплачено за період"), h("b", {}, money(s.payouts))),
          h("div", { class: "sum-line total" }, h("span", {}, "Загальний борг по зарплаті (на сьогодні)"), h("span", {}, money(s.salary_debt_total)))),
        h("div", { class: "card" }, h("h3", {}, "Оплати клієнтів"),
          Object.keys(PAY).map((k) => h("div", { class: "sum-line" }, h("span", {}, PAY[k]), h("b", {}, money(s.by_payment[k] || 0)))),
          h("div", { class: "sum-line muted small" }, h("span", {}, "Закупівля матеріалів (грошовий потік)"), h("span", {}, money(s.expenses.materials_purchases))))),
      d.by_day.length > 1 ? h("div", { class: "card", style: { marginTop: "12px" } }, h("h3", {}, "Виручка по днях"),
        h("div", { class: "bars" }, d.by_day.map((x) => h("div", { style: { height: (x.revenue / maxDay) * 100 + "%" }, "data-tip": `${dm(x.date)}: ${money(x.revenue)}` })))) : null,
      h("h2", { style: { marginTop: "22px" } }, "По майстрах"),
      h("div", { class: "card table-wrap" }, h("table", {},
        h("tr", {}, h("th", {}, "Майстер"), h("th", { class: "num" }, "Послуг"), h("th", { class: "num" }, "Виручка"), h("th", { class: "num" }, "Нараховано"), h("th", { class: "num" }, "Виплачено"), h("th", { class: "num" }, "Борг")),
        d.by_master.map((m) => h("tr", {}, h("td", {}, m.master_name), h("td", { class: "num" }, m.orders_count), h("td", { class: "num" }, money(m.revenue)),
          h("td", { class: "num" }, money(m.masters_share)), h("td", { class: "num" }, money(m.paid)), h("td", { class: "num" }, h("b", {}, money(m.balance))))))),
      h("h2", { style: { marginTop: "22px" } }, "По послугах"),
      d.by_service.length ? h("div", { class: "card table-wrap" }, h("table", {},
        h("tr", {}, h("th", {}, "Послуга"), h("th", { class: "num" }, "К-сть"), h("th", { class: "num" }, "Виручка"), h("th", { class: "num" }, "Майстрам"), h("th", { class: "num" }, "Матеріали"), h("th", { class: "num" }, "Салону")),
        d.by_service.map((x) => h("tr", {}, h("td", {}, x.service_name), h("td", { class: "num" }, x.orders_count), h("td", { class: "num" }, money(x.revenue)),
          h("td", { class: "num" }, money(x.masters_share)), h("td", { class: "num" }, money(x.materials)), h("td", { class: "num" }, money(x.salon_net_from_orders))))))
        : h("div", { class: "card" }, emptyState("Немає замовлень за період")),
    ].filter(Boolean));
  };
  await load("period=month");
};

// ---------- Зміни й виплати
VIEWS.shifts = async (main) => {
  const [closed, all, payouts] = await Promise.all([api.get("/api/shifts?status=closed"), api.get("/api/shifts"), api.get("/api/payouts")]);
  main.append(topbar("Зміни й виплати", h("button", { class: "btn", onclick: () => payoutForm(null) }, "Інша виплата / аванс")));
  main.append(h("h2", {}, "Очікують підтвердження виплати"));
  if (!closed.length) main.append(h("div", { class: "card" }, emptyState("Немає закритих змін, що очікують виплати")));
  for (const s of closed) {
    main.append(h("div", { class: "card row" },
      h("div", { class: "grow" }, h("b", {}, s.master_name), h("div", { class: "muted small" }, dmy(s.opened_at), " ", hm(s.opened_at), " — ", s.closed_at ? hm(s.closed_at) : ""),
        h("div", { class: "small" }, `Клієнтів: ${s.report.clients_count} · послуг: ${s.report.services_count} · виручка ${money(s.report.revenue)} · скасувань: ${s.report.cancellations}`)),
      h("div", { class: "kpi-value" }, money(s.payout_due)),
      h("button", { class: "btn ghost", onclick: () => openShift(s.id) }, "Звіт"),
      h("button", { class: "btn gold", onclick: () => payoutForm(s) }, "Підтвердити виплату")));
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
    const method = select(Object.entries(PAY), "cash");
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
      h("tr", {}, h("th", {}, "#"), h("th", {}, "Дата"), h("th", {}, "Майстер"), h("th", {}, "Послуга"), h("th", {}, "Клієнт"), h("th", {}, "Оплата"),
        h("th", { class: "num" }, "Сума"), h("th", { class: "num" }, "Матеріали"), h("th", { class: "num" }, "Майстру"), h("th", { class: "num" }, "Салону")),
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
  const method = select(Object.entries(PAY), o.payment_method);
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
    const svcBox = h("div", { class: "row" }, state.services.filter((s) => s.is_active).map((s) => h("label", { class: "row small", style: { gap: "4px" } },
      h("input", { type: "checkbox", checked: svcIds.has(s.id), onchange: (e) => (e.target.checked ? svcIds.add(s.id) : svcIds.delete(s.id)) }), s.name)));
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
      h("details", {}, h("summary", { class: "muted", style: { cursor: "pointer" } }, "Послуги майстра (порожньо = усі)"), svcBox),
      h("details", {}, h("summary", { class: "muted", style: { cursor: "pointer" } }, "Графік роботи"),
        h("label", { class: "row small", style: { margin: "8px 0" } }, useOwn, "Індивідуальний графік (інакше — графік салону)"), schedBox),
      isNew ? null : h("label", { class: "row" }, active, "Активний (може входити в систему)"),
      isNew ? null : h("div", { class: "row" }, h("button", { class: "btn sm", onclick: async () => {
        const r = await run(() => api.post(`/api/users/${u.id}/telegram-code`)); code.textContent = `Код для Telegram: /link ${r.code}`;
      } }, "Код для Telegram"), code),
      save));
  }
};

// ---------- Послуги та матеріали
VIEWS.catalog = async (main) => {
  const [services, materials] = await Promise.all([api.get("/api/services?all=true"), api.get("/api/materials?all=true")]);
  main.append(topbar("Послуги та матеріали", h("button", { class: "btn gold", onclick: () => svcForm(null) }, "+ Послуга"), h("button", { class: "btn", onclick: () => matForm(null) }, "+ Матеріал")));
  main.append(h("h2", {}, "Послуги"), h("div", { class: "card table-wrap" }, h("table", {},
    h("tr", {}, h("th", {}, "Назва"), h("th", {}, "Категорія"), h("th", { class: "num" }, "Тривалість"), h("th", { class: "num" }, "Ціна"), h("th", {})),
    services.map((s) => h("tr", { class: "click", onclick: () => svcForm(s) }, h("td", {}, s.name), h("td", {}, s.category || ""), h("td", { class: "num" }, s.duration_min, " хв"),
      h("td", { class: "num" }, money(s.price)), h("td", {}, s.is_active ? "" : h("span", { class: "chip" }, "прихована")))))));
  main.append(h("h2", { style: { marginTop: "22px" } }, "Матеріали та розхідники"), materials.length ? h("div", { class: "card table-wrap" }, h("table", {},
    h("tr", {}, h("th", {}, "Назва"), h("th", {}, "Одиниця"), h("th", { class: "num" }, "Собівартість"), h("th", {})),
    materials.map((m) => h("tr", { class: "click", onclick: () => matForm(m) }, h("td", {}, m.name), h("td", {}, m.unit), h("td", { class: "num" }, money(m.unit_cost)),
      h("td", {}, m.is_active ? "" : h("span", { class: "chip" }, "прихований")))))) : h("div", { class: "card" }, emptyState("Додайте матеріали, щоб майстри могли швидко їх обирати")));

  function svcForm(s) {
    const name = h("input", { value: s?.name || "" });
    const cat = h("input", { value: s?.category || "", placeholder: "Нігті, Волосся…" });
    const dur = h("input", { type: "number", min: 5, step: 5, value: s?.duration_min || 60 });
    const price = h("input", { inputmode: "decimal", value: s ? kopToInput(s.price) : "" });
    const order = h("input", { type: "number", value: s?.sort_order || 0 });
    const active = h("input", { type: "checkbox", checked: s ? s.is_active : true });
    openSheet(s ? s.name : "Нова послуга", h("div", { class: "stack" }, field("Назва", name), field("Категорія", cat),
      h("div", { class: "form-grid" }, field("Тривалість, хв", dur), field("Ціна, ₴", price), field("Порядок", order)),
      h("label", { class: "row" }, active, "Доступна для запису"),
      h("button", { class: "btn gold big", onclick: async () => {
        const body = { name: name.value, category: cat.value || null, duration_min: +dur.value, price: toKop(price.value), sort_order: +order.value, is_active: active.checked };
        await run(() => (s ? api.patch("/api/services/" + s.id, body) : api.post("/api/services", body)), "Збережено");
        closeSheet(); await loadRefs(); reload();
      } }, "Зберегти")));
  }
  function matForm(m) {
    const name = h("input", { value: m?.name || "" });
    const unit = h("input", { value: m?.unit || "шт" });
    const cost = h("input", { inputmode: "decimal", value: m ? kopToInput(m.unit_cost) : "" });
    const active = h("input", { type: "checkbox", checked: m ? m.is_active : true });
    openSheet(m ? m.name : "Новий матеріал", h("div", { class: "stack" }, field("Назва", name),
      h("div", { class: "form-grid" }, field("Одиниця", unit), field("Собівартість за одиницю, ₴", cost)),
      h("label", { class: "row" }, active, "Активний"),
      h("button", { class: "btn gold big", onclick: async () => {
        const body = { name: name.value, unit: unit.value, unit_cost: toKop(cost.value) };
        if (m) body.is_active = active.checked;
        await run(() => (m ? api.patch("/api/materials/" + m.id, body) : api.post("/api/materials", body)), "Збережено");
        closeSheet(); await loadRefs(); reload();
      } }, "Зберегти")));
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
      h("p", { class: "muted small" }, "Індивідуальні умови задаються в картці майстра (розділ «Персонал»). Зміни діють для нових замовлень; закриті замовлення зберігають умови на момент закриття.")),
    h("div", { class: "card stack" }, h("h3", {}, "Запис клієнтів"), field("Назва салону", name),
      h("div", { class: "form-grid" }, field("Крок слотів, хв", step), field("Запис наперед, днів", horizon), field("Мін. хв до запису (бот)", lead)),
      h("label", { class: "row" }, auto, "Записи з Telegram одразу підтверджені")),
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
