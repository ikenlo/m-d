/* M&D Beauty Salon — сторінка: налаштування з shared/config.json, прайс із shared/services.json.
   Без cookie та трекерів. Усі тексти, контакти й прапорці — лише з конфігурації. */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  // DOM лише через створення елементів — без innerHTML з даними
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  const svg = (paths) => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "1.8"); s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round"); s.setAttribute("aria-hidden", "true");
    for (const d of paths) { const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", d); s.append(p); }
    return s;
  };
  const ICONS = {
    phone: ["M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.8.6 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.5 2.7.6a2 2 0 0 1 1.7 2z"],
    telegram: ["M22 2L11 13", "M22 2l-7 20-4-9-9-4z"],
    viber: ["M12 3c5 0 8 2.5 8 7.5S17 18 12 18l-4 3v-3.4C5.6 16.6 4 14.3 4 10.5 4 5.5 7 3 12 3z", "M9.5 8.5c.5 2 2 3.5 4 4"],
    instagram: ["M7 2h10a5 5 0 0 1 5 5v10a5 5 0 0 1-5 5H7a5 5 0 0 1-5-5V7a5 5 0 0 1 5-5z", "M16 11.4A4 4 0 1 1 12.6 8 4 4 0 0 1 16 11.4z", "M17.5 6.5h.01"],
    user: ["M20 21a8 8 0 0 0-16 0", "M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10z"],
  };
  const money = (uah) => `${Number(uah).toLocaleString("uk-UA")} грн`;
  const priceText = (s) => (s.price === null || s.price === undefined || s.price === "" ? "ціна уточнюється" : `${s.price_from ? "від " : ""}${money(s.price)}`);
  const durText = (m) => (!m ? "" : m < 60 ? `${m} хв` : `${Math.floor(m / 60)} год${m % 60 ? ` ${m % 60} хв` : ""}`);
  const get = (obj, path) => path.split(".").reduce((o, k) => (o ? o[k] : undefined), obj);

  async function loadJSON(name) {
    for (const base of ["../shared/", "shared/"]) {
      try {
        const r = await fetch(base + name, { cache: "no-cache" });
        if (r.ok) return await r.json();
      } catch (_) { /* пробуємо наступний шлях */ }
    }
    throw new Error("Не вдалося завантажити " + name);
  }

  // швидкі кнопки зв'язку — лише ті, що задані в конфігурації
  function quickLinks(c, { icons = false } = {}) {
    const ct = c.contacts || {};
    const tel = (ct.phone || "").replace(/[^\d+]/g, "");
    const items = [
      ct.telegram && ["telegram", "Telegram", ct.telegram],
      ct.viber && ["viber", "Viber", ct.viber],
      tel && ["phone", "Подзвонити", "tel:" + tel],
      ct.instagram && ["instagram", "Instagram", ct.instagram],
    ].filter(Boolean);
    return items.map(([ic, label, href]) => h("a", { class: "qbtn", href, target: href.startsWith("http") ? "_blank" : null, rel: href.startsWith("http") ? "noopener" : null, "aria-label": label },
      svg(ICONS[ic]), icons ? h("span", {}, label) : label));
  }

  // години роботи й статус «Відчинено / Зачинено» за київським часом
  const WD = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"];
  function kyivNow(tz) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour12: false, weekday: "short", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
      .formatToParts(new Date()).map((p) => [p.type, p.value]));
    const wd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(parts.weekday);
    return { date: `${parts.year}-${parts.month}-${parts.day}`, wd, minutes: (Number(parts.hour) % 24) * 60 + Number(parts.minute) };
  }
  const toMin = (s) => { const [a, b] = s.split(":").map(Number); return a * 60 + b; };

  function renderContacts(c) {
    const ct = c.contacts || {};
    if (ct.address) $("#c-address").textContent = [ct.address, ct.city].filter(Boolean).join(", ");
    if (ct.phone) $("#c-phone").replaceChildren(h("a", { href: "tel:" + ct.phone.replace(/[^\d+]/g, "") }, ct.phone));
    const days = (c.hours && c.hours.days) || {};
    if (Object.keys(days).length) {
      const now = kyivNow(c.hours.timezone || "Europe/Kyiv");
      const today = days[String(now.wd)];
      const open = Array.isArray(today) && now.minutes >= toMin(today[0]) && now.minutes < toMin(today[1]);
      const st = $("#c-status");
      st.hidden = false;
      st.className = "status " + (open ? "open" : "closed");
      st.textContent = open ? `Відчинено до ${today[1]}` : "Зачинено";
      $("#c-hours").replaceChildren(...WD.map((n, i) => {
        const d = days[String(i)];
        return h("span", { class: "hours-row" + (i === now.wd ? " today" : "") }, h("span", {}, n), h("span", {}, Array.isArray(d) ? `${d[0]}–${d[1]}` : "вихідний"));
      }));
    }
    if (ct.map_embed_url) {
      $("#map").replaceChildren(h("iframe", { src: ct.map_embed_url, title: "Карта: як дістатися до салону", loading: "lazy", referrerpolicy: "no-referrer-when-downgrade" }));
    }
  }

  function renderPriceList(c, services, onBook) {
    const box = $("#price-list");
    box.replaceChildren();
    services.categories.forEach((cat, ci) => {
      const id = "cat-" + cat.id;
      const btn = h("button", { class: "acc-btn", type: "button", "aria-expanded": ci === 0 ? "true" : "false", "aria-controls": id, id: id + "-btn" },
        cat.title, h("span", { class: "count" }, cat.services.length > 1 || cat.services[0]?.price !== null ? `${cat.services.length}` : ""));
      const panel = h("div", { class: "acc-panel", id, role: "region", "aria-labelledby": id + "-btn", hidden: ci !== 0 },
        cat.services.map((s) => h("div", { class: "svc" },
          h("span", { class: "svc-name" }, s.name),
          h("span", { class: "svc-price" + (s.price === null ? " todo" : "") }, priceText(s)),
          h("span", { class: "svc-meta" }, s.duration_min ? h("span", {}, durText(s.duration_min)) : null,
            s.level && c.levels?.[s.level] ? h("span", { class: "level" }, c.levels[s.level]) : null),
          s.price !== null ? h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => onBook(s.id), "aria-label": `Записатися: ${s.name}` }, "Записатися") : null)));
      box.append(h("div", { class: "acc-item" }, h("h3", {}, btn), panel));
    });
  }

  function renderPromos(c) {
    const p = c.promos || {};
    const items = [];
    if (p.trainee_discount?.enabled) items.push(`${p.trainee_discount.text}${p.trainee_discount.percent ? ` — −${p.trainee_discount.percent}%` : ""}`);
    if (p.duo_discount?.enabled) items.push(`${p.duo_discount.text}${p.duo_discount.percent ? ` — −${p.duo_discount.percent}%` : ""}`);
    if (p.loyalty?.enabled) items.push(p.loyalty.text);
    const box = $("#promos");
    if (items.length) { box.hidden = false; box.replaceChildren(...items.map((t) => h("div", { class: "promo" }, t))); }
  }

  function renderMasters(c) {
    $("#masters-list").replaceChildren(...(c.masters || []).map((m) => h("article", { class: "master" },
      h("div", { class: "ph" }, m.photo ? h("img", { src: m.photo, alt: `Майстер ${m.name}`, loading: "lazy", width: 400, height: 500 }) : svg(ICONS.user)),
      h("div", { class: "body" }, h("h3", {}, m.name), m.role ? h("p", { class: "muted small" }, m.role) : null,
        m.level && c.levels?.[m.level] ? h("span", { class: "level" }, c.levels[m.level]) : null))));
  }

  function renderGallery(c) {
    const g = c.gallery || {};
    const works = g.works || [];
    $("#gallery-grid").replaceChildren(...(works.length ? works.map((w) => h("div", { class: "tile" }, h("img", { src: w.src, alt: w.alt || "Робота майстра", loading: "lazy", width: 600, height: 600 })))
      : Array.from({ length: 6 }, () => h("div", { class: "tile", "aria-hidden": "true" }, h("span", {}, "M&D")))));
    if (works.length) $("#gallery-note").hidden = true;
    const ba = g.before_after || [];
    $("#ba-grid").replaceChildren(...(ba.length ? ba : [{}, {}]).map((x) => h("div", { class: "ba-item", "aria-hidden": ba.length ? null : "true" },
      h("div", { class: "tile" }, x.before ? h("img", { src: x.before, alt: (x.alt || "") + " — до", loading: "lazy" }) : null, h("b", {}, "до")),
      h("div", { class: "tile" }, x.after ? h("img", { src: x.after, alt: (x.alt || "") + " — після", loading: "lazy" }) : null, h("b", {}, "після")))));
  }

  function renderExtras(c) {
    const p = c.promos || {};
    if (p.gift_certificates?.enabled) {
      $("#gifts").hidden = false;
      $("#gift-text").textContent = p.gift_certificates.text || "";
      const ct = c.contacts || {};
      const href = ct.telegram || ct.instagram || "#contacts";
      const b = $("#gift-btn");
      b.href = href;
      if (href.startsWith("http")) { b.target = "_blank"; b.rel = "noopener"; }
    }
    // відгуки — лише справжні: блок показуємо, коли ввімкнено й є посилання
    if (p.reviews?.enabled && c.contacts?.google_reviews_url) {
      $("#reviews").hidden = false;
      $("#reviews-link").href = c.contacts.google_reviews_url;
    }
    $("#about-text").textContent = c.salon?.about || "Розповідь про салон з'явиться найближчим часом.";
  }

  function seo(c) {
    const ct = c.contacts || {};
    const ld = { "@context": "https://schema.org", "@type": "HairSalon", name: c.salon?.name, description: c.seo?.description,
      url: c.seo?.site_url || undefined, image: c.seo?.og_image || undefined, telephone: ct.phone || undefined,
      sameAs: [ct.instagram, ct.telegram].filter(Boolean), paymentAccepted: "Cash, Credit Card", currenciesAccepted: "UAH" };
    if (ct.address) ld.address = { "@type": "PostalAddress", streetAddress: ct.address, addressLocality: ct.city || undefined, addressCountry: "UA" };
    const days = c.hours?.days || {};
    const en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
    const spec = Object.entries(days).filter(([, v]) => Array.isArray(v)).map(([k, v]) => ({ "@type": "OpeningHoursSpecification", dayOfWeek: en[+k], opens: v[0], closes: v[1] }));
    if (spec.length) ld.openingHoursSpecification = spec;
    $("#ld-json").textContent = JSON.stringify(ld);
    if (c.seo?.description) document.querySelector('meta[name="description"]').setAttribute("content", c.seo.description);
  }

  function ui() {
    // меню на телефоні
    const burger = $(".burger"), nav = $("#nav");
    burger.addEventListener("click", () => { const o = nav.classList.toggle("open"); burger.setAttribute("aria-expanded", String(o)); });
    nav.addEventListener("click", (e) => { if (e.target.closest("a")) { nav.classList.remove("open"); burger.setAttribute("aria-expanded", "false"); } });
    // акордеони (прайс і FAQ)
    document.addEventListener("click", (e) => {
      const b = e.target.closest(".acc-btn");
      if (!b) return;
      const open = b.getAttribute("aria-expanded") === "true";
      b.setAttribute("aria-expanded", String(!open));
      document.getElementById(b.getAttribute("aria-controls")).hidden = open;
    });
    // липку кнопку ховаємо, коли форма запису на екрані
    const sticky = $("#sticky-cta");
    if ("IntersectionObserver" in window) {
      new IntersectionObserver((en) => sticky.classList.toggle("hide", en.some((x) => x.isIntersecting)), { threshold: 0.05 }).observe($("#booking"));
    }
  }

  window.MD = window.MD || {};
  window.MD.h = h;
  window.MD.svg = svg;
  window.MD.ICONS = ICONS;
  window.MD.quickLinks = quickLinks;
  window.MD.kyivNow = kyivNow;
  window.MD.priceText = priceText;
  window.MD.durText = durText;
  window.MD.ready = (async () => {
    ui();
    const [config, services] = await Promise.all([loadJSON("config.json"), loadJSON("services.json")]);
    document.querySelectorAll("[data-cfg]").forEach((el) => { const v = get(config, el.dataset.cfg); if (v) el.textContent = v; else if (el.dataset.cfg === "salon.opening_note") el.hidden = true; });
    if (config.salon?.name) document.title = `${config.salon.name} — ${config.salon.tagline || "онлайн-запис"}`;
    ["#quick-hero", "#quick-contacts", "#quick-footer"].forEach((s) => $(s).replaceChildren(...quickLinks(config)));
    $("#quick-sticky").replaceChildren(...quickLinks(config, { icons: true }).slice(0, 2));
    renderPromos(config);
    renderPriceList(config, services, (id) => window.MD.pickService && window.MD.pickService(id));
    renderMasters(config);
    renderGallery(config);
    renderExtras(config);
    renderContacts(config);
    seo(config);
    return { config, services };
  })();
  window.MD.ready.catch((e) => {
    $("#price-list").replaceChildren(h("p", { class: "alert" }, "Не вдалося завантажити дані сайту. Оновіть сторінку."));
    console.error(e);
  });
})();
