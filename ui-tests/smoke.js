const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const BASE = process.argv[2] || process.env.UI_BASE || "http://localhost:8770";
const OUT = path.join(__dirname, "out");
const USERS = [["margaryta", "Director123"], ["dmitry", "Master123"]];
const MODES = [
  { name: "mob", viewport: { width: 390, height: 844 }, theme: "light" },
  { name: "mob-dark", viewport: { width: 390, height: 844 }, theme: "dark" },
  { name: "desk", viewport: { width: 1400, height: 900 }, theme: "light" },
];

const failures = [];
const fail = (where, msg) => { failures.push(`${where}: ${msg}`); console.log(`  ✗ ${msg}`); };

async function login(page, user, pass) {
  await page.goto(BASE + "/");
  await page.fill("#login-user", user);
  await page.fill("#login-pass", pass);
  await page.click("button[type=submit]");
  await page.waitForSelector(".main", { timeout: 15000 });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  for (const [user, pass] of USERS) {
    for (const mode of MODES) {
      const ctx = await browser.newContext({ viewport: mode.viewport, timezoneId: "Europe/Kyiv", locale: "uk-UA" });
      await ctx.addInitScript((t) => { try { localStorage.setItem("mds_theme", t); } catch (_) {  } }, mode.theme);
      const page = await ctx.newPage();
      let where = `${user}/${mode.name}`;
      page.on("pageerror", (e) => fail(where, "помилка JS: " + e.message));
      page.on("console", (m) => { if (m.type() === "error" && !/favicon/.test(m.text())) fail(where, "console: " + m.text()); });
      page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 500) fail(where, `сервер ${r.status()} ${new URL(r.url()).pathname}`); });

      await login(page, user, pass);
      const routes = await page.evaluate(() => [...(NAV[state.user.role] || []).map((i) => i[0]), "more", "profile"]);
      console.log(`${user} · ${mode.name}: ${routes.length} сторінок`);
      for (const r of [...new Set(routes)]) {
        where = `${user}/${mode.name}/#/${r}`;
        await page.goto(`${BASE}/#/${r}`);
        try {
          await page.waitForSelector(".main", { timeout: 15000 });
          await page.waitForSelector(".view-skel", { state: "detached", timeout: 15000 });
        } catch (_) { fail(where, "сторінка не завантажилась за 15 с"); continue; }
        await page.waitForTimeout(400);
        const res = await page.evaluate(() => {
          const W = document.documentElement.clientWidth;
          const out = { hscroll: document.documentElement.scrollWidth > W + 1 ? document.documentElement.scrollWidth : 0 };
          const plus = document.querySelector(".tab-plus span");
          if (plus && plus.offsetParent) { const b = plus.getBoundingClientRect(); out.plusOff = Math.round(b.left + b.width / 2 - W / 2); }
          const err = document.querySelector(".main > .empty");
          out.errorState = err && /помилк|error|не вдалося/i.test(err.textContent) ? err.textContent.trim().slice(0, 80) : "";
          return out;
        });
        if (res.hscroll) fail(where, `горизонтальна прокрутка сторінки: ${res.hscroll}px > ширини`);
        if (res.plusOff !== undefined && Math.abs(res.plusOff) > 2) fail(where, `«+» не по центру (зсув ${res.plusOff}px)`);
        if (res.errorState) fail(where, "замість сторінки помилка: " + res.errorState);
        if (r === "logic") {
          await page.waitForTimeout(2500);
          const nodes = await page.evaluate(() => [...document.querySelectorAll(".mermaid-box svg")].map((s) => s.querySelectorAll("g.node").length));
          if (nodes.length !== 3 || nodes.some((n) => n < 5)) fail(where, `схеми «Логіки системи» не побудовані: ${JSON.stringify(nodes)}`);
        }
        await page.screenshot({ path: path.join(OUT, `${user}-${mode.name}-${r}.png`), fullPage: mode.name === "desk" ? false : true });
      }
      await ctx.close();
    }
  }
  await browser.close();
  if (failures.length) {
    console.log(`\n${failures.length} проблем(и):\n` + failures.join("\n"));
    process.exit(1);
  }
  console.log("\nUI-перевірка: усе гаразд");
})().catch((e) => { console.error(e); process.exit(1); });
