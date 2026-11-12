// UI-перевірка оплати карткою: ручний режим (confirm) і POS-термінал ПриватБанку через макет TCP на 127.0.0.1.
const net = require("net");
const { chromium } = require("playwright");

const BASE = process.argv[2] || process.env.UI_BASE || "http://localhost:8770";
const failures = [];
const check = (cond, msg) => { if (cond) console.log("  ✓ " + msg); else { failures.push(msg); console.log("  ✗ " + msg); } };

function mockTerminal() {
  const t = { mode: "approve", delay: 1200, received: [] };
  t.server = net.createServer((sock) => {
    let buf = Buffer.alloc(0);
    sock.on("error", () => {});
    sock.on("data", (d) => {
      buf = Buffer.concat([buf, d]);
      const i = buf.indexOf(0);
      if (i < 0) return;
      const msg = JSON.parse(buf.subarray(0, i).toString("utf8"));
      t.received.push(msg.method);
      const send = (o) => { if (!sock.destroyed) sock.write(JSON.stringify(o) + "\0"); };
      if (msg.method === "PingDevice") return send({ method: "PingDevice", step: 0, error: false, params: { responseCode: "0000" } });
      if (msg.method === "ServiceMessage") { t.interrupt && t.interrupt(); return send({ method: "ServiceMessage", step: 0, error: false }); }
      if (t.mode === "hang") { t.interrupt = () => send({ method: "Purchase", step: 0, error: true, params: { responseCode: "1001", rrn: "" } }); return; }
      setTimeout(() => send(t.mode === "approve"
        ? { method: "Purchase", step: 0, error: false, params: { responseCode: "0000", rrn: "014722766522", approvalCode: "131192", pan: "4149********1451" } }
        : { method: "Purchase", step: 0, error: false, params: { responseCode: "05", rrn: "" } }), t.delay);
    });
  });
  return new Promise((ok) => t.server.listen(0, "127.0.0.1", () => { t.port = t.server.address().port; ok(t); }));
}

(async () => {
  const term = await mockTerminal();
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: "Europe/Kyiv", locale: "uk-UA" })).newPage();
  page.on("pageerror", (e) => check(false, "помилка JS: " + e.message));
  const dialogs = [];
  let answer = true;
  page.on("dialog", (d) => { dialogs.push(d.message()); answer ? d.accept() : d.dismiss(); });

  await page.goto(BASE + "/");
  await page.fill("#login-user", "margaryta");
  await page.fill("#login-pass", "Director123");
  await page.click("button[type=submit]");
  await page.waitForSelector(".main");

  const ctx = await page.evaluate(async () => {
    const users = await api.get("/api/users");
    const m = users.find((u) => u.role === "master") || users[0];
    const svc = (await api.get("/api/services")).find((s) => s.price > 0 && !s.price_from && !s.needs_materials);
    return { master: m.id, service: svc.id };
  });
  let slot = 0;
  const dayOffset = 5 + Math.floor(Math.random() * 300);
  // Демо-дані мають майбутні записи — при «Майстер зайнятий» пробуємо інший день.
  const newAppt = () => page.evaluate(async ({ c, n, off }) => {
    let last;
    for (let k = 0; k < 30; k++) {
      const day = new Date(Date.now() + (off + k * 7) * 864e5).toISOString().slice(0, 10);
      try {
        return await api.post("/api/appointments", { master_id: c.master, service_id: c.service, starts_at: `${day}T${String(8 + n).padStart(2, "0")}:00:00`,
          client_name: "Тест Оплата", confirm_off_hours: true, status: "confirmed" });
      } catch (e) { last = e; }
    }
    throw last;
  }, { c: ctx, n: slot++, off: dayOffset });
  const openClose = async (a) => {
    await page.evaluate((a) => closeOrderSheet(a, () => { window.__closed = (window.__closed || 0) + 1; }), a);
    await page.waitForSelector("#overlay .sheet");
    await page.waitForTimeout(300);
    const names = await page.$$eval("#overlay .choice button", (bs) => bs.map((b) => b.textContent.trim()));
    if (names.join("|") !== "Готівка|Картка") check(false, "кнопки оплати: " + names.join(", "));
    await page.click("#overlay .choice button:has-text('Картка')");
  };

  console.log("Налаштування «POS Термінал»");
  await page.goto(BASE + "/#/settings?g=payments");
  await page.waitForSelector("[data-testid=pos-card]");
  await page.check("[data-testid=pos-enabled]");
  await page.fill("[data-testid=pos-ip]", "127.0.0.1");
  await page.fill("[data-testid=pos-port]", String(term.port));
  await page.click("[data-testid=pos-save]");
  await page.waitForTimeout(500);
  await page.click("[data-testid=pos-check]");
  await page.waitForSelector("[data-testid=pos-check-result] :text(\"Зв'язок є\")", { timeout: 8000 }).catch(() => {});
  check(/Зв'язок є/.test(await page.textContent("[data-testid=pos-check-result]")), "«Перевірити зв'язок» — зв'язок є");
  check(term.received.includes("PingDevice"), "термінал отримав PingDevice");

  console.log("Термінал: успішна оплата");
  let a = await newAppt();
  await openClose(a);
  await page.click("#overlay .btn.primary.big");
  await page.waitForSelector("[data-testid=pay-wait]");
  check(/Очікування оплати на терміналі/.test(await page.textContent("[data-testid=pay-wait-text]")), "екран «Очікування оплати на терміналі...»");
  check(await page.evaluate(() => document.querySelector("#overlay .sheet").classList.contains("sheet-locked") && isProcessingPayment), "вікно заблоковано, isProcessingPayment = true");
  await page.keyboard.press("Escape");
  check(await page.$("#overlay") !== null, "Escape не закриває вікно під час оплати");
  const shown = await page.waitForSelector("[data-testid=pay-wait-text]:has-text('Оплату проведено')", { timeout: 10000 }).then(() => true, () => false);
  check(shown && await page.$("[data-testid=pay-wait] .pw-ok") !== null, "показано «Оплату проведено» із зеленою позначкою");
  await page.waitForSelector("[data-testid=pay-wait]", { state: "detached", timeout: 5000 });
  await page.waitForSelector("#overlay", { state: "detached", timeout: 5000 });
  let st = await page.evaluate(async (id) => (await api.get(`/api/appointments/${id}`)).status, a.id);
  check(st === "completed", `замовлення закрито (статус запису: ${st})`);
  check(await page.evaluate(() => !isProcessingPayment), "блокування знято");

  console.log("Термінал: відмова → «Закрити вручну»");
  term.mode = "decline";
  a = await newAppt();
  await openClose(a);
  await page.click("#overlay .btn.primary.big");
  await page.waitForSelector("[data-testid=pay-wait-text]:has-text('відхилив')", { timeout: 10000 }).catch(() => {});
  check(/Термінал відхилив оплату \(код 05\)/.test(await page.textContent("[data-testid=pay-wait-text]")), "показано текст помилки з сервера");
  answer = true;
  dialogs.length = 0;
  await page.click("[data-testid=pay-manual]");
  await page.waitForSelector("#overlay", { state: "detached", timeout: 5000 }).catch(() => {});
  check(dialogs[0] === "Ви підтверджуєте, що оплата пройшла успішно?", "confirm «Ви підтверджуєте…»");
  st = await page.evaluate(async (id) => (await api.get(`/api/appointments/${id}`)).status, a.id);
  check(st === "completed", `закрито вручну (статус запису: ${st})`);

  console.log("Термінал: «Скасувати» під час очікування");
  term.mode = "hang";
  a = await newAppt();
  await openClose(a);
  await page.click("#overlay .btn.primary.big");
  await page.waitForSelector("[data-testid=pay-cancel]");
  await page.waitForTimeout(700);
  await page.click("[data-testid=pay-cancel]");
  await page.waitForSelector("[data-testid=pay-wait]", { state: "detached", timeout: 5000 });
  check(term.received.includes("ServiceMessage"), "термінал отримав команду скасування");
  check(await page.evaluate(() => !isProcessingPayment && !document.querySelector("#overlay .sheet").classList.contains("sheet-locked")), "вікно розблоковано");
  await page.waitForTimeout(800);
  st = await page.evaluate(async (id) => (await api.get(`/api/appointments/${id}`)).status, a.id);
  check(st !== "completed", "замовлення не закрито");
  await page.evaluate(() => closeSheet(true));

  console.log("Ручний режим (термінал вимкнено): confirm()");
  const purchasesBefore = term.received.filter((m) => m === "Purchase").length;
  await page.evaluate(() => api.patch("/api/settings/terminal", { enabled: false }));
  a = await newAppt();
  await openClose(a);
  answer = false;
  dialogs.length = 0;
  await page.click("#overlay .btn.primary.big");
  await page.waitForTimeout(500);
  check(dialogs[0] === "Підтвердіть, що оплата карткою успішно пройшла на терміналі", "confirm «Підтвердіть, що оплата карткою…»");
  check(await page.$("#overlay") !== null && await page.evaluate(() => !isProcessingPayment), "«Скасувати» в confirm — вікно лишається, розблоковане");
  answer = true;
  await page.click("#overlay .btn.primary.big");
  await page.waitForSelector("#overlay", { state: "detached", timeout: 5000 });
  st = await page.evaluate(async (id) => (await api.get(`/api/appointments/${id}`)).status, a.id);
  check(st === "completed", `закрито після підтвердження (статус запису: ${st})`);
  check(term.received.filter((m) => m === "Purchase").length === purchasesBefore, "у ручному режимі термінал не викликався");

  await browser.close();
  term.server.close();
  if (failures.length) { console.log(`\n${failures.length} проблем(и):\n` + failures.join("\n")); process.exit(1); }
  console.log("\nОплата карткою (UI): усе гаразд");
})().catch((e) => { console.error(e); process.exit(1); });
