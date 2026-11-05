/* M&D Beauty Salon — «Швидка заявка»: послуга + коли зручно + ім'я й телефон, адміністратор передзвонить.
   Режими як в онлайн-записі (shared/config.json → booking.api_mode):
   - "mock": заявка зберігається лише в цьому браузері;
   - "live": POST /api/public/callbacks системи MDS Connect (контракт — site/docs/api.md).
   Варіанти кнопок — shared/config.json → quick_request. DOM — лише через h(), без innerHTML. */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* приватний режим */ } },
    del(k) { try { localStorage.removeItem(k); } catch (_) { /* немає сховища */ } },
  };
  const DRAFT = "md_quick_draft";
  const MOCK_KEY = "md_mock_callbacks";
  const DEFAULTS = {
    services: ["Жіноча стрижка", "Чоловіча стрижка", "Фарбування", "Мелірування / blonde", "Догляд", "Інше"],
    when: ["Сьогодні", "Завтра", "Цього тижня", "Як зручно салону"],
  };

  window.MD.ready.then(({ config, services }) => {
    const h = window.MD.h;
    const q = { ...DEFAULTS, ...(config.quick_request || {}) };
    const live = config.booking?.api_mode === "live";
    const base = (config.booking?.api_base || "").replace(/\/$/, "");
    const el = { form: $("#quick-form"), svc: $("#q-services"), when: $("#q-when"), name: $("#q-name"), phone: $("#q-phone"),
      website: $("#q-website"), err: $("#q-error"), submit: $("#q-submit"), done: $("#q-done") };
    $("#q-mock").hidden = live;
    const draft = store.get(DRAFT) || {};
    const st = { service: draft.service || null, when: draft.when || null, picked: null, startedAt: 0 };

    const save = () => store.set(DRAFT, { service: st.service, when: st.when, name: el.name.value, phone: el.phone.value });
    const touch = () => { if (!st.startedAt) st.startedAt = Date.now(); };

    function chip(label, cur, onPick, extra) {
      return h("button", { type: "button", class: "pick-chip" + (extra ? " " + extra : ""), role: "radio", "aria-checked": String(label === cur),
        onclick: () => { touch(); onPick(label); save(); draw(); } }, label);
    }
    function draw() {
      const list = st.picked && !q.services.includes(st.picked) ? [st.picked, ...q.services] : q.services;
      el.svc.replaceChildren(...list.map((x) => chip(x, st.service, (v) => { st.service = st.service === v ? null : v; }, x === st.picked ? "picked" : "")));
      el.when.replaceChildren(...q.when.map((x) => chip(x, st.when, (v) => { st.when = st.when === v ? null : v; })));
    }

    function showError(msg, withContacts) {
      el.err.hidden = false;
      el.err.replaceChildren(h("div", {}, msg, withContacts ? h("div", { class: "quick" }, window.MD.quickLinks(config)) : null));
    }

    // кнопка «Записатися» в прайсі: підставляємо конкретну послугу в заявку
    const allServices = services.categories.flatMap((c) => c.services.map((s) => ({ ...s, cat: c.title })));
    window.MD.pickQuick = (id) => {
      const s = allServices.find((x) => x.id === id);
      if (!s) return;
      const label = `${s.cat}: ${s.name}`.slice(0, 60);
      st.picked = label; st.service = label; save(); draw();
      if (!el.done.hidden) { el.done.hidden = true; el.form.hidden = false; }
      document.getElementById("quick").scrollIntoView({ behavior: "smooth", block: "start" });
      setTimeout(() => (el.name.value ? el.phone : el.name).focus({ preventScroll: true }), 450);
    };

    async function send(p) {
      if (!live) {
        await new Promise((r) => setTimeout(r, 300));
        const list = store.get(MOCK_KEY) || [];
        list.push({ ...p, at: new Date().toISOString() });
        store.set(MOCK_KEY, list.slice(-20));
        return { message: "Демонстраційну заявку збережено в цьому браузері." };
      }
      let r;
      try {
        r = await fetch(base + "/api/public/callbacks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(p) });
      } catch (_) {
        const e = new Error("Немає зв'язку із сервером."); e.status = 0; throw e;
      }
      let data = null;
      try { data = await r.json(); } catch (_) { /* порожня відповідь */ }
      if (!r.ok) { const e = new Error(data?.detail || "Сервіс заявок тимчасово недоступний."); e.status = r.status; throw e; }
      return data;
    }

    const phoneOk = (v) => { const d = v.replace(/\D/g, ""); return /^0\d{9}$/.test(d) || /^380\d{9}$/.test(d); };
    el.form.addEventListener("input", () => { touch(); save(); });
    el.form.addEventListener("focusin", touch);
    el.form.addEventListener("submit", async (e) => {
      e.preventDefault();
      el.err.hidden = true;
      el.name.removeAttribute("aria-invalid"); el.phone.removeAttribute("aria-invalid");
      if (el.name.value.trim().length < 2) { el.name.setAttribute("aria-invalid", "true"); el.name.focus(); return showError("Вкажіть ім'я."); }
      if (!phoneOk(el.phone.value)) { el.phone.setAttribute("aria-invalid", "true"); el.phone.focus(); return showError("Вкажіть український номер телефону, напр. 067 123 45 67."); }
      el.submit.disabled = true;
      el.submit.textContent = "Надсилаємо…";
      try {
        const r = await send({ name: el.name.value.trim(), phone: el.phone.value.trim(), service: st.service, when: st.when,
          website: el.website.value, elapsed_ms: st.startedAt ? Date.now() - st.startedAt : 0 });
        const what = [st.service, st.when].filter(Boolean).join(" · ");
        store.del(DRAFT);
        el.form.hidden = true;
        el.done.hidden = false;
        el.done.replaceChildren(h("div", { class: "check", "aria-hidden": "true" }, "✓"), h("h3", {}, "Заявку отримано!"),
          what ? h("p", {}, h("b", {}, what)) : null,
          h("p", {}, r?.message || "Адміністратор зателефонує й підбере зручний час."),
          h("p", { class: "small" }, `Ми зателефонуємо на ${el.phone.value.trim()}.`),
          h("button", { class: "btn btn-ghost", type: "button", onclick: () => { el.done.hidden = true; el.form.hidden = false; st.startedAt = 0; } }, "Ще одна заявка"));
        el.done.focus();
      } catch (err) {
        showError(err.status === 0 || err.status >= 500 ? `${err.message} Зв'яжіться з нами напряму — дані у формі збережено.` : err.message,
          err.status === 0 || err.status === 429 || err.status >= 500);
      } finally {
        el.submit.disabled = false;
        el.submit.textContent = "Залишити заявку";
      }
    });

    el.name.value = draft.name || "";
    el.phone.value = draft.phone || "";
    if (st.service && !q.services.includes(st.service)) st.picked = st.service;
    draw();
  });
})();
