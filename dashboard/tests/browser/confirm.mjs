// 確認ダイアログと CSP をブラウザで確かめる（pytest には入れない。dashboard の依存に playwright を足さない）。
//
//   cd dashboard && AIL_DEMO=1 AIL_PORT=3019 .venv/bin/python -m app.main &        # デモで起動（本物の記録に触れない）
//   (cd <どこか> && npm i playwright@1.58)                                          # chromium-1208 と対の版
//   PW_DIR=<どこか> node dashboard/tests/browser/confirm.mjs http://127.0.0.1:3019
//
// ⚠ **POST はブラウザ側で止める**（route で受けて偽の応答を返す）。「受けると送られる」を見ても HALT は書かれない。
// 見るもの: (1) ダイアログが出る (2) 断ると送られない (3) 受けると送られる (4) CSP 違反 0 件
import { createRequire } from "node:module";

const require = createRequire((process.env.PW_DIR || process.cwd()) + "/");
const { chromium } = require("playwright");
const base = process.argv[2] || "http://127.0.0.1:3019";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const violations = [];
const posts = [];
let dialogs = [];
let answer = "dismiss";
page.on("console", (m) => { if (/Content Security Policy|violates/i.test(m.text())) violations.push(`${page.url()} :: ${m.text().slice(0, 160)}`); });
page.on("dialog", async (d) => { dialogs.push(d.message()); if (answer === "accept") await d.accept(); else await d.dismiss(); });
await page.route("**/ops/**", async (route) => {
  if (route.request().method() !== "POST") return route.continue();
  posts.push(new URL(route.request().url()).pathname);
  await route.fulfill({ status: 200, contentType: "text/html", body: "<p>intercepted</p>" });
});

const results = [];
function check(name, ok, detail = "") { results.push({ name, ok, detail }); }

async function tryForm(path, selector, label) {
  for (const ans of ["dismiss", "accept"]) {
    await page.goto(base + path, { waitUntil: "load" });
    const form = page.locator(selector).first();
    if (!(await form.count())) { check(`${label}: form がある`, false, `${path} に ${selector} が無い`); return; }
    dialogs = []; posts.length = 0; answer = ans;
    await form.locator("button").first().click();
    await page.waitForTimeout(400);
    check(`${label}: ダイアログが出る（${ans}）`, dialogs.length === 1, dialogs[0] || "出なかった");
    if (ans === "dismiss") check(`${label}: 断ると送られない`, posts.length === 0, posts.join(","));
    else check(`${label}: 受けると送られる`, posts.length === 1, posts.join(","));
  }
}

await tryForm("/", 'form[action="/ops/halt"]', "概要の右上の停止");
await tryForm("/ops", 'section form[action="/ops/halt"]', "操作の停止");
await tryForm("/ops", 'form[action="/ops/cleanup"]', "操作の後片付け");

// CSP 違反（インラインの style ／ スクリプト）が 1 件も無いこと
for (const path of ["/", "/overall", "/records", "/judge", "/ops", "/dev"]) {
  await page.goto(base + path, { waitUntil: "load" });
  await page.waitForTimeout(200);
}
const firstRun = await page.evaluate(async () => (await (await fetch("/api/records")).json()));
const ids = (firstRun.runs || firstRun || []).map((r) => r.run_id).filter(Boolean);
if (ids.length) {
  await page.goto(`${base}/records/${ids[0]}`, { waitUntil: "load" });
  if (ids.length > 1) await page.goto(`${base}/records/diff?a=${ids[1]}&b=${ids[0]}`, { waitUntil: "load" });
  await page.waitForTimeout(200);
}
check("CSP 違反 0 件", violations.length === 0, violations.join("\n"));

await browser.close();
let failed = 0;
for (const r of results) { if (!r.ok) failed++; console.log(`${r.ok ? "✅" : "❌"} ${r.name}${r.ok && !r.detail ? "" : " — " + r.detail}`); }
process.exit(failed ? 1 : 0);
