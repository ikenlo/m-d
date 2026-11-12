// UI: Checkbox (ПРРО) — налаштування директора, закриття замовлення → фіскальний чек → друк через MDSAndroid.
// Потрібен сервер з MDS_ENV=test і MDS_CHECKBOX_API_URL=http://127.0.0.1:<CHECKBOX_MOCK_PORT> (за замовчуванням 8779)
// та будь-якими MDS_CHECKBOX_LOGIN / _PASSWORD / _LICENSE_KEY. Макет Checkbox і міст планшета — у самому тесті.
const http = require("http");
const { chromium } = require("playwright");

const BASE = process.argv[2] || process.env.UI_BASE || "http://localhost:8770";
const MOCK_PORT = Number(process.env.CHECKBOX_MOCK_PORT || 8779);
const failures = [];
const check = (cond, msg) => { if (cond) console.log("  ✓ " + msg); else { failures.push(msg); console.log("  ✗ " + msg); } };

function checkboxMock() {
  const st = { shift: null, receipts: {}, sells: [] };
  const send = (res, code, body, type = "application/json") => {
    res.writeHead(code, { "Content-Type": type });
    res.end(type === "application/json" ? JSON.stringify(body) : body);
  };
  st.server = http.createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => { raw += c; });
    req.on("end", () => {
      const u = new URL(req.url, "http://x");
      const body = raw ? JSON.parse(raw) : null;
      if (req.method === "POST" && u.pathname === "/api/v1/cashier/signin") return send(res, 200, { access_token: "T" });
      if (req.method === "GET" && u.pathname === "/api/v1/cashier/shift") return send(res, 200, st.shift);
      if (req.method === "POST" && u.pathname === "/api/v1/shifts") { st.shift = { id: body.id, status: "OPENED" }; return send(res, 202, st.shift); }
      if (req.method === "POST" && u.pathname === "/api/v1/shifts/close") { st.shift = null; return send(res, 202, { status: "CLOSING" }); }
      if (req.method === "POST" && u.pathname === "/api/v1/receipts/sell") {
        st.sells.push(body);
        st.receipts[body.id] = { id: body.id, status: "DONE", fiscal_code: "TEST" + st.sells.length };
        return send(res, 201, st.receipts[body.id]);
      }
      let m = u.pathname.match(/^\/api\/v1\/receipts\/([^/]+)\/text$/);
      if (m) return send(res, 200, `ФОП ТЕСТ\nФН ${st.receipts[m[1]].fiscal_code}\nСУМА ${(st.sells.at(-1).payments[0].value / 100).toFixed(2)}\n`, "text/plain");
      m = u.pathname.match(/^\/api\/v1\/receipts\/([^/]+)$/);
      if (m) return st.receipts[m[1]] ? send(res, 200, st.receipts[m[1]]) : send(res, 404, { message: "Not found" });
      send(res, 404, { message: "Unknown" });
    });
  });
  return new Promise((ok) => st.server.listen(MOCK_PORT, "127.0.0.1", () => ok(st)));
}

(async () => {
  const cb = await checkboxMock();
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: "Europe/Kyiv", locale: "uk-UA" });
  await ctx.addInitScript(() => {
    window.__prints = [];
    window.MDSAndroid = {
      available: () => true,
      printWifi: (ip, port, b64) => { window.__prints.push({ kind: "wifi", ip, port, bytes: atob(b64) }); setTimeout(() => window.mdsPrintResult(true, "Чек надруковано"), 50); },
      printBluetooth: (b64, name) => { window.__prints.push({ kind: "bt", name, bytes: atob(b64) }); setTimeout(() => window.mdsPrintResult(true, "Чек надруковано"), 50); },
    };
  });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => check(false, "помилка JS: " + e.message));
  try {
    await page.goto(BASE + "/");
    await page.fill("#login-user", "margaryta");
    await page.fill("#login-pass", "Director123");
    await page.click("button[type=submit]");
    await page.waitForSelector(".main");

    console.log("Налаштування «Checkbox (ПРРО)»");
    await page.goto(BASE + "/#/settings");
    await page.waitForSelector("[data-testid=cb-card] :text('Ключі касира задані на сервері')", { timeout: 8000 });
    check(true, "ключі касира задані на сервері");
    await page.check("[data-testid=cb-enabled]");
    await page.selectOption("[data-testid=cb-printer-type]", "wifi");
    await page.fill("[data-testid=cb-printer-ip]", "192.168.1.60");
    await page.click("[data-testid=cb-save]");
    await page.waitForSelector(".toast:has-text('Налаштування Checkbox збережено')", { timeout: 5000 }).catch(() => {});
    await page.click("[data-testid=cb-check]");
    const ok = await page.waitForSelector("[data-testid=cb-result] :text(\"Зв'язок є\")", { timeout: 8000 }).then(() => true, () => false);
    check(ok, "«Перевірити зв'язок» — вхід касира успішний");
    await page.click("[data-testid=cb-test-print]");
    await page.waitForTimeout(400);
    let prints = await page.evaluate(() => window.__prints.map((p) => ({ ...p, bytes: p.bytes.length })));
    check(prints.length === 1 && prints[0].ip === "192.168.1.60" && prints[0].port === 9100, "тестовий друк пішов на Wi-Fi принтер");

    console.log("Закриття замовлення → чек → друк");
    const appt = await page.evaluate(async () => {
      const users = await api.get("/api/users");
      const m = users.find((u) => u.username === "dmitry");
      const svc = (await api.get("/api/services")).find((s) => s.price > 0 && !s.price_from && !s.needs_materials);
      const day = new Date(Date.now() + (5 + Math.floor(Math.random() * 300)) * 864e5).toISOString().slice(0, 10);
      return api.post("/api/appointments", { master_id: m.id, service_id: svc.id, starts_at: `${day}T10:00:00`, client_name: "Чек Тест",
        confirm_off_hours: true, status: "confirmed" });
    });
    await page.evaluate((a) => closeOrderSheet(a, () => {}), appt);
    await page.waitForSelector("#overlay .sheet");
    await page.waitForTimeout(300);
    await page.click("#overlay .choice button:has-text('Готівка')");
    await page.click("#overlay .btn.primary.big");
    await page.waitForSelector("#overlay", { state: "detached", timeout: 5000 });
    await page.waitForSelector(".toast:has-text('Чек надруковано')", { timeout: 8000 }).catch(() => {});
    prints = await page.evaluate(() => window.__prints.map((p) => ({ kind: p.kind, ip: p.ip, head: [...p.bytes.slice(0, 3)].map((c) => c.charCodeAt(0)), tail: [...p.bytes.slice(-4)].map((c) => c.charCodeAt(0)) })));
    const last = cb.sells.at(-1) || {};
    check(cb.sells.length === 1 && last.payments?.[0]?.type === "CASH" && last.payments[0].value === appt.price, `чек Checkbox: CASH, ${last.payments?.[0]?.value} коп.`);
    check(prints.length === 2 && prints[1].ip === "192.168.1.60" && prints[1].head.join() === "27,64,27" && prints[1].tail.join() === "29,86,66,0",
      "чек надіслано на принтер (ESC @ … обрізання)");

    console.log("Повторний друк із картки замовлення");
    const orderId = await page.evaluate(async (apptId) => {
      const r = await api.get("/api/orders?period=year");
      return (Array.isArray(r) ? r : r.items || []).find((o) => o.appointment_id === apptId)?.id;
    }, appt.id);
    if (orderId) {
      await page.evaluate((id) => orderSheet(id, () => {}), orderId);
      await page.waitForSelector("[data-testid=fiscal-block] button:has-text('Друкувати чек')", { timeout: 5000 });
      await page.click("[data-testid=fiscal-block] button");
      await page.waitForTimeout(500);
      const n = await page.evaluate(() => window.__prints.length);
      check(n === 3 && cb.sells.length === 1, "«Друкувати чек» — повторний друк без нового чека");
    } else check(false, "не знайдено замовлення для повторного друку");

    await page.evaluate(() => api.patch("/api/settings/checkbox", { enabled: false, printer_type: "none", printer_ip: "" }));
  } finally {
    await browser.close();
    cb.server.close();
  }
  if (failures.length) { console.log(`\n${failures.length} проблем(и):\n` + failures.join("\n")); process.exit(1); }
  console.log("\nCheckbox (UI): усе гаразд");
})().catch((e) => { console.error(e); process.exit(1); });
