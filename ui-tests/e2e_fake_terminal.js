// E2E: емулятор термінала (server/scripts/fake_terminal.py) + справжній інтерфейс.
// Директор налаштовує «POS Термінал» і перевіряє зв'язок; майстер у «Сьогодні» закриває запис «Карткою»:
// спінер, заблоковане вікно, схвалення від емулятора (≈3 с), замовлення закрито.
//
//   node e2e_fake_terminal.js http://localhost:8770            # сервер з MDS_ENV=test
//   HEADED=1 node e2e_fake_terminal.js http://localhost:8770   # видно браузер (повільно, для огляду)
//   FAKE_TERMINAL_PORT=2001 PYTHON=python3 node e2e_fake_terminal.js …
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const { chromium } = require("playwright");

const BASE = process.argv[2] || process.env.UI_BASE || "http://localhost:8770";
const PORT = Number(process.env.FAKE_TERMINAL_PORT || 2000);
const HEADED = !!process.env.HEADED;
const OUT = path.join(__dirname, "out");
const failures = [];
const check = (cond, msg) => { if (cond) console.log("  ✓ " + msg); else { failures.push(msg); console.log("  ✗ " + msg); } };

function startEmulator() {
  const script = path.join(__dirname, "..", "server", "scripts", "fake_terminal.py");
  const proc = spawn(process.env.PYTHON || "python", [script, "--host", "127.0.0.1", "--port", String(PORT), "--mode", "approve"],
    { stdio: ["ignore", "pipe", "pipe"] });
  const log = [];
  proc.stderr.on("data", (d) => log.push(String(d)));
  return new Promise((ok, fail) => {
    const t = setTimeout(() => fail(new Error("емулятор не стартував: " + log.join(""))), 10000);
    proc.stdout.on("data", (d) => { if (/Емулятор термінала/.test(String(d))) { clearTimeout(t); ok({ proc, log }); } });
    proc.on("exit", (c) => { clearTimeout(t); fail(new Error(`емулятор завершився (${c}): ${log.join("")}`)); });
  });
}

async function login(browser, user, pass) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: "Europe/Kyiv", locale: "uk-UA" });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => check(false, `${user}: помилка JS: ${e.message}`));
  await page.goto(BASE + "/");
  await page.fill("#login-user", user);
  await page.fill("#login-pass", pass);
  await page.click("button[type=submit]");
  await page.waitForSelector(".main");
  return page;
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const emu = await startEmulator();
  console.log(`Емулятор: 127.0.0.1:${PORT}`);
  const browser = await chromium.launch({ headless: !HEADED, slowMo: HEADED ? 250 : 0 });
  try {
    console.log("Директор: «POS Термінал» → емулятор");
    const dir = await login(browser, "margaryta", "Director123");
    await dir.goto(BASE + "/#/settings?g=payments");
    await dir.waitForSelector("[data-testid=pos-card]");
    await dir.check("[data-testid=pos-enabled]");
    await dir.fill("[data-testid=pos-ip]", "127.0.0.1");
    await dir.fill("[data-testid=pos-port]", String(PORT));
    await dir.click("[data-testid=pos-save]");
    await dir.waitForSelector(".toast:has-text('Налаштування термінала збережено')", { timeout: 5000 }).catch(() => {});
    await dir.click("[data-testid=pos-check]");
    const linked = await dir.waitForSelector("[data-testid=pos-check-result] :text(\"Зв'язок є\")", { timeout: 8000 }).then(() => true, () => false);
    check(linked, "«Перевірити зв'язок» → «Зв'язок є»");
    await dir.locator("[data-testid=pos-card]").screenshot({ path: path.join(OUT, "e2e-pos-settings.png") });

    const client = `E2E Термінал ${Math.floor(Math.random() * 1e6)}`;
    const appt = await dir.evaluate(async (client) => {
      const users = await api.get("/api/users");
      const m = users.find((u) => u.username === "dmitry");
      const svc = (await api.get("/api/services")).find((s) => s.price > 0 && !s.price_from && !s.needs_materials);
      const d = new Date();
      const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      let last = null;
      for (let h = 6; h <= 22; h++) for (const mm of ["00", "30"]) {
        try {
          return await api.post("/api/appointments", { master_id: m.id, service_id: svc.id, starts_at: `${day}T${String(h).padStart(2, "0")}:${mm}:00`,
            client_name: client, confirm_off_hours: true, status: "confirmed" });
        } catch (e) { last = e.message; }
      }
      throw new Error("не вдалося створити запис на сьогодні: " + last);
    }, client);
    check(!!appt.id, `запис на сьогодні для майстра (#${appt.id}, ${client})`);

    console.log("Майстер: «Сьогодні» → «Закрити» → «Картка»");
    const m = await login(browser, "dmitry", "Master123");
    await m.goto(BASE + "/#/today");
    const card = m.locator(".card.appt", { hasText: client });
    await card.waitFor({ timeout: 10000 });
    await card.locator("button:has-text('Закрити')").click();
    await m.waitForSelector("#overlay .sheet");
    await m.waitForTimeout(400);
    const names = await m.$$eval("#overlay .choice button", (bs) => bs.map((b) => b.textContent.trim()));
    check(names.join("|") === "Готівка|Картка", `кнопки оплати: ${names.join(", ")}`);
    await m.click("#overlay .choice button:has-text('Картка')");
    await m.click("#overlay .btn.primary.big");

    await m.waitForSelector("[data-testid=pay-wait] .spinner", { timeout: 5000 });
    check(true, "з'явився спінер «Очікування оплати на терміналі...»");
    const lock = await m.evaluate(() => {
      const sh = document.querySelector("#overlay .sheet");
      const cs = getComputedStyle(sh);
      return { cls: sh.classList.contains("sheet-locked"), pe: cs.pointerEvents, op: cs.opacity, flag: isProcessingPayment };
    });
    check(lock.cls && lock.pe === "none" && Number(lock.op) <= 0.5 && lock.flag, `вікно заблоковано (pointer-events ${lock.pe}, opacity ${lock.op})`);
    await m.screenshot({ path: path.join(OUT, "e2e-waiting.png") });
    await m.keyboard.press("Escape");
    check(await m.$("#overlay") !== null, "Escape не закриває вікно під час оплати");

    const approved = await m.waitForSelector("[data-testid=pay-wait-text]:has-text('Оплату проведено')", { timeout: 15000 }).then(() => true, () => false);
    check(approved, "емулятор схвалив — «Оплату проведено»");
    if (approved) await m.screenshot({ path: path.join(OUT, "e2e-approved.png") });
    await m.waitForSelector("#overlay", { state: "detached", timeout: 8000 }).catch(() => {});
    check(await m.$("#overlay") === null, "вікно закрилося");
    const st = await m.evaluate(async (id) => (await api.get(`/api/appointments/${id}`)).status, appt.id);
    check(st === "completed", `замовлення закрито (статус запису: ${st})`);
    const pays = await m.evaluate(async (id) => api.get(`/api/appointments/${id}/card-payments`), appt.id);
    const pay = pays[pays.length - 1] || {};
    check(pay.mode === "ecr" && pay.status === "paid" && /^\d{12}$/.test(pay.rrn || "") && !!pay.order_id, `оплата ecr/paid, RRN ${pay.rrn}`);
    await m.screenshot({ path: path.join(OUT, "e2e-today-after.png") });

    await dir.evaluate(() => api.patch("/api/settings/terminal", { enabled: false }));
  } finally {
    await browser.close();
    emu.proc.kill();
  }
  if (failures.length) { console.log(`\n${failures.length} проблем(и):\n` + failures.join("\n")); process.exit(1); }
  console.log(`\nE2E з емулятором термінала: усе гаразд (знімки — ${OUT}/e2e-*.png)`);
})().catch((e) => { console.error(e); process.exit(1); });
