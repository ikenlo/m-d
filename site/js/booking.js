/* M&D Beauty Salon — онлайн-запис.
   Два режими (shared/config.json → booking.api_mode):
   - "mock": демонстрація без сервера — вільний час генерується, запис зберігається лише в цьому браузері;
   - "live": API системи MDS Connect (спільне сховище з Telegram-ботом) — контракт у site/docs/api.md.
   Час — Europe/Kyiv; на сьогодні минулий час не показується. Введені дані не губляться (чернетка в браузері). */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* приватний режим — без чернетки */ } },
    del(k) { try { localStorage.removeItem(k); } catch (_) { /* немає сховища */ } },
  };
  const DRAFT = "md_booking_draft";
  const MOCK_KEY = "md_mock_bookings";
  const pad = (n) => String(n).padStart(2, "0");
  const toMin = (s) => { const [a, b] = s.split(":").map(Number); return a * 60 + b; };
  const fromMin = (m) => `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
  const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  const WD_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"];
  const MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"];
  const wdOf = (iso) => (new Date(iso + "T12:00:00Z").getUTCDay() + 6) % 7;
  const dateLong = (iso) => `${["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"][wdOf(iso)]}, ${Number(iso.slice(8))} ${MONTHS[Number(iso.slice(5, 7)) - 1]}`;

  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }

  // ---------------------------------------------------------------- mock-API (без сервера)
  function mockApi(config, services) {
    const b = config.booking || {};
    const masters = (config.masters || []).map((m, i) => ({ id: i + 1, name: m.name, level: m.level }));
    const hours = config.hours?.days || {};
    const dayHours = (iso) => {
      if (Object.keys(hours).length) { const d = hours[String(wdOf(iso))]; return Array.isArray(d) ? d : null; }
      return wdOf(iso) === 6 ? null : ["09:00", "19:00"]; // демонстраційний графік, поки власник не вказав години
    };
    // псевдовипадкова зайнятість, однакова при кожному відкритті
    const busy = (key) => { let x = 0; for (const c of key) x = (x * 31 + c.charCodeAt(0)) >>> 0; return x % 4 === 0; };
    const svc = (id) => services.categories.flatMap((c) => c.services).find((s) => s.id === id);
    const delay = (v) => new Promise((r) => setTimeout(() => r(v), 250));
    return {
      mode: "mock",
      async masters() { return delay(masters); },
      async slots(date, serviceId, masterId) {
        const s = svc(serviceId);
        const span = dayHours(date);
        if (!s || !span) return delay([]);
        const step = b.slot_step_min || 30, dur = s.duration_min || 60;
        const taken = store.get(MOCK_KEY) || [];
        const out = [];
        for (let t = toMin(span[0]); t + dur <= toMin(span[1]); t += step) {
          const time = fromMin(t);
          const free = masters.filter((m) => (!masterId || m.id === masterId) && !busy(`${date}${time}${m.id}`)
            && !taken.some((x) => x.date === date && x.master === m.id && toMin(x.time) < t + dur && toMin(x.time) + x.dur > t));
          if (free.length) out.push({ time, masters: free.map((m) => m.id) });
        }
        return delay(out);
      },
      async book(p) {
        const slots = await this.slots(p.date, p.service_id, p.master_id);
        const slot = slots.find((x) => x.time === p.time);
        if (!slot) throw new ApiError(409, "Цей час щойно зайняли. Оберіть інший, будь ласка.");
        const master = p.master_id || slot.masters[0];
        const s = svc(p.service_id);
        const taken = store.get(MOCK_KEY) || [];
        taken.push({ date: p.date, time: p.time, master, dur: s.duration_min || 60 });
        store.set(MOCK_KEY, taken);
        return delay({ id: Date.now(), status: "pending", starts_at: `${p.date}T${p.time}`, service: s.name,
          master: masters.find((m) => m.id === master)?.name, message: "Демонстраційний запис збережено в цьому браузері." });
      },
    };
  }

  // ---------------------------------------------------------------- live-API (MDS Connect)
  function liveApi(config) {
    const base = (config.booking?.api_base || "").replace(/\/$/, "");
    async function req(method, path, body) {
      let r;
      try {
        r = await fetch(base + path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
      } catch (_) {
        throw new ApiError(0, "Немає зв'язку із сервером запису.");
      }
      let data = null;
      try { data = await r.json(); } catch (_) { /* порожня відповідь */ }
      if (!r.ok) throw new ApiError(r.status, data?.detail || "Сервіс запису тимчасово недоступний.");
      return data;
    }
    let svcMap = null; // id із services.json → id послуги в системі
    return {
      mode: "live",
      async mapServices(services) {
        const list = await req("GET", "/api/public/services");
        const key = (c, sub, n) => `${c}|${sub || ""}|${n}`.toLowerCase();
        const byKey = new Map(list.map((x) => [key(x.category, x.subcategory, x.name), x]));
        svcMap = new Map();
        for (const cat of services.categories) for (const s of cat.services) {
          const x = s.system && byKey.get(key(s.system.category, s.system.subcategory, s.system.name));
          if (x) { svcMap.set(s.id, x.id); s.price = x.price / 100; s.price_from = x.price_from; s.duration_min = x.duration_min; }
        }
        return svcMap;
      },
      async masters(serviceId) { return req("GET", "/api/public/masters" + (serviceId && svcMap?.get(serviceId) ? `?service=${svcMap.get(serviceId)}` : "")); },
      async slots(date, serviceId, masterId) {
        const id = svcMap?.get(serviceId);
        if (!id) return [];
        const r = await req("GET", `/api/public/slots?date=${date}&service=${id}${masterId ? `&master=${masterId}` : ""}`);
        return r.slots || [];
      },
      async book(p) { return req("POST", "/api/public/bookings", { ...p, service_id: svcMap?.get(p.service_id) }); },
    };
  }

  // ---------------------------------------------------------------- форма
  window.MD.ready.then(async ({ config, services }) => {
    const h = window.MD.h;
    const b = config.booking || {};
    const tz = config.hours?.timezone || "Europe/Kyiv";
    let api = b.api_mode === "live" && b.api_base ? liveApi(config) : mockApi(config, services);
    $("#mock-note").hidden = api.mode !== "mock";
    const form = $("#booking-form");
    const el = { service: $("#b-service"), master: $("#b-master"), dates: $("#b-dates"), times: $("#b-times"), name: $("#b-name"),
      phone: $("#b-phone"), comment: $("#b-comment"), website: $("#b-website"), err: $("#b-error"), sum: $("#b-summary"), submit: $("#b-submit"), done: $("#b-done") };
    const draft = store.get(DRAFT) || {};
    const st = { date: draft.date || null, time: null, startedAt: 0 };
    const allServices = services.categories.flatMap((c) => c.services.map((s) => ({ ...s, cat: c.title })));
    const svcById = (id) => allServices.find((s) => s.id === id);

    function showError(msg, withContacts) {
      el.err.hidden = false;
      el.err.replaceChildren(h("div", {}, msg), withContacts ? h("div", { class: "quick" }, window.MD.quickLinks(config)) : null);
    }
    const clearError = () => { el.err.hidden = true; };

    if (api.mode === "live") {
      try { await api.mapServices(services); } catch (e) {
        showError(`${e.message} Можна записатися через Telegram або телефоном — ваші дані у формі збережуться.`, true);
      }
    }

    // послуги (згруповано за категоріями; без ціни — не для запису)
    el.service.replaceChildren(h("option", { value: "" }, "Оберіть послугу"), ...services.categories.map((c) => {
      const items = c.services.filter((s) => s.price !== null && s.price !== undefined);
      return items.length ? h("optgroup", { label: c.title }, items.map((s) => h("option", { value: s.id }, `${s.name} — ${window.MD.priceText(s)}`))) : null;
    }));

    async function loadMasters() {
      const cur = el.master.value || draft.master || "";
      let list = [];
      try { list = await api.masters(el.service.value); } catch (_) { list = []; }
      const levelOf = (name) => { const m = (config.masters || []).find((x) => x.name && name && name.startsWith(x.name)); return m?.level && config.levels?.[m.level] ? ` · ${config.levels[m.level]}` : ""; };
      el.master.replaceChildren(h("option", { value: "" }, "Будь-який вільний"), ...list.map((m) => h("option", { value: m.id }, m.name + levelOf(m.name))));
      if ([...el.master.options].some((o) => o.value === String(cur))) el.master.value = String(cur);
    }

    // дати: від сьогодні (Київ) на N днів
    function drawDates() {
      const today = window.MD.kyivNow(tz).date;
      const n = b.days_ahead || 14;
      const days = config.hours?.days || {};
      const hasHours = Object.keys(days).length > 0;
      if (!st.date || st.date < today) st.date = today;
      el.dates.replaceChildren(...Array.from({ length: n }, (_, i) => {
        const iso = addDays(today, i);
        const off = hasHours && !Array.isArray(days[String(wdOf(iso))]);
        return h("button", { type: "button", class: "chip" + (off ? " off" : ""), role: "radio", "aria-checked": String(iso === st.date), disabled: off || null,
          "aria-label": `${dateLong(iso)}${off ? " — вихідний" : ""}`, onclick: () => { st.date = iso; st.time = null; save(); drawDates(); loadSlots(); } },
        h("small", {}, i === 0 ? "Сьогодні" : i === 1 ? "Завтра" : WD_SHORT[wdOf(iso)]), h("b", {}, Number(iso.slice(8))));
      }));
    }

    let slotReq = 0;
    async function loadSlots() {
      const svc = el.service.value;
      if (!svc || !st.date) { el.times.replaceChildren(h("p", { class: "muted small" }, "Спершу оберіть послугу й дату.")); summary(); return; }
      const my = ++slotReq;
      el.times.replaceChildren(...Array.from({ length: 6 }, () => h("div", { class: "skeleton" })));
      let slots;
      try {
        slots = await api.slots(st.date, svc, el.master.value ? Number(el.master.value) : null);
      } catch (e) {
        if (my !== slotReq) return;
        el.times.replaceChildren(h("p", { class: "muted small" }, "Не вдалося отримати вільний час."));
        showError(`${e.message} Спробуйте пізніше або зв'яжіться з нами — введені дані збережено.`, true);
        return;
      }
      if (my !== slotReq) return;
      clearError();
      // на сьогодні — лише час, пізніший за поточний (+ мінімальний відступ)
      const now = window.MD.kyivNow(tz);
      if (st.date === now.date) slots = slots.filter((x) => toMin(x.time) > now.minutes + (b.min_lead_min || 0));
      if (!slots.some((x) => x.time === st.time)) st.time = null;
      el.times.replaceChildren(...(slots.length ? slots.map((x) => h("button", { type: "button", class: "slot", role: "radio", "aria-checked": String(x.time === st.time),
        onclick: (ev) => { st.time = x.time; [...el.times.children].forEach((c) => c.setAttribute("aria-checked", String(c === ev.currentTarget))); summary(); } }, x.time))
        : [h("p", { class: "muted small" }, st.date === now.date ? "На сьогодні вільного часу вже немає — оберіть іншу дату." : "На цю дату вільного часу немає — оберіть іншу дату або іншого майстра.")]));
      summary();
    }

    function summary() {
      const s = svcById(el.service.value);
      if (!s || !st.date || !st.time) { el.sum.hidden = true; return; }
      el.sum.hidden = false;
      el.sum.replaceChildren(h("b", {}, s.name), h("br"), `${dateLong(st.date)} о ${st.time}`,
        ` · ${el.master.value ? el.master.options[el.master.selectedIndex].text : "будь-який вільний майстер"}`, h("br"),
        h("span", { class: "muted" }, `${window.MD.priceText(s)}${s.duration_min ? ` · ${window.MD.durText(s.duration_min)}` : ""}`));
    }

    function save() {
      store.set(DRAFT, { service: el.service.value, master: el.master.value, date: st.date, name: el.name.value, phone: el.phone.value, comment: el.comment.value });
    }
    const touch = () => { if (!st.startedAt) st.startedAt = Date.now(); };
    form.addEventListener("input", () => { touch(); save(); });
    form.addEventListener("focusin", touch);

    el.service.addEventListener("change", async () => { st.time = null; save(); await loadMasters(); loadSlots(); });
    el.master.addEventListener("change", () => { st.time = null; save(); loadSlots(); });

    window.MD.pickService = async (id) => {
      el.service.value = id;
      st.time = null; save();
      document.getElementById("booking").scrollIntoView({ behavior: "smooth" });
      await loadMasters(); loadSlots();
      setTimeout(() => el.service.focus({ preventScroll: true }), 400);
    };

    const phoneOk = (v) => { const d = v.replace(/\D/g, ""); return /^0\d{9}$/.test(d) || /^380\d{9}$/.test(d); };
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearError();
      const s = svcById(el.service.value);
      el.name.removeAttribute("aria-invalid"); el.phone.removeAttribute("aria-invalid");
      if (!s) return showError("Оберіть послугу.");
      if (!st.date || !st.time) return showError("Оберіть дату й вільний час.");
      if (el.name.value.trim().length < 2) { el.name.setAttribute("aria-invalid", "true"); el.name.focus(); return showError("Вкажіть ім'я."); }
      if (!phoneOk(el.phone.value)) { el.phone.setAttribute("aria-invalid", "true"); el.phone.focus(); return showError("Вкажіть український номер телефону, напр. 067 123 45 67."); }
      el.submit.disabled = true;
      el.submit.textContent = "Надсилаємо…";
      try {
        const r = await api.book({ name: el.name.value.trim(), phone: el.phone.value.trim(), service_id: s.id, master_id: el.master.value ? Number(el.master.value) : null,
          date: st.date, time: st.time, comment: el.comment.value.trim() || null, website: el.website.value, elapsed_ms: st.startedAt ? Date.now() - st.startedAt : 0 });
        store.del(DRAFT);
        form.hidden = true;
        el.done.hidden = false;
        el.done.replaceChildren(h("div", { class: "check", "aria-hidden": "true" }, "✓"), h("h3", {}, "Запис отримано"),
          h("p", {}, h("b", {}, r.service || s.name), h("br"), `${dateLong(st.date)} о ${st.time}${r.master ? ` · майстер ${r.master}` : ""}`),
          h("p", { class: "muted" }, r.message || "Адміністратор зв'яжеться з вами для підтвердження."),
          h("button", { class: "btn btn-ghost", type: "button", onclick: () => { el.done.hidden = true; form.hidden = false; st.time = null; loadSlots(); } }, "Записатися ще"));
        el.done.focus();
      } catch (err) {
        if (err.status === 409) { st.time = null; await loadSlots(); showError(err.message); }
        else if (err.status === 0 || err.status >= 500) showError(`${err.message} Зв'яжіться з нами напряму — дані у формі збережено.`, true);
        else showError(err.message, err.status === 429);
      } finally {
        el.submit.disabled = false;
        el.submit.textContent = "Записатися";
      }
    });

    // відновлюємо чернетку
    if (draft.service && svcById(draft.service)) el.service.value = draft.service;
    el.name.value = draft.name || "";
    el.phone.value = draft.phone || "";
    el.comment.value = draft.comment || "";
    await loadMasters();
    drawDates();
    loadSlots();
    // о півночі / за годину список часу оновлюється сам (минулий час зникає)
    setInterval(() => { if (!form.hidden) { drawDates(); loadSlots(); } }, 5 * 60 * 1000);
  });
})();
