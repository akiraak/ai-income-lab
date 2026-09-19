// i マークのヘルプをブラウザで確かめる（pytest には入れない。dashboard の依存に playwright を足さない。dashboard.md §15-10）。
//
//   cd dashboard && AIL_DEMO=1 AIL_PORT=3019 .venv/bin/python -m app.main &        # デモで起動（本物の記録に触れない）
//   PW_DIR=<playwright を入れた場所> node dashboard/tests/browser/help.mjs http://127.0.0.1:3019 [撮る先.png]
//
// 見るもの: 各画面に出る・<p> の中に無い ／ 押すと開く・1 つだけ開く・右端と下端で倒れて収まる ／ Escape・外を押すと閉じる ／
//           Tab → Enter で開く ／ 開いている間は部分更新で閉じない ／ 停止ボタンの横に置かない ／ CSP 違反 0 件
import { createRequire } from "node:module";
const require = createRequire((process.env.PW_DIR || process.cwd()) + "/");
const { chromium } = require("playwright");
const base = process.argv[2] || "http://127.0.0.1:3019";
const out = process.argv[3] || "";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const violations = [];
page.on("console", (m) => { if (/Content Security Policy|violates/i.test(m.text())) violations.push(m.text().slice(0, 160)); });
const res = [];
const check = (n, ok, d = "") => res.push(`${ok ? "OK " : "NG "} ${n} ${d}`);
for (const path of ["/", "/overall", "/traders/mock_a", "/records", "/judge", "/ops"]) {
  await page.goto(base + path, { waitUntil: "load" });
  const n = await page.locator("details.help").count();
  check(`${path}: i マーク`, n > 0, `${n} 個`);
  // <p> が閉じて崩れていないか: details.help の親に p が無い
  const inP = await page.evaluate(() => [...document.querySelectorAll("details.help")].filter((d) => d.closest("p")).length);
  check(`${path}: p の中に無い`, inP === 0);
}
await page.goto(base + "/", { waitUntil: "load" });
const first = page.locator(".stat details.help").first();
await first.locator("summary").click();
check("押すと開く", await first.evaluate((d) => d.open));
check("吹き出しが見える", await first.locator(".help-pop").isVisible());
const box = await first.locator(".help-pop").boundingBox();
check("画面の内に収まる", box.x >= 0 && box.x + box.width <= 1280, JSON.stringify(box));
// 2 つ目を開くと 1 つ目が閉じる
const last = page.locator(".stat details.help").last();
await last.locator("summary").click();
await page.waitForTimeout(150);
check("1 つだけ開く", (await page.locator("details.help[open]").count()) === 1);
const lb = await last.locator(".help-pop").boundingBox();
check("右端でも収まる", lb.x >= 0 && lb.x + lb.width <= 1280, JSON.stringify(lb));
if (out) { await page.screenshot({ path: out, fullPage: false }); }
await page.keyboard.press("Escape");
check("Escape で閉じる", (await page.locator("details.help[open]").count()) === 0);
check("Escape の後 summary に戻る", await page.evaluate(() => document.activeElement && document.activeElement.tagName === "SUMMARY"));
// キーボード: summary に focus → Enter
await last.locator("summary").focus();
await page.keyboard.press("Enter");
check("Enter で開く", await last.evaluate((d) => d.open));
await page.mouse.click(640, 700);
check("外を押すと閉じる", (await page.locator("details.help[open]").count()) === 0);
// 部分更新の枠の中: 開いたまま 6 秒待っても閉じない
const st = page.locator("#strip details.help").first();
await st.locator("summary").click();
await page.waitForTimeout(6500);
check("部分更新で閉じない", await st.evaluate((d) => d.isConnected && d.open));
await page.keyboard.press("Escape");
// フッタの i（下端 → 上に倒す）
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
const ft = page.locator("footer details.help").first();
await ft.locator("summary").click();
await page.waitForTimeout(150);
const fb = await ft.locator(".help-pop").boundingBox();
const vh = 900;
check("フッタの i は上に開いて収まる", fb.y >= 0 && fb.y + fb.height <= vh && fb.x + fb.width <= 1280, JSON.stringify(fb));
if (out) { await page.screenshot({ path: out.replace(".png", "-foot.png") }); }
await page.keyboard.press("Escape");
check("停止ボタンの横に i を置かない", (await page.locator(".envbar details.help").count()) === 0);
// トレーダーの詳細と全体の詳細の見た目
await page.goto(base + "/traders/mock_a", { waitUntil: "load" });
await page.locator(".tile details.help").nth(1).locator("summary").click();
await page.waitForTimeout(150);
if (out) { await page.screenshot({ path: out.replace(".png", "-trader.png") }); }
await page.goto(base + "/overall", { waitUntil: "load" });
await page.locator(".helprow details.help").nth(2).locator("summary").click();
await page.waitForTimeout(150);
if (out) { await page.screenshot({ path: out.replace(".png", "-overall.png") }); }
check("CSP 違反 0 件", violations.length === 0, violations.join(" | "));
console.log(res.join("\n"));
await browser.close();
process.exit(res.some((r) => r.startsWith("NG")) ? 1 : 0);
