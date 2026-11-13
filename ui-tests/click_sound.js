const fs = require("fs");
const http = require("http");
const { chromium } = require("playwright");
const path = require("path");

const APP = path.join(__dirname, "../server/mds/web/static/app.js");

(async () => {
  const src = fs.readFileSync(APP, "utf8");
  if (!src.includes("Звуки та вібрація інтерфейсу")) throw new Error("немає перемикача в профілі");
  if (!src.includes("function playClickSound()")) throw new Error("немає playClickSound");

  const server = http.createServer((_, res) => {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end("<div id=\"root\"></div>");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;

  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}/`);
  await page.addScriptTag({ path: APP });
  await page.waitForSelector("button.pw-eye");
  await page.evaluate(() => {
    window.__ticks = 0;
    const orig = playClickSound;
    playClickSound = function () { window.__ticks += 1; return orig(); };
  });

  await page.click("button.pw-eye");
  const first = await page.evaluate(() => window.__ticks);
  if (first !== 1) throw new Error("перший клік не зіграв звук: " + first);

  await page.evaluate(() => localStorage.setItem("ui_sounds", false));
  await page.waitForTimeout(350);
  await page.click("button.pw-eye");
  const muted = await page.evaluate(() => window.__ticks);
  if (muted !== 1) throw new Error("звук не вимкнувся: " + muted);

  await page.evaluate(() => localStorage.setItem("ui_sounds", true));
  await page.waitForTimeout(350);
  await page.click("button[type=submit]");
  const again = await page.evaluate(() => window.__ticks);
  if (again !== 2) throw new Error("після увімкнення очікував ще один звук: " + again);
  await page.evaluate(() => playClickSound());
  if (errors.length) throw new Error("помилки JS: " + errors.join("; "));

  await browser.close();
  server.close();
  console.log("click_sound: ок");
})().catch((e) => { console.error(e); process.exit(1); });
