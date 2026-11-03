/* =========================================================
   M&D Beauty Salon — інтерактивність
   ========================================================= */
(function () {
  "use strict";

  var D = window.SALON;
  var $ = function (s, ctx) { return (ctx || document).querySelector(s); };
  var $$ = function (s, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(s)); };
  var STORAGE_KEY = "md-salon-bookings";

  function esc(str) {
    return String(str).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function formatPrice(s) {
    if (s.price === 0) return "Безкоштовно";
    return (s.from ? "<small>від</small>" : "") + s.price.toLocaleString("uk-UA") + " ₴";
  }
  function formatDuration(min) {
    if (!min) return "";
    var h = Math.floor(min / 60), m = min % 60;
    return (h ? h + " год" : "") + (h && m ? " " : "") + (m ? m + " хв" : "");
  }
  function findCategory(id) {
    for (var i = 0; i < D.categories.length; i++) if (D.categories[i].id === id) return D.categories[i];
    return D.categories[0];
  }

  /* ---------- Контакти з data.js ---------- */
  $$("[data-phone]").forEach(function (a) {
    a.textContent = D.contacts.phone;
    if (a.tagName === "A") a.href = "tel:" + D.contacts.phoneRaw;
  });
  $$("[data-address]").forEach(function (el) { el.textContent = D.contacts.address; });
  $$("[data-instagram]").forEach(function (a) { a.href = D.contacts.instagram; });
  $$("[data-telegram]").forEach(function (a) { a.href = D.contacts.telegram; });
  var map = $("#map");
  if (map) map.src = "https://www.google.com/maps?q=" + encodeURIComponent(D.contacts.mapQuery) + "&output=embed";
  $("#year").textContent = new Date().getFullYear();

  /* ---------- Хедер та меню ---------- */
  var header = $("#header");
  var burger = $("#burger");
  var nav = $("#nav");

  function onScroll() { header.classList.toggle("is-scrolled", window.scrollY > 30); }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  function toggleMenu(open) {
    var isOpen = typeof open === "boolean" ? open : !nav.classList.contains("is-open");
    nav.classList.toggle("is-open", isOpen);
    burger.setAttribute("aria-expanded", String(isOpen));
    burger.setAttribute("aria-label", isOpen ? "Закрити меню" : "Відкрити меню");
    document.body.classList.toggle("menu-open", isOpen);
  }
  burger.addEventListener("click", function () { toggleMenu(); });
  $$("a", nav).forEach(function (a) { a.addEventListener("click", function () { toggleMenu(false); }); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && nav.classList.contains("is-open")) toggleMenu(false); });

  /* Активний пункт меню */
  if ("IntersectionObserver" in window) {
    var links = $$(".nav__link");
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        links.forEach(function (l) { l.classList.toggle("is-active", l.getAttribute("href") === "#" + en.target.id); });
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    $$("main section[id]").forEach(function (s) { spy.observe(s); });
  }

  /* ---------- Послуги та ціни ---------- */
  var tabsEl = $("#serviceTabs");
  var panelEl = $("#pricePanel");

  tabsEl.innerHTML = D.categories.map(function (c, i) {
    return '<button class="tab' + (i === 0 ? " is-active" : "") + '" role="tab" aria-selected="' + (i === 0) +
      '" data-cat="' + c.id + '"><span class="tab__icon" aria-hidden="true">' + c.icon + "</span>" + esc(c.name) + "</button>";
  }).join("");

  function renderPrices(catId) {
    var c = findCategory(catId);
    panelEl.innerHTML =
      '<div class="price-panel__media"><img src="' + c.image + '" alt="' + esc(c.name) + '" loading="lazy">' +
      '<div class="price-panel__caption"><h3>' + esc(c.name) + "</h3><p>" + esc(c.description) + "</p></div></div>" +
      '<ul class="price-list">' + c.services.map(function (s, i) {
        var dur = formatDuration(s.duration);
        return '<li class="price-item"><div><span class="price-item__name">' + esc(s.name) + "</span>" +
          (dur ? '<span class="price-item__dur">≈ ' + dur + "</span>" : "") + "</div>" +
          '<span class="price-item__price">' + formatPrice(s) + "</span>" +
          '<button class="btn btn--outline btn--sm" data-book-cat="' + c.id + '" data-book-idx="' + i + '">Записатися</button></li>';
      }).join("") + "</ul>";
    // перезапуск анімації
    panelEl.style.animation = "none"; void panelEl.offsetWidth; panelEl.style.animation = "";
  }

  tabsEl.addEventListener("click", function (e) {
    var btn = e.target.closest(".tab");
    if (!btn) return;
    $$(".tab", tabsEl).forEach(function (t) {
      var on = t === btn;
      t.classList.toggle("is-active", on);
      t.setAttribute("aria-selected", String(on));
    });
    renderPrices(btn.dataset.cat);
    if (tabsEl.scrollWidth > tabsEl.clientWidth) {
      tabsEl.scrollTo({ left: btn.offsetLeft - (tabsEl.clientWidth - btn.offsetWidth) / 2, behavior: "smooth" });
    }
  });
  renderPrices(D.categories[0].id);

  /* ---------- Галерея + лайтбокс ---------- */
  var grid = $("#galleryGrid");
  var catNames = { hair: "Волосся", nails: "Нігті", makeup: "Макіяж", face: "Догляд", brows: "Брови та вії", spa: "SPA" };
  var visible = [];

  function renderGallery(filter) {
    visible = D.gallery.filter(function (g) { return filter === "all" || g.cat === filter; });
    grid.innerHTML = visible.map(function (g, i) {
      return '<button class="g-item' + (g.tall ? " g-item--tall" : "") + '" data-idx="' + i + '" style="animation-delay:' + (i * 50) + 'ms" aria-label="Відкрити фото: ' + esc(g.title) + '">' +
        '<img src="' + g.src + '" alt="' + esc(g.title) + '" loading="lazy">' +
        '<span class="g-item__cap"><small>' + (catNames[g.cat] || "") + "</small>" + esc(g.title) + "</span></button>";
    }).join("");
  }
  renderGallery("all");

  $("#galleryFilters").addEventListener("click", function (e) {
    var b = e.target.closest(".filter");
    if (!b) return;
    $$(".filter").forEach(function (f) { f.classList.toggle("is-active", f === b); });
    renderGallery(b.dataset.filter);
  });

  var lb = $("#lightbox"), lbImg = $("#lbImg"), lbCap = $("#lbCaption"), lbIdx = 0, lastFocus = null;
  function showLb(i) {
    lbIdx = (i + visible.length) % visible.length;
    var g = visible[lbIdx];
    lbImg.src = g.src; lbImg.alt = g.title; lbCap.textContent = g.title;
  }
  function openLb(i) {
    lastFocus = document.activeElement;
    showLb(i); lb.hidden = false; document.body.style.overflow = "hidden";
    $(".lightbox__close", lb).focus();
  }
  function closeLb() {
    lb.hidden = true; document.body.style.overflow = "";
    if (lastFocus) lastFocus.focus();
  }
  grid.addEventListener("click", function (e) {
    var item = e.target.closest(".g-item");
    if (item) openLb(+item.dataset.idx);
  });
  lb.addEventListener("click", function (e) {
    var act = e.target.closest("[data-lb]");
    if (act) {
      var a = act.dataset.lb;
      if (a === "close") closeLb(); else showLb(lbIdx + (a === "next" ? 1 : -1));
    } else if (e.target === lb) closeLb();
  });
  document.addEventListener("keydown", function (e) {
    if (lb.hidden) return;
    if (e.key === "Escape") closeLb();
    if (e.key === "ArrowRight") showLb(lbIdx + 1);
    if (e.key === "ArrowLeft") showLb(lbIdx - 1);
  });
  var touchX = null;
  lb.addEventListener("touchstart", function (e) { touchX = e.touches[0].clientX; }, { passive: true });
  lb.addEventListener("touchend", function (e) {
    if (touchX === null) return;
    var dx = e.changedTouches[0].clientX - touchX;
    if (Math.abs(dx) > 50) showLb(lbIdx + (dx < 0 ? 1 : -1));
    touchX = null;
  });

  /* ---------- Майстри ---------- */
  $("#mastersGrid").innerHTML = D.masters.map(function (m, i) {
    return '<article class="master reveal"><div class="master__avatar" aria-hidden="true">' + esc(m.name.charAt(0)) + "</div>" +
      "<h3>" + esc(m.name) + '</h3><p class="master__role">' + esc(m.role) + '</p><p class="master__exp">' + esc(m.exp) + "</p>" +
      '<button class="btn btn--outline btn--sm" data-book-master="' + i + '">Записатися</button></article>';
  }).join("");

  /* =========================================================
     Онлайн-запис
     ========================================================= */
  var B = D.booking;
  var form = $("#bookingForm");
  var fCat = $("#bfCategory"), fSrv = $("#bfService"), fMaster = $("#bfMaster");
  var fDates = $("#bfDates"), fTimes = $("#bfTimes"), fInfo = $("#bfServiceInfo");
  var fName = $("#bfName"), fPhone = $("#bfPhone"), fComment = $("#bfComment"), fAgree = $("#bfAgree");
  var summary = $("#bfSummary");
  var state = { date: null, time: null };

  function loadBookings() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || []; } catch (e) { return []; }
  }
  function saveBooking(b) {
    try {
      var all = loadBookings(); all.push(b);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
    } catch (e) { /* приватний режим — ігноруємо */ }
  }
  function dateKey(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function pad(n) { return String(n).padStart(2, "0"); }

  fCat.innerHTML = D.categories.map(function (c) { return '<option value="' + c.id + '">' + esc(c.name) + "</option>"; }).join("");

  function fillServices() {
    var c = findCategory(fCat.value);
    fSrv.innerHTML = c.services.map(function (s, i) {
      var p = s.price === 0 ? "безкоштовно" : (s.from ? "від " : "") + s.price.toLocaleString("uk-UA") + " ₴";
      return '<option value="' + i + '">' + esc(s.name) + " — " + p + "</option>";
    }).join("");
    fillMasters();
    updateServiceInfo();
  }
  function fillMasters(keep) {
    var prev = keep !== undefined ? keep : fMaster.value;
    var opts = '<option value="">Будь-який вільний майстер</option>';
    D.masters.forEach(function (m, i) {
      if (m.categories.indexOf(fCat.value) !== -1) opts += '<option value="' + i + '">' + esc(m.name) + " — " + esc(m.role) + "</option>";
    });
    fMaster.innerHTML = opts;
    if ($('option[value="' + prev + '"]', fMaster)) fMaster.value = prev;
  }
  function currentService() { return findCategory(fCat.value).services[+fSrv.value || 0]; }
  function updateServiceInfo() {
    var s = currentService();
    var dur = formatDuration(s.duration);
    fInfo.textContent = dur ? "Тривалість ≈ " + dur : "";
    renderTimes();
  }

  function renderDates() {
    var today = new Date(); today.setHours(0, 0, 0, 0);
    var html = "";
    for (var i = 0; i < B.daysAhead; i++) {
      var d = new Date(today); d.setDate(today.getDate() + i);
      var key = dateKey(d);
      var wd = i === 0 ? "Сьогодні" : i === 1 ? "Завтра" : d.toLocaleDateString("uk-UA", { weekday: "short" });
      var mon = d.toLocaleDateString("uk-UA", { month: "short" }).replace(".", "");
      html += '<button type="button" class="date" role="radio" aria-checked="false" data-date="' + key + '" aria-label="' +
        d.toLocaleDateString("uk-UA", { weekday: "long", day: "numeric", month: "long" }) + '"><small>' + wd +
        "</small><strong>" + d.getDate() + "</strong><small>" + mon + "</small></button>";
    }
    fDates.innerHTML = html;
  }

  function selectDate(key) {
    state.date = key; state.time = null;
    $$(".date", fDates).forEach(function (b) {
      var on = b.dataset.date === key;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-checked", String(on));
    });
    renderTimes();
  }

  function renderTimes() {
    if (!state.date) { fTimes.innerHTML = '<p class="times__empty">Спершу оберіть дату</p>'; updateSummary(); return; }
    var parts = state.date.split("-");
    var day = new Date(+parts[0], +parts[1] - 1, +parts[2]);
    var isSunday = day.getDay() === 0;
    var open = isSunday ? B.weekendOpen : B.openHour;
    var close = isSunday ? B.weekendClose : B.closeHour;
    var dur = Math.max(currentService().duration, B.slotMinutes);
    var now = new Date();
    var isToday = dateKey(now) === state.date;
    var nowMin = now.getHours() * 60 + now.getMinutes() + 60; // мінімум за годину

    var master = fMaster.value;
    var taken = loadBookings().filter(function (b) {
      return b.date === state.date && (master === "" ? false : String(b.masterIdx) === master);
    }).map(function (b) { return b.time; });

    var html = "", count = 0;
    for (var t = open * 60; t + dur <= close * 60; t += B.slotMinutes) {
      var label = pad(Math.floor(t / 60)) + ":" + pad(t % 60);
      var disabled = (isToday && t < nowMin) || taken.indexOf(label) !== -1;
      if (!disabled) count++;
      html += '<button type="button" class="time' + (state.time === label ? " is-active" : "") + '" role="radio" aria-checked="' +
        (state.time === label) + '" data-time="' + label + '"' + (disabled ? " disabled" : "") + ">" + label + "</button>";
    }
    fTimes.innerHTML = count ? html : '<p class="times__empty">На цей день вільних вікон немає — оберіть іншу дату.</p>';
    if (state.time && !$('.time[data-time="' + state.time + '"]:not(:disabled)', fTimes)) state.time = null;
    updateSummary();
  }

  function updateSummary() {
    if (!state.date || !state.time) { summary.innerHTML = ""; return; }
    var s = currentService();
    var parts = state.date.split("-");
    var d = new Date(+parts[0], +parts[1] - 1, +parts[2]);
    summary.innerHTML = "Ваш запис: <b>" + esc(s.name) + "</b>, " +
      d.toLocaleDateString("uk-UA", { weekday: "long", day: "numeric", month: "long" }) + " о <b>" + state.time + "</b>" +
      (s.price ? " · " + (s.from ? "від " : "") + s.price.toLocaleString("uk-UA") + " ₴" : "");
  }

  fCat.addEventListener("change", fillServices);
  fSrv.addEventListener("change", updateServiceInfo);
  fMaster.addEventListener("change", renderTimes);
  fDates.addEventListener("click", function (e) {
    var b = e.target.closest(".date");
    if (b) selectDate(b.dataset.date);
  });
  fTimes.addEventListener("click", function (e) {
    var b = e.target.closest(".time");
    if (!b || b.disabled) return;
    state.time = b.dataset.time;
    $$(".time", fTimes).forEach(function (t) {
      var on = t === b;
      t.classList.toggle("is-active", on);
      t.setAttribute("aria-checked", String(on));
    });
    setError("time", "");
    updateSummary();
  });

  /* Маска телефону: +38 (0XX) XXX-XX-XX */
  function phoneDigits(v) {
    var d = v.replace(/\D/g, "");
    if (/^\s*\+38/.test(v) || d.indexOf("380") === 0) d = d.slice(2);
    d = d.replace(/^0+/, "0");
    if (d && d[0] !== "0") d = "0" + d;
    return d.slice(0, 10);
  }
  fPhone.addEventListener("input", function () {
    var d = phoneDigits(fPhone.value);
    if (!d) { fPhone.value = ""; return; }
    var out = "+38 (" + d.slice(0, 3);
    if (d.length > 3) out += ") " + d.slice(3, 6);
    if (d.length > 6) out += "-" + d.slice(6, 8);
    if (d.length > 8) out += "-" + d.slice(8, 10);
    fPhone.value = out;
  });
  fPhone.addEventListener("focus", function () {
    if (fPhone.value) return;
    fPhone.value = "+38 (0";
    setTimeout(function () { var n = fPhone.value.length; fPhone.setSelectionRange(n, n); }, 0);
  });
  fPhone.addEventListener("blur", function () { if (phoneDigits(fPhone.value).length <= 1) fPhone.value = ""; });

  function setError(name, msg) {
    var el = $('[data-error="' + name + '"]', form);
    if (el) el.textContent = msg;
    var field = el && el.closest(".field");
    if (field) field.classList.toggle("has-error", !!msg);
  }

  function validate() {
    var ok = true;
    if (!state.date || !state.time) { setError("time", "Оберіть дату та час візиту"); ok = false; } else setError("time", "");
    if (fName.value.trim().length < 2) { setError("name", "Вкажіть ваше ім'я"); ok = false; } else setError("name", "");
    if (phoneDigits(fPhone.value).length !== 10) { setError("phone", "Вкажіть номер у форматі +38 (0XX) XXX-XX-XX"); ok = false; } else setError("phone", "");
    if (!fAgree.checked) { setError("agree", "Потрібна ваша згода"); ok = false; } else setError("agree", "");
    return ok;
  }
  [fName, fPhone].forEach(function (inp) {
    inp.addEventListener("input", function () { setError(inp.name, ""); });
  });

  var modal = $("#successModal");
  function openModal(html) {
    $("#successText").innerHTML = html;
    modal.hidden = false; document.body.style.overflow = "hidden";
    $("[data-close-modal]", modal).focus();
  }
  function closeModal() { modal.hidden = true; document.body.style.overflow = ""; }
  modal.addEventListener("click", function (e) { if (e.target === modal || e.target.closest("[data-close-modal]")) closeModal(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !modal.hidden) closeModal(); });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!validate()) {
      var firstErr = $(".has-error", form);
      if (firstErr) firstErr.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    var c = findCategory(fCat.value), s = currentService();
    var masterIdx = fMaster.value === "" ? null : +fMaster.value;
    var booking = {
      id: Date.now().toString(36),
      createdAt: new Date().toISOString(),
      category: c.name,
      service: s.name,
      price: s.price,
      priceFrom: !!s.from,
      masterIdx: masterIdx,
      master: masterIdx === null ? "Будь-який" : D.masters[masterIdx].name,
      date: state.date,
      time: state.time,
      name: fName.value.trim(),
      phone: fPhone.value,
      comment: fComment.value.trim()
    };

    var submit = $("#bfSubmit");
    submit.disabled = true; submit.textContent = "Надсилаємо…";

    var send = B.endpoint
      ? fetch(B.endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(booking) })
          .then(function (r) { if (!r.ok) throw new Error(r.status); })
      : Promise.resolve();

    send.then(function () {
      saveBooking(booking);
      var p = booking.date.split("-");
      var d = new Date(+p[0], +p[1] - 1, +p[2]);
      openModal(
        "<p>" + esc(booking.name) + ", ми отримали вашу заявку. Адміністратор зв'яжеться з вами для підтвердження.</p>" +
        "<dl><dt>Послуга</dt><dd>" + esc(booking.service) + "</dd>" +
        "<dt>Майстер</dt><dd>" + esc(booking.master) + "</dd>" +
        "<dt>Дата</dt><dd>" + d.toLocaleDateString("uk-UA", { weekday: "long", day: "numeric", month: "long" }) + "</dd>" +
        "<dt>Час</dt><dd>" + booking.time + "</dd>" +
        "<dt>Телефон</dt><dd>" + esc(booking.phone) + "</dd></dl>"
      );
      fComment.value = "";
      state.time = null;
      renderTimes();
    }).catch(function () {
      openModal('<p>Не вдалося надіслати заявку через мережеву помилку. Будь ласка, зателефонуйте нам: <a href="tel:' +
        D.contacts.phoneRaw + '">' + esc(D.contacts.phone) + "</a></p>");
    }).then(function () {
      submit.disabled = false; submit.textContent = "Підтвердити запис";
    });
  });

  /* Кнопки «Записатися» в прайсі та в картках майстрів */
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-book-cat]");
    var m = e.target.closest("[data-book-master]");
    if (!b && !m) return;
    if (b) {
      fCat.value = b.dataset.bookCat;
      fillServices();
      fSrv.value = b.dataset.bookIdx;
      updateServiceInfo();
    } else {
      var master = D.masters[+m.dataset.bookMaster];
      fCat.value = master.categories[0];
      fillServices();
      fillMasters(m.dataset.bookMaster);
      renderTimes();
    }
    $("#booking").scrollIntoView({ behavior: "smooth" });
    form.classList.add("is-highlight");
  });

  fillServices();
  renderDates();
  selectDate(dateKey(new Date()));
  // якщо сьогодні вільних вікон немає — автоматично переходимо на завтра
  if (!$(".time:not(:disabled)", fTimes)) {
    var t = new Date(); t.setDate(t.getDate() + 1);
    selectDate(dateKey(t));
  }

  /* ---------- Анімації появи ---------- */
  var reveals = $$(".reveal");
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("is-visible"); io.unobserve(en.target); }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
    reveals.forEach(function (el) { io.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add("is-visible"); });
  }

  /* ---------- Мобільна кнопка запису ховається біля форми ---------- */
  var cta = $("#mobileCta");
  if ("IntersectionObserver" in window) {
    var hideOn = [$("#booking"), $(".hero")];
    var visibleSet = new Set();
    var ctaIo = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) visibleSet.add(en.target); else visibleSet.delete(en.target); });
      cta.classList.toggle("is-hidden", visibleSet.size > 0);
    }, { threshold: 0.15 });
    hideOn.forEach(function (el) { ctaIo.observe(el); });
  }
})();
