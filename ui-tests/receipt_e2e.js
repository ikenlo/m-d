// E2E друку чеків: емулятор Checkbox (server/scripts/fake_checkbox.py) + емулятор принтера ESC/POS
// (server/scripts/fake_printer.py) + справжній інтерфейс. Міст планшета MDSAndroid.printWifi емулюється в Node:
// ті самі байти йдуть по TCP на «принтер», як з Android. Знімки надрукованих чеків — ui-tests/out/receipt-*.png.
//
// Сервер: MDS_ENV=test, MDS_CHECKBOX_API_URL=http://127.0.0.1:8779, MDS_CHECKBOX_LOGIN/_PASSWORD/_LICENSE_KEY — будь-які.
//   node receipt_e2e.js http://localhost:8770
const path = require("path");
const fs = require("fs");
const net = require("net");
const { spawn } = require("child_process");
const { chromium } = require("playwright");

const BASE = process.argv[2] || process.env.UI_BASE || "http://localhost:8770";
const CB_PORT = Number(process.env.CHECKBOX_MOCK_PORT || 8779);
const PRN_PORT = Number(process.env.FAKE_PRINTER_PORT || 9100);
const OUT = path.join(__dirname, "out");
const PRINTED = path.join(OUT, "printed");
const SCRIPTS = path.join(__dirname, "..", "server", "scripts");
const failures = [];
const check = (cond, msg) => { if (cond) console.log("  ✓ " + msg); else { failures.push(msg); console.log("  ✗ " + msg); } };

function start(script, args, ready) {
  const proc = spawn(process.env.PYTHON || "python", [path.join(SCRIPTS, script), ...args], { stdio: ["ignore", "pipe", "pipe"] });
  const log = [];
  proc.stderr.on("data", (d) => log.push(String(d)));
  return new Promise((ok, fail) => {
    const t = setTimeout(() => fail(new Error(`${script} не стартував: ${log.join("")}`)), 10000);
    proc.stdout.on("data", (d) => { if (ready.test(String(d))) { clearTimeout(t); ok(proc); } });
    proc.on("exit", (c) => { clearTimeout(t); fail(new Error(`${script} завершився (${c}): ${log.join("")}`)); });
  });
}

function nativePrintWifi(ip, port, b64) {
  return new Promise((ok, fail) => {
    const sock = net.createConnection({ host: ip, port }, () => sock.end(Buffer.from(b64, "base64")));
    sock.setTimeout(5000, () => { sock.destroy(); fail(new Error("Принтер Wi-Fi недоступний")); });
    sock.on("close", (hadErr) => (hadErr ? fail(new Error("Принтер Wi-Fi недоступний")) : ok(true)));
    sock.on("error", () => {});
  });
}

const waitFile = async (f, ms = 10000) => {
  for (let t = 0; t < ms; t += 200) { if (fs.existsSync(f)) return true; await new Promise((r) => setTimeout(r, 200)); }
  return false;
};

(async () => {
  fs.rmSync(PRINTED, { recursive: true, force: true });
  fs.mkdirSync(PRINTED, { recursive: true });
  const cb = await start("fake_checkbox.py", ["--port", String(CB_PORT)], /Емулятор Checkbox/);
  const prn = await start("fake_printer.py", ["--port", String(PRN_PORT), "--out", PRINTED], /Емулятор принтера/);
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: "Europe/Kyiv", locale: "uk-UA" });
    await ctx.exposeFunction("__nativePrintWifi", nativePrintWifi);
    await ctx.addInitScript(() => {
      window.MDSAndroid = {
        available: () => true,
        printWifi: (ip, port, b64) => window.__nativePrintWifi(ip, port, b64)
          .then(() => window.mdsPrintResult(true, "Чек надруковано"), (e) => window.mdsPrintResult(false, String(e.message || e))),
        printBluetooth: () => window.mdsPrintResult(false, "Bluetooth у тесті не емулюється"),
      };
    });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => check(false, "помилка JS: " + e.message));
    page.on("dialog", (d) => d.accept());
    await page.goto(BASE + "/");
    await page.fill("#login-user", "margaryta");
    await page.fill("#login-pass", "Director123");
    await page.click("button[type=submit]");
    await page.waitForSelector(".main");

    console.log("Директор: Checkbox + принтер Wi-Fi (емулятор)");
    await page.evaluate(() => api.patch("/api/settings/terminal", { enabled: false }).catch(() => {}));
    await page.goto(BASE + "/#/settings");
    await page.waitForSelector("[data-testid=cb-card] :text('Ключі касира задані на сервері')", { timeout: 8000 });
    await page.check("[data-testid=cb-enabled]");
    await page.selectOption("[data-testid=cb-printer-type]", "wifi");
    await page.fill("[data-testid=cb-printer-ip]", "127.0.0.1");
    await page.fill("[data-testid=cb-printer-port]", String(PRN_PORT));
    await page.click("[data-testid=cb-save]");
    await page.waitForSelector(".toast:has-text('Налаштування Checkbox збережено')", { timeout: 5000 });
    await page.click("[data-testid=cb-check]");
    check(await page.waitForSelector("[data-testid=cb-result] :text(\"Зв'язок є\")", { timeout: 8000 }).then(() => true, () => false),
      "«Перевірити зв'язок» з емулятором Checkbox");

    const ctxData = await page.evaluate(async () => {
      const users = await api.get("/api/users");
      const svcs = await api.get("/api/services");
      const prods = await api.get("/api/products").catch(() => []);
      const prod = prods.find((p) => p.name === "Шампунь відновлювальний 250 мл" && p.stock_qty > 0)
        || await api.post("/api/products", { name: "Шампунь відновлювальний 250 мл", cost_price_cents: 18000, sale_price_cents: 32000, stock_qty: 20 });
      return { master: users.find((u) => u.username === "dmitry").id,
        svc: svcs.find((s) => s.price > 0 && !s.price_from && !s.needs_materials && /стриж/i.test(s.name)) || svcs.find((s) => s.price > 0 && !s.price_from && !s.needs_materials),
        prod };
    });
    const day = new Date(Date.now() + (5 + Math.floor(Math.random() * 300)) * 864e5).toISOString().slice(0, 10);
    const closeOrder = async (hour, client, pay, withProduct) => {
      const a = await page.evaluate(async ({ c, day, hour, client }) => api.post("/api/appointments", { master_id: c.master, service_id: c.svc.id,
        starts_at: `${day}T${hour}:00:00`, client_name: client, confirm_off_hours: true, status: "confirmed" }), { c: ctxData, day, hour, client });
      await page.evaluate((a) => closeOrderSheet(a, () => {}), a);
      await page.waitForSelector("#overlay .sheet");
      await page.waitForTimeout(300);
      if (withProduct && ctxData.prod) {
        await page.click("#overlay summary:has-text('Товари клієнту')");
        await page.locator("#overlay .sum-line", { hasText: ctxData.prod.name }).locator("button:has-text('+')").click();
      }
      await page.click(`#overlay .choice button:has-text('${pay}')`);
      await page.click("#overlay .btn.primary.big");
      await page.waitForSelector("#overlay", { state: "detached", timeout: 8000 });
      return a;
    };

    console.log("Готівка + товар → чек → друк");
    await closeOrder("10", "Олена Коваль", "Готівка", true);
    check(await waitFile(path.join(PRINTED, "receipt-1.html")), "принтер отримав чек №1");
    await page.waitForSelector(".toast:has-text('Чек надруковано')", { timeout: 8000 }).catch(() => {});

    console.log("Картка (ручне підтвердження) → чек → друк");
    await closeOrder("12", "Ірина Мельник", "Картка", false);
    check(await waitFile(path.join(PRINTED, "receipt-2.html")), "принтер отримав чек №2");

    const t1 = fs.existsSync(path.join(PRINTED, "receipt-1.txt")) ? fs.readFileSync(path.join(PRINTED, "receipt-1.txt"), "utf8") : "";
    const t2 = fs.existsSync(path.join(PRINTED, "receipt-2.txt")) ? fs.readFileSync(path.join(PRINTED, "receipt-2.txt"), "utf8") : "";
    check(t1.includes("ГОТІВКА") && t1.includes("СУМА") && t1.includes(ctxData.svc.name.slice(0, 10)), "чек №1: послуга, СУМА, ГОТІВКА");
    check(t1.includes("Шампунь відновлювальний") && t1.includes("1.000 x 320.00"), "чек №1: товар у чеку (1 × 320.00)");
    check(t2.includes("БЕЗГОТІВКОВА"), "чек №2: безготівкова оплата (CASHLESS)");
    check(!/\?/.test(t1 + t2) && t1.includes("ІД"), "кирилиця без «?» (кодова сторінка cp1251)");

    for (const [n, name] of [[1, "receipt-cash"], [2, "receipt-card"]]) {
      const f = path.join(PRINTED, `receipt-${n}.html`);
      if (!fs.existsSync(f)) continue;
      const p2 = await browser.newPage({ viewport: { width: 460, height: 400 }, deviceScaleFactor: 2 });
      await p2.goto("file://" + f);
      await p2.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
      await p2.close();
    }
    await page.evaluate(() => api.patch("/api/settings/checkbox", { enabled: false, printer_type: "none", printer_ip: "" }));
  } finally {
    await browser.close();
    cb.kill();
    prn.kill();
  }
  if (failures.length) { console.log(`\n${failures.length} проблем(и):\n` + failures.join("\n")); process.exit(1); }
  console.log(`\nДрук чеків (емуляція): усе гаразд — ${OUT}/receipt-cash.png, receipt-card.png`);
})().catch((e) => { console.error(e); process.exit(1); });
