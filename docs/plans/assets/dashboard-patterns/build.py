#!/usr/bin/env python3
"""管理画面の画面構成 3 パターンのモックを作る（docs/plans/archive/dashboard-required-features.md Phase 5）。

    fixtures/run.sh /tmp/lt3                                  # 3 人 × 20 営業日のモックの記録（10-19 は起動しない）
    python3 build.py --live-dir /tmp/lt3 --monitor fixtures/monitor-demo.json
    → vibeboard の Plans タブ（assets › dashboard-patterns）で開く

構成（利用者の決定 2026-09-18）: 上に全体の概要、その下にトレーダー。各トレーダーの詳細はクリックで進む。
3 パターンで変えるのは**トレーダーの一覧の見せ方**だけ（1 表 ／ 2 カード ／ 3 段）。概要と詳細ページは共通。

⚠ 本番のコード（dashboard/app/）には入れない。`dashboard/app/live.py` は記録を読むのに使うだけ。
⚠ CSP と同じ制約で書く: スクリプトなし・インラインの style なし（幾何は SVG の属性、色はクラス）。
⚠ 写る数字はモックの値で【実測】ではない。損益には勝ち負けの色を付けない（CLAUDE.md の例外・dashboard.md §15-2）。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, timedelta
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "dashboard"))
from app import live as lv  # noqa: E402

# vibeboard の Plans タブは .html を /api/design/plans/<パス> の iframe で開くので、相対パスの CSS は届かない。
# vibeboard がプロジェクト直下を配信している /files/ から読む（⚠ file:// で直接開くと CSS が当たらない）
CSS = ["/files/dashboard/app/static/app.css", "/files/docs/plans/assets/dashboard-patterns/patterns.css"]
PATTERNS = [("pattern-1-table.html", "1 表"), ("pattern-2-cards.html", "2 カード"), ("pattern-3-lanes.html", "3 段")]
ACT_LABEL = {"buy": "買い", "sell": "売り", "hold": "保有", "skip": "見送り", "none": "動きなし", "nostart": "起動なし"}
SKIP_KINDS = ("too_small", "over_budget", "over_day_cap", "no_quote")


def h(x) -> str:
    return escape(str(x), quote=True)


def fmt_usd(v: float) -> str:
    return ("−" if v < 0 else "＋" if v > 0 else "") + f"${abs(v):,.2f}"


def fmt_pct(v: float) -> str:
    return ("−" if v < 0 else "＋" if v > 0 else "") + f"{abs(v):.2f}%"


def status_of(m: float | None) -> str:
    # 差 1 の中央値の色分け（dashboard.md §15-4。閾値の正本は実売買のプラン §2-4）
    return "na" if m is None else ("ok" if m <= 5 else ("warn" if m <= 10 else "ng"))


MARK = {"ok": "✅", "warn": "⚠", "ng": "❌", "na": "—"}


# ================================================================ データ

def business_days(first: str, last: str) -> list[str]:
    # ⚠ 休場日の暦はまだ無い（N7 の実装で足す）。モックの 2026-10 は平日がすべて営業日
    d, end, out = date.fromisoformat(first), date.fromisoformat(last), []
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def load(live_dir: Path) -> dict:
    tr = lv.traders(live_dir)
    ds = sorted(lv.dates(live_dir))
    days = {d: lv.day(live_dir, d) for d in ds}
    bd = business_days(ds[0], ds[-1])
    missing = [d for d in bd if d not in days]
    st = lv.states(live_dir)
    for i, t in enumerate(tr):
        name = t["name"]
        t["cls"] = f"s{i + 1}"
        ledger = {d: r for d in ds for r in days[d]["ledger"] if r.get("trader") == name}
        t["pnl_usd"] = [(ledger[d]["realized_usd"] + ledger[d]["unrealized_usd"]) if d in ledger else None for d in bd]
        t["pnl_pct"] = [(v / t["budget_usd"] * 100) if v is not None and t["budget_usd"] else None for v in t["pnl_usd"]]
        last = ledger[ds[-1]] if ds[-1] in ledger else {}
        t["last"] = last
        t["state"] = (st.get("cert") or {}).get(name) or {}
        # 行動のマス目（銘柄 × 営業日）
        grid: dict[str, list[dict]] = {s: [] for s in t["symbols"]}
        diff1: list[tuple[str, float]] = []
        n_orders = n_filled = n_transfers = 0
        for d in bd:
            dd = days.get(d)
            for s in t["symbols"]:
                if dd is None:
                    grid[s].append({"a": "nostart", "tip": f"{d} {s}: 起動なし（営業日なのに執行器の記録が無い）"})
                    continue
                sig = next((x for x in dd["signals"] if x.get("trader") == name and x.get("symbol") == s), None)
                sig_t = f"（合図 買い {sig['buy']:.0f} ／ 出口 {sig['exit']:.0f}）" if sig else ""
                act, detail = "none", ""
                for o in dd["orders"]:
                    part = next((p for p in o.get("parts") or [] if p.get("trader") == name), None)
                    if part and o.get("symbol") == s and o.get("fills_qty"):
                        act = o["side"]
                        detail = f"{part['shares']} 株 @ {o['fill_price']:.2f}"
                for x in dd["transfers"]:
                    if x.get("symbol") == s and name in (x.get("buyer"), x.get("seller")):
                        act = "buy" if x.get("buyer") == name else "sell"
                        other = x.get("seller") if act == "buy" else x.get("buyer")
                        detail = f"{x['shares']} 株 @ {x['price']:.2f}（内部移転。相手 {other}）"
                if act == "none" and any(e.get("kind") in SKIP_KINDS and e.get("trader") == name and e.get("symbol") == s for e in dd["events"]):
                    act = "skip"
                    detail = next(e["kind"] for e in dd["events"] if e.get("kind") in SKIP_KINDS and e.get("trader") == name and e.get("symbol") == s)
                held = s in ((ledger.get(d) or {}).get("holdings") or {})
                if act == "none" and held:
                    act = "hold"
                grid[s].append({"a": act, "tip": f"{d} {s}: {ACT_LABEL[act]} {detail}{sig_t}".strip()})
            if dd is None:
                continue
            for o in dd["orders"]:
                if name in o["traders"]:
                    n_orders += 1
                    n_filled += 1 if o.get("final_status") == "Filled" else 0
                    diff1 += [(d, v) for v in o["diff1_bp"]]
            n_transfers += sum(1 for x in dd["transfers"] if name in (x.get("buyer"), x.get("seller")))
        t["grid"] = grid
        t["diff1"] = diff1
        t["diff1_median"] = lv._median([v for _, v in diff1])
        t["n_orders"], t["n_filled"], t["n_transfers"] = n_orders, n_filled, n_transfers
        t["today"] = [(s, grid[s][-1]["a"]) for s in t["symbols"]]
    all_diff1 = [v for t in tr for _, v in t["diff1"]]
    latest = days[ds[-1]]
    return {"traders": tr, "bd": bd, "missing": missing, "days": days, "dates": ds, "latest": latest,
            "diff1_median": lv._median([v for d in ds for o in days[d]["orders"] for v in o["diff1_bp"]]),
            "diff1_n": len(all_diff1),
            "orders": sum(days[d]["n_orders"] for d in ds), "filled": sum(days[d]["n_filled"] for d in ds),
            "bad": sum(days[d]["n_bad"] for d in ds), "retries": sum(days[d]["retries"] for d in ds),
            "problem_days": sum(1 for d in ds if days[d]["problems"] or days[d]["n_bad"]),
            "transfers": sum(len(days[d]["transfers"]) for d in ds)}


# ================================================================ SVG

def nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    span = hi - lo or 1.0
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    out, v = [], start
    while v <= hi + step * 0.001:
        out.append(round(v, 10))
        v += step
    return out


def line_chart(series: list[dict], bd: list[str], missing: list[str], *, w: int, hgt: int, fmt, label_fmt=None,
               pad_l: int = 52, pad_r: int = 120, pad_t: int = 14, pad_b: int = 22, show_x: bool = True,
               end_labels: bool = True, aria: str = "", domain: tuple[float, float] | None = None, n_ticks: int = 4,
               miss_text: bool = True) -> str:
    """折れ線（1 軸だけ）。欠けた営業日は線を切り、帯で示す。日ごとの透明な帯に全系列の値を <title> で持たせる（CSP の内の hover）。"""
    vals = [v for s in series for v in s["pts"] if v is not None] + [0.0]
    lo, hi = domain or (min(vals), max(vals))
    ticks = nice_ticks(lo, hi, n_ticks)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])
    n = len(bd)
    pw, ph = w - pad_l - pad_r, hgt - pad_t - pad_b
    x = lambda i: pad_l + (i * pw / (n - 1) if n > 1 else pw / 2)  # noqa: E731
    y = lambda v: pad_t + (hi - v) / (hi - lo or 1) * ph  # noqa: E731
    step = pw / (n - 1) if n > 1 else pw
    o = [f'<svg class="chart" viewBox="0 0 {w} {hgt}" role="img" aria-label="{h(aria)}">']
    for tv in ticks:
        cls = "zero" if abs(tv) < 1e-12 else "grid"
        o.append(f'<line class="{cls}" x1="{pad_l}" x2="{pad_l + pw}" y1="{y(tv):.1f}" y2="{y(tv):.1f}"/>')
        o.append(f'<text class="tick" x="{pad_l - 6}" y="{y(tv) + 3:.1f}" text-anchor="end">{h(fmt(tv))}</text>')
    for d in missing:
        i = bd.index(d)
        o.append(f'<rect class="miss" x="{x(i) - step / 2:.1f}" y="{pad_t}" width="{step:.1f}" height="{ph}"><title>{d}: 起動なし（営業日なのに執行器の記録が無い）</title></rect>')
        if miss_text:
            o.append(f'<text class="miss-t" x="{x(i):.1f}" y="{pad_t + 10}" text-anchor="middle">起動なし</text>')
    if show_x:
        for i, d in enumerate(bd):
            if i % 5 == 0 or i == n - 1:
                o.append(f'<text class="tick" x="{x(i):.1f}" y="{hgt - 6}" text-anchor="middle">{d[5:]}</text>')
    for s in series:
        segs, cur = [], []
        for i, v in enumerate(s["pts"]):
            if v is None:
                if cur:
                    segs.append(cur)
                cur = []
            else:
                cur.append((x(i), y(v)))
        if cur:
            segs.append(cur)
        d_attr = " ".join("M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in seg) for seg in segs)
        o.append(f'<g class="{s["cls"]}"><path class="ln" d="{d_attr}"/></g>')
    # 端の点と、重ならないように押し分けた端のラベル（押し分けたら引き出し線でつなぐ）
    ends = []
    for s in series:
        idx = max((i for i, v in enumerate(s["pts"]) if v is not None), default=None)
        if idx is None:
            continue
        ends.append({"s": s, "i": idx, "px": x(idx), "py": y(s["pts"][idx]), "ly": y(s["pts"][idx])})
        o.append(f'<circle class="dt {s["cls"]}" cx="{x(idx):.1f}" cy="{y(s["pts"][idx]):.1f}" r="4"/>')
    if end_labels and ends:
        ends.sort(key=lambda e: e["py"])
        for k in range(1, len(ends)):
            if ends[k]["ly"] - ends[k - 1]["ly"] < 13:
                ends[k]["ly"] = ends[k - 1]["ly"] + 13
        for e in ends:
            lx = e["px"] + 10
            if abs(e["ly"] - e["py"]) > 0.5:
                o.append(f'<line class="axis" x1="{e["px"] + 4:.1f}" y1="{e["py"]:.1f}" x2="{lx - 2:.1f}" y2="{e["ly"]:.1f}"/>')
            v = e["s"]["pts"][e["i"]]
            lab = label_fmt(e["s"], e["i"]) if label_fmt else fmt(v)
            name = f'{h(e["s"]["name"])} ' if len(series) > 1 else ""
            o.append(f'<text class="lab" x="{lx:.1f}" y="{e["ly"] + 4:.1f}">{name}<tspan class="lab-m">{h(lab)}</tspan></text>')
    for i, d in enumerate(bd):
        parts = [d] + [f'{s["name"]}: {s["tips"][i]}' for s in series if s.get("tips") and s["tips"][i]]
        o.append(f'<rect class="hit" x="{x(i) - step / 2:.1f}" y="{pad_t}" width="{step:.1f}" height="{ph}"><title>{h(chr(10).join(parts))}</title></rect>')
    o.append("</svg>")
    return "".join(o)


def sparkline(t: dict, bd: list[str], missing: list[str], domain, w: int = 140, hgt: int = 30) -> str:
    return line_chart([{"cls": t["cls"], "name": "", "pts": t["pnl_pct"], "tips": pnl_tips(t)}], bd, missing, w=w, hgt=hgt,
                      fmt=lambda v: "", pad_l=2, pad_r=6, pad_t=4, pad_b=4, show_x=False, end_labels=False,
                      aria=f'{t["name"]} の損益の推移', domain=domain, n_ticks=2, miss_text=False)


def pnl_domain(D: dict) -> tuple[float, float]:
    # ⚠ トレーダーを並べる小さな図は縦軸を揃える（ばらばらだと見比べを誤る）
    vals = [v for t in D["traders"] for v in t["pnl_pct"] if v is not None] + [0.0]
    return min(vals), max(vals)


def pnl_tips(t: dict) -> list[str]:
    return [f"{fmt_pct(p)}（{fmt_usd(u)}）" if p is not None else "" for p, u in zip(t["pnl_pct"], t["pnl_usd"])]


def diff1_dots(traders: list[dict], *, w: int = 520) -> str:
    """差 1 の中央値（トレーダー別）。背景は閾値の帯（状態の色は付けてよい: 判定の対象は執行の差。§15-4）。"""
    vals = [v for t in traders for _, v in t["diff1"]]
    lo = min([0.0] + vals) - 2
    hi = max([15.0] + vals) + 3
    pad_l, pad_r, row = 70, 70, 26
    hgt = 22 + row * len(traders) + 18
    pw = w - pad_l - pad_r
    x = lambda v: pad_l + (v - lo) / (hi - lo) * pw  # noqa: E731
    o = [f'<svg class="chart" viewBox="0 0 {w} {hgt}" role="img" aria-label="トレーダー別の差 1 の中央値">']
    top, bot = 16, hgt - 18
    for cls, a, b, t in (("band-ok", max(lo, 0), 5, "✅ ≤ 5"), ("band-warn", 5, 10, "⚠ 5〜10"), ("band-ng", 10, hi, "❌ ＞ 10")):
        o.append(f'<rect class="{cls}" x="{x(a):.1f}" y="{top}" width="{x(b) - x(a):.1f}" height="{bot - top}"/>')
        o.append(f'<text class="band-t" x="{(x(a) + x(b)) / 2:.1f}" y="{top - 4}" text-anchor="middle">{t}</text>')
    for tv in nice_ticks(lo, hi, 5):
        if lo <= tv <= hi:
            o.append(f'<text class="tick" x="{x(tv):.1f}" y="{hgt - 5}" text-anchor="middle">{tv:g}</text>')
    o.append(f'<line class="zero" x1="{x(0):.1f}" x2="{x(0):.1f}" y1="{top}" y2="{bot}"/>')
    for k, t in enumerate(traders):
        cy = top + row * k + row / 2 + 2
        vs = [v for _, v in t["diff1"]]
        o.append(f'<text class="lab" x="{pad_l - 8}" y="{cy + 4:.1f}" text-anchor="end">{h(t["name"])}</text>')
        if not vs:
            o.append(f'<text class="lab-m" x="{pad_l + 6}" y="{cy + 4:.1f}">約定なし</text>')
            continue
        o.append(f'<g class="{t["cls"]}"><path class="ln" d="M{x(min(vs)):.1f},{cy:.1f} L{x(max(vs)):.1f},{cy:.1f}"/></g>')
        m = t["diff1_median"]
        o.append(f'<circle class="dt {t["cls"]}" cx="{x(m):.1f}" cy="{cy:.1f}" r="5"><title>{h(t["name"])}: 中央値 {m} bp（n={len(vs)}・最小 {min(vs)}・最大 {max(vs)}）</title></circle>')
        o.append(f'<text class="lab" x="{w - pad_r + 8}" y="{cy + 4:.1f}">{m} bp</text>')
    o.append("</svg>")
    return "".join(o)


def action_grid(t: dict, bd: list[str], *, w: int, pad_l: int = 52, pad_r: int = 120, cell_h: int = 20, show_x: bool = True) -> str:
    """行動のマス目（銘柄 × 営業日）。⚠ 状態の色は使わない。系列の色の濃淡（保有は薄く、売買は濃く ＋ 文字）で分ける。"""
    n = len(bd)
    pw = w - pad_l - pad_r
    cw = pw / n
    rows = t["symbols"]
    hgt = cell_h * len(rows) + (16 if show_x else 2)
    o = [f'<svg class="chart" viewBox="0 0 {w} {hgt}" role="img" aria-label="{h(t["name"])} の行動（銘柄 × 日）">']
    for r, s in enumerate(rows):
        yy = r * cell_h
        o.append(f'<text class="tick" x="{pad_l - 6}" y="{yy + cell_h / 2 + 3:.1f}" text-anchor="end">{h(s)}</text>')
        for i, c in enumerate(t["grid"][s]):
            a = c["a"]
            cls = {"none": "c-none", "hold": f'c-hold {t["cls"]}', "buy": f'c-buy {t["cls"]}', "sell": f'c-sell {t["cls"]}',
                   "skip": "c-skip", "nostart": "c-nostart"}[a]
            xx = pad_l + i * cw
            o.append(f'<rect class="{cls}" x="{xx + 1:.1f}" y="{yy + 1}" width="{cw - 2:.1f}" height="{cell_h - 2}" rx="2"><title>{h(c["tip"])}</title></rect>')
            txt = {"buy": ("c-t", "買"), "sell": ("c-t", "売"), "skip": ("c-t-m", "見"), "nostart": ("c-t-ng", "✕")}.get(a)
            if txt:
                o.append(f'<text class="{txt[0]}" x="{xx + cw / 2:.1f}" y="{yy + cell_h / 2 + 3.5:.1f}" text-anchor="middle">{txt[1]}</text>')
    if show_x:
        for i, d in enumerate(bd):
            if i % 5 == 0 or i == n - 1:
                o.append(f'<text class="tick" x="{pad_l + i * cw + cw / 2:.1f}" y="{hgt - 3}" text-anchor="middle">{d[5:]}</text>')
    o.append("</svg>")
    return "".join(o)


# ================================================================ HTML の部品

ASSET = "/files/docs/plans/assets/dashboard-patterns/"
MOCKBAR_1 = ("🧪 <b>モック</b> — 画面構成の 3 パターンを比べるためのもの（Phase 5 の 1 回目）。⚠ <b>数字は執行器のモック 20 営業日の値で【実測】ではない</b>。"
             "トレーダーは試験用の 3 人（mock_a ／ mock_b ／ mock_c）。10-19 はわざと起動していない（「起動しなかった日」の見え方の確認）")


def page(title: str, body: str, active: str, *, nav: str | None = None, css_extra: tuple[str, ...] = (), body_class: str = "",
         mockbar: str = MOCKBAR_1, side: str | None = None) -> str:
    links = "".join(f'<link rel="stylesheet" href="{c}">' for c in list(CSS) + [ASSET + c for c in css_extra])
    if nav is None:
        nav = "".join(f'<a href="{f}" class="{"on" if active == f else ""}">概要（{lab}）</a>' for f, lab in PATTERNS)
        nav += '<a href="#">操作</a><a href="#">記録と判定 <span class="small muted">（観点 A まで）</span></a>'
    if side is not None:
        return _page_with_side(title, body, links, side, body_class, mockbar)
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)}</title>
{links}
</head>
<body class="{body_class}">
<header class="top">
  <div class="brand">ai-income-lab <span class="muted">管理画面</span> <span class="ver">モック</span></div>
  <nav>{nav}</nav>
  <div class="who"><span class="pill pill-local">ローカル面 · ループバックのみ</span></div>
</header>
<div class="envbar">
  <span class="badge badge-cert">CERT</span><span class="badge badge-mock">MOCK</span>
  <span class="badge badge-prod">PROD</span><span class="badge badge-mock">MOCK</span>
  <span class="muted small">scope: read trade openid</span>
  <span class="grow"></span>
  <button class="btn btn-danger" type="button">■ 停止</button>
</div>
<div class="mockbar">{mockbar}</div>
<main>
{body}
</main>
<footer class="foot"><span>モック · build.py で生成</span><span class="muted">docs/plans/assets/dashboard-patterns/</span></footer>
</body>
</html>
"""


def _page_with_side(title: str, body: str, links: str, side: str, body_class: str, mockbar: str) -> str:
    """左ペインの形（2 回目のデザイン）。ナビとトレーダーとデザインの切り替えを左に縦に並べ、停止ボタンは右の上に残す。"""
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)}</title>
{links}
</head>
<body class="{body_class}">
<div class="shell">
<aside class="side">
{side}
</aside>
<div class="maincol">
<div class="envbar">
  <span class="badge badge-cert">CERT</span><span class="badge badge-mock">MOCK</span>
  <span class="badge badge-prod">PROD</span><span class="badge badge-mock">MOCK</span>
  <span class="muted small">scope: read trade openid</span>
  <span class="grow"></span>
  <button class="btn btn-danger" type="button">■ 停止</button>
</div>
<div class="mockbar">{mockbar}</div>
<main>
{body}
</main>
<footer class="foot"><span>モック · build.py で生成</span><span class="muted">docs/plans/assets/dashboard-patterns/</span></footer>
</div>
</div>
</body>
</html>
"""


def tiles(mon: dict) -> str:
    ms = mon["monitors"]
    out = []
    for env in ("cert", "prod"):
        m = ms.get(env) or {}
        a = m.get("auth") or {}
        ok = a.get("ok")
        out.append(f'<div class="tile env-{env}"><div class="k">{env.upper()} の認証</div>'
                   f'<div class="v">{"✅ 認証中" if ok else "❌ 未認証"}</div>'
                   f'<div class="s">残り {a.get("remaining_s", 0):.0f} 秒 · 更新 {a.get("refresh_ok", 0)} ／ 失敗 {a.get("refresh_fail", 0)}</div></div>')
    p = ms.get("prod") or {}
    b = p.get("balances") or {}
    out.append(f'<div class="tile"><div class="k">口座（PROD）</div><div class="v num">${float(b.get("cash-balance") or 0):,.0f}</div>'
               f'<div class="s">買付余力 ${float(b.get("equity-buying-power") or 0):,.0f}</div></div>')
    s, dx, q = p.get("account_stream") or {}, p.get("dxlink") or {}, p.get("quote") or {}
    conn_ok = s.get("state") == "connected" and dx.get("state") == "connected"
    out.append(f'<div class="tile"><div class="k">接続（PROD）</div><div class="v">{"✅ 接続中" if conn_ok else "⚠ 切断あり"}</div>'
               f'<div class="s">2 本 · 再接続 {s.get("reconnects", 0) + dx.get("reconnects", 0)} · 遅延 {q.get("delay_s", 0):.2f} 秒</div></div>')
    wo = sum(len((ms.get(e) or {}).get("live_orders") or []) for e in ("cert", "prod"))
    out.append(f'<div class="tile"><div class="k">働いている注文</div><div class="v num">{wo} 件</div><div class="s">cert ＋ prod</div></div>')
    ev = mon.get("events") or []
    lastk = ev[0]["kind"] if ev else "—"
    out.append(f'<div class="tile"><div class="k">監視の事象</div><div class="v num">{len(ev)} 件</div><div class="s">最新: {h(lastk)}</div></div>')
    return '<div class="tiles">' + "".join(out) + "</div>"


def legend(traders: list[dict]) -> str:
    return '<div class="legend">' + "".join(f'<span class="item"><i class="sw {t["cls"]}"></i>{h(t["name"])}</span>' for t in traders) + "</div>"


def pnl_panel(D: dict, *, w: int = 760, hgt: int = 230) -> str:
    tr, bd, miss = D["traders"], D["bd"], D["missing"]
    pnl = line_chart([{"cls": t["cls"], "name": t["name"], "pts": t["pnl_pct"], "tips": pnl_tips(t)} for t in tr], bd, miss,
                     w=w, hgt=hgt, fmt=lambda v: f"{v:+.1f}%" if v else "0%",
                     label_fmt=lambda s, i: fmt_pct(s["pts"][i]), aria="トレーダー別の損益の推移（予算に対する %）")
    return f"""<div class="panel">
    <h3>損益の推移 <span class="small">予算に対する %（実現 ＋ 含み）· 並びは設定の順</span></h3>
    {legend(tr)}
    {pnl}
    <p class="rule">⚠ 損益で手法を採らない（20 営業日では統計的に判定できない）。勝ち負けの色は付けない。紙上の損益は Phase 3（紙上の対照）の後で重ねる</p>
  </div>"""


def diff_panel(D: dict) -> str:
    tr, miss = D["traders"], D["missing"]
    m = D["diff1_median"]
    st = status_of(m)
    return f"""<div class="panel">
    <h3>執行の差 <span class="small">判定の対象はこちら</span></h3>
    <div class="legend"><span class="item">差 1（気配 → 約定）の中央値 <span class="chip {st}">{MARK[st]} {m if m is not None else "—"} bp</span> · n={D["diff1_n"]}</span></div>
    {diff1_dots(tr)}
    <div class="legend"><span class="item">差 2 手数料 {fmt_usd(sum(t["last"].get("fees_usd", 0) for t in tr)) or "$0.00"}</span><span class="item">差 3 紙上 − 実物 <span class="muted">Phase 3 の後</span></span>
      <span class="item">差 4 問題のあった日 {D["problem_days"]} · 再送 {D["retries"]} · <b class="ng">起動なし {len(miss)} 日</b></span></div>
  </div>"""


def latest_panel(D: dict) -> str:
    latest = D["latest"]
    rows = []
    for o in latest["orders"]:
        who = " · ".join(f'{p["trader"]} {p["shares"]}' for p in o.get("parts") or [])
        rows.append(f'<tr><td>{h(who)}</td><td>{h(o["symbol"])}</td><td>{"買い" if o["side"] == "buy" else "売り"}</td>'
                    f'<td class="num">{o["fill_price"]:.2f} × {o["fills_qty"]:g}</td><td class="num">{" ".join(str(v) for v in o["diff1_bp"]) or "—"}</td>'
                    f'<td class="cell-{o["status_class"]}">{h(o["final_status"])}</td></tr>')
    for x in latest["transfers"]:
        rows.append(f'<tr><td>{h(x["seller"])} → {h(x["buyer"])}</td><td>{h(x["symbol"])}</td><td>内部移転</td><td class="num">{x["price"]:.2f} × {x["shares"]:g}</td><td class="num">—</td><td class="cell-na">口座に出ない</td></tr>')
    today = ('<div class="scroll-x"><table class="small orders"><tr><th>誰の分</th><th>銘柄</th><th>売買</th><th class="num">約定</th><th class="num">差 1 bp</th><th>状態</th></tr>'
             + "".join(rows) + "</table></div>") if rows else '<p class="muted small">注文なし</p>'
    return f"""<div class="panel" id="today">
  <h3>最新の日 {latest["date"]} <span class="small">注文 {latest["n_orders"]} 件 · 約定 {latest["n_filled"]} · 内部移転 {len(latest["transfers"])} · 起動 {latest["runs"]} 回</span></h3>
  {today}
</div>"""


def overview(D: dict, mon: dict) -> str:
    bd = D["bd"]
    return f"""
<div class="sec"><h2>全体の概要</h2><span class="muted small">直近 {len(bd)} 営業日（{bd[0]}〜{bd[-1]}）</span></div>
{tiles(mon)}
<div class="ov-grid">
  {pnl_panel(D)}
  {diff_panel(D)}
</div>
{latest_panel(D)}
"""


def daily_table(D: dict) -> str:
    rows = []
    for d in reversed(D["bd"]):
        dd = D["days"].get(d)
        if dd is None:
            rows.append(f'<tr class="row-missing"><td><b>{d}</b></td><td colspan="7">⚠ 起動なし — 営業日なのに執行器の記録が無い（timer ／ cron が動かなかった？）</td></tr>')
            continue
        m = dd["diff1_median_bp"]
        rows.append(f'<tr><td><b>{d}</b></td><td class="num">{dd["runs"]}</td><td class="num">{len(dd["signals"])}</td><td class="num">{dd["n_orders"]}</td>'
                    f'<td class="num cell-{"ok" if dd["n_orders"] and dd["n_filled"] == dd["n_orders"] else ("na" if not dd["n_orders"] else "warn")}">{dd["n_filled"]}</td>'
                    f'<td class="num">{len(dd["transfers"])}</td><td class="num cell-{"ng" if dd["n_bad"] else "na"}">{dd["n_bad"]}</td>'
                    f'<td class="num cell-{status_of(m)}">{m if m is not None else "—"}</td></tr>')
    return ('<div class="sec"><h2>日次</h2><span class="muted small">全トレーダーまとめて 1 日 1 行（新しい順）· 起動しなかった日も行を出す</span></div>'
            '<div class="panel"><table class="small orders"><tr><th>日付</th><th class="num">起動</th><th class="num">合図</th><th class="num">注文</th>'
            '<th class="num">約定</th><th class="num">内部移転</th><th class="num">問題</th><th class="num">差 1 中央値 bp</th></tr>' + "".join(rows) + "</table></div>")


def today_text(t: dict) -> str:
    acts = [f"{s} {ACT_LABEL[a]}" for s, a in t["today"] if a != "none"]
    return " · ".join(acts) if acts else "動きなし"


def holdings_text(t: dict) -> str:
    hs = (t["last"] or {}).get("holdings") or {}
    return " · ".join(f'{s} {v["shares"]:g} @ {v["avg_price"]:.2f}' for s, v in hs.items()) or "なし"


def name_cell(t: dict) -> str:
    badge = ' <span class="badge badge-mock">TEST</span>' if t["test"] else ""
    return f'<span class="tr-name"><i class="sw dot {t["cls"]}"></i><a href="trader-{h(t["name"])}.html">{h(t["name"])}</a>{badge}</span>'


def pnl_now(t: dict) -> tuple[float, float]:
    u = next((v for v in reversed(t["pnl_usd"]) if v is not None), 0.0)
    return u, (u / t["budget_usd"] * 100 if t["budget_usd"] else 0.0)


def traders_table(D: dict) -> str:
    rows = []
    for t in D["traders"]:
        u, p = pnl_now(t)
        m = t["diff1_median"]
        rows.append(f'<tr><td>{name_cell(t)}</td><td class="num nw">${t["budget_usd"]:,.0f}</td>'
                    f'<td class="num nw pnl">{fmt_pct(p)}<span class="usd">{fmt_usd(u)}</span></td>'
                    f'<td class="spark">{sparkline(t, D["bd"], D["missing"], pnl_domain(D))}</td>'
                    f'<td class="num nw"><span class="chip {status_of(m)}">{MARK[status_of(m)]} {m if m is not None else "—"} bp</span></td>'
                    f'<td class="num nw">{t["n_filled"]} ／ {t["n_orders"]}<span class="muted"> ＋ 移転 {t["n_transfers"]}</span></td>'
                    f'<td class="small">{h(today_text(t))}</td><td class="small nw">{h(holdings_text(t))}</td>'
                    f'<td class="go"><a href="trader-{h(t["name"])}.html">詳細 →</a></td></tr>')
    return ('<div class="panel"><table class="exp"><tr><th>トレーダー</th><th class="num">予算</th><th class="num">損益</th><th>推移（20 日）</th>'
            '<th class="num">差 1 中央値</th><th class="num">約定 ／ 注文</th><th>今日</th><th>建玉</th><th></th></tr>' + "".join(rows) + "</table></div>")


def traders_cards(D: dict) -> str:
    cards = []
    for t in D["traders"]:
        u, p = pnl_now(t)
        m = t["diff1_median"]
        chart = line_chart([{"cls": t["cls"], "name": t["name"], "pts": t["pnl_pct"], "tips": pnl_tips(t)}], D["bd"], D["missing"],
                           w=380, hgt=110, fmt=lambda v: f"{v:+.1f}%" if v else "0%", pad_l=40, pad_r=12, end_labels=False,
                           aria=f'{t["name"]} の損益の推移', domain=pnl_domain(D), n_ticks=3, miss_text=False)
        cards.append(f"""<div class="tcard bar-{t["cls"]}">
  <div class="thead">{name_cell(t)}<span class="muted small">予算 ${t["budget_usd"]:,.0f}</span></div>
  <div class="big">{fmt_pct(p)}<span class="usd">{fmt_usd(u)}</span></div>
  {chart}
  <div class="kvs">
    <span class="k">差 1 中央値</span><span><span class="chip {status_of(m)}">{MARK[status_of(m)]} {m if m is not None else "—"} bp</span> <span class="muted">n={len(t["diff1"])}</span></span>
    <span class="k">約定 ／ 注文</span><span>{t["n_filled"]} ／ {t["n_orders"]}（＋ 内部移転 {t["n_transfers"]}）</span>
    <span class="k">今日</span><span>{h(today_text(t))}</span>
    <span class="k">建玉</span><span>{h(holdings_text(t))}</span>
    <span class="k">モデル</span><span>{h(" ／ ".join(mm["name"] for mm in t["models"]))} · {h(t["combine_label"])} · θ {t["threshold"]:g}</span>
  </div>
  <div class="tfoot"><a href="trader-{h(t["name"])}.html">詳細 →</a></div>
</div>""")
    return '<div class="cards">' + "".join(cards) + "</div>"


def traders_lanes(D: dict, lane_h: int = 70) -> str:
    lanes = []
    w = 1010
    for k, t in enumerate(D["traders"]):
        u, p = pnl_now(t)
        m = t["diff1_median"]
        chart = line_chart([{"cls": t["cls"], "name": t["name"], "pts": t["pnl_pct"], "tips": pnl_tips(t)}], D["bd"], D["missing"],
                           w=w, hgt=lane_h, fmt=lambda v: f"{v:+.1f}%" if v else "0%", pad_l=52, pad_r=40, pad_b=4,
                           show_x=False, end_labels=False, aria=f'{t["name"]} の損益の推移', domain=pnl_domain(D), n_ticks=2,
                           miss_text=(k == 0))
        grid = action_grid(t, D["bd"], w=w, pad_l=52, pad_r=40, show_x=(k == len(D["traders"]) - 1))
        lanes.append(f"""<div class="lane bl-{t["cls"]}">
  <div class="info">{name_cell(t)}
    <span class="big">{fmt_pct(p)}</span><span class="muted">{fmt_usd(u)} · 予算 ${t["budget_usd"]:,.0f}</span>
    <span><span class="k">差 1</span> <span class="chip {status_of(m)}">{MARK[status_of(m)]} {m if m is not None else "—"} bp</span></span>
    <span><span class="k">約定</span> {t["n_filled"]} ／ {t["n_orders"]}（＋ 移転 {t["n_transfers"]}）</span>
    <span><span class="k">今日</span> {h(today_text(t))}</span>
    <a href="trader-{h(t["name"])}.html">詳細 →</a>
  </div>
  <div>{chart}{grid}</div>
</div>""")
    key = ('<div class="keyrow"><span class="key"><i class="k-hold"></i>保有</span><span class="key"><i class="k-buy"></i>買 ／ 売（約定・内部移転）</span>'
           '<span class="key"><i class="k-skip"></i>見送り</span><span class="key"><i class="k-none"></i>動きなし</span><span class="key"><i class="k-nostart"></i>起動なし</span>'
           '<span>· 色の濃淡は各トレーダーの色。⚠ 状態の色（緑・赤）は使わない</span></div>')
    return '<div class="lanes">' + "".join(lanes) + "</div>" + key


def pattern_page(D: dict, mon: dict, fname: str, lab: str, body_traders: str, note: str) -> str:
    body = (overview(D, mon)
            + f'<div class="sec"><h2>トレーダー</h2><span class="muted small">パターン {h(lab)} — {h(note)} · 並びは設定の順で固定（損益の順にしない）· 名前を押すと詳細</span></div>'
            + body_traders + daily_table(D))
    return page(f"概要（パターン {lab}）· 管理画面モック", body, fname)


def trader_page(D: dict, t: dict, *, backs: str | None = None, **page_kw) -> str:
    bd, miss = D["bd"], D["missing"]
    u, p = pnl_now(t)
    pnl = line_chart([{"cls": t["cls"], "name": t["name"], "pts": t["pnl_pct"], "tips": pnl_tips(t)}], bd, miss, w=620, hgt=200,
                     fmt=lambda v: f"{v:+.1f}%" if v else "0%", pad_r=70, end_labels=True,
                     label_fmt=lambda s, i: fmt_pct(s["pts"][i]), aria=f'{t["name"]} の損益の推移')
    # 紙上の損益の枠（N3。材料が無いので作り物の数字は入れない）
    ph = ('<svg class="chart" viewBox="0 0 620 60" role="img" aria-label="紙上の損益（Phase 3 の後）"><rect class="ph" x="52" y="6" width="488" height="46" rx="4"/>'
          '<text class="ph-t" x="296" y="34" text-anchor="middle">紙上の損益（同じ合図を公式終値で回した値）は Phase 3（紙上の対照）の後でここに重ねる</text></svg>')
    grid = action_grid(t, bd, w=1240, pad_l=52, pad_r=40, cell_h=24)
    # 差 1 の推移（注文ごとの点。背景は閾値の帯）
    vals = [v for _, v in t["diff1"]]
    lo, hi = min([0.0] + vals) - 2, max([15.0] + vals) + 3
    w2, h2, pl, pr, pt, pb = 620, 200, 52, 20, 12, 22
    n = len(bd)
    X = lambda i: pl + i * (w2 - pl - pr) / (n - 1)  # noqa: E731
    Y = lambda v: pt + (hi - v) / (hi - lo) * (h2 - pt - pb)  # noqa: E731
    d1 = [f'<svg class="chart" viewBox="0 0 {w2} {h2}" role="img" aria-label="{h(t["name"])} の差 1（注文ごと）">']
    for cls, a, b in (("band-ok", max(lo, 0), 5), ("band-warn", 5, 10), ("band-ng", 10, hi)):
        d1.append(f'<rect class="{cls}" x="{pl}" y="{Y(b):.1f}" width="{w2 - pl - pr}" height="{Y(a) - Y(b):.1f}"/>')
    for tv in nice_ticks(lo, hi, 4):
        if lo <= tv <= hi:
            d1.append(f'<text class="tick" x="{pl - 6}" y="{Y(tv) + 3:.1f}" text-anchor="end">{tv:g}</text>')
    d1.append(f'<line class="zero" x1="{pl}" x2="{w2 - pr}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    for i, d in enumerate(bd):
        if i % 5 == 0 or i == n - 1:
            d1.append(f'<text class="tick" x="{X(i):.1f}" y="{h2 - 6}" text-anchor="middle">{d[5:]}</text>')
    for d in miss:
        i = bd.index(d)
        cw = (w2 - pl - pr) / (n - 1)
        d1.append(f'<rect class="miss" x="{X(i) - cw / 2:.1f}" y="{pt}" width="{cw:.1f}" height="{h2 - pt - pb}"><title>{d}: 起動なし</title></rect>')
    for d, v in t["diff1"]:
        d1.append(f'<circle class="dt {t["cls"]}" cx="{X(bd.index(d)):.1f}" cy="{Y(v):.1f}" r="4"><title>{d}: 差 1 {v} bp</title></circle>')
    d1.append("</svg>")
    orders = orders_table(D, t["name"])
    m = t["diff1_median"]
    return _trader_page_rest(D, t, pnl, ph, grid, d1, orders, m, u, p, miss, backs, page_kw)


def orders_table(D: dict, name: str | None = None) -> str:
    """注文の履歴（新しい順・内部移転を含む）。name を渡すとそのトレーダーの分だけ。"""
    rows = []
    for d in reversed(D["dates"]):
        dd = D["days"][d]
        for o in dd["orders"]:
            parts = o.get("parts") or []
            if name is not None:
                part = next((q for q in parts if q.get("trader") == name), None)
                if not part:
                    continue
                qty = f'{part["shares"]:g} 株'
                note = ("同じ注文: " + ", ".join(q["trader"] for q in parts if q["trader"] != name)) if len(parts) > 1 else ""
            else:
                qty = f'{o["fills_qty"]:g} 株'
                note = " · ".join(f'{q["trader"]} {q["shares"]:g}' for q in parts)
            qm = (o.get("quote_at_signal") or {}).get("mid")
            rows.append(f'<tr><td>{d}</td><td>{h(o["symbol"])}</td><td>{"買い" if o["side"] == "buy" else "売り"}</td><td class="num">{qty}</td>'
                        f'<td class="num muted">{qm:.2f}</td><td class="num">{o["fill_price"]:.2f}</td><td class="num">{" ".join(str(v) for v in o["diff1_bp"]) or "—"}</td>'
                        f'<td class="cell-{o["status_class"]}">{h(o["final_status"])}</td><td class="small muted">{h(note)}</td></tr>')
        for x in dd["transfers"]:
            if name is None or name in (x["buyer"], x["seller"]):
                if name is None:
                    side, note = "内部移転", f'{x["seller"]} → {x["buyer"]}（口座に出ない）'
                else:
                    side = "買い" if x["buyer"] == name else "売り"
                    note = f'相手 {x["seller"] if side == "買い" else x["buyer"]}（口座に出ない）'
                rows.append(f'<tr><td>{d}</td><td>{h(x["symbol"])}</td><td>{side}</td><td class="num">{x["shares"]:g} 株</td><td class="num muted">—</td>'
                            f'<td class="num">{x["price"]:.2f}</td><td class="num">—</td><td class="cell-na">内部移転</td><td class="small muted">{h(note)}</td></tr>')
    who = "" if name is not None else "<th>誰の分</th>"
    return ('<table class="small orders"><tr><th>日付</th><th>銘柄</th><th>売買</th><th class="num">数量</th><th class="num">合図時の mid</th>'
            f'<th class="num">約定</th><th class="num">差 1 bp</th><th>状態</th>{who or "<th></th>"}</tr>' + "".join(rows) + "</table>")


def _trader_page_rest(D, t, pnl, ph, grid, d1, orders, m, u, p, miss, backs, page_kw) -> str:
    if backs is None:
        backs = " ".join(f'<a href="{f}">← 概要（パターン {lab}）</a>' for f, lab in PATTERNS)
    body = f"""
<div class="backs">{backs}</div>
<div class="sec"><h2>{name_cell(t)}</h2><span class="muted small">予算 ${t["budget_usd"]:,.0f} · 銘柄 {h(" ".join(t["symbols"]))} · モデル {h(" ／ ".join(mm["kind_label"] + " " + mm["name"] for mm in t["models"]))} · 合成 {h(t["combine_label"])} · θ {t["threshold"]:g} · 株数 {h(t["sizing_label"])}</span></div>
<div class="tiles">
  <div class="tile"><div class="k">損益（実現 ＋ 含み）</div><div class="v">{fmt_pct(p)}</div><div class="s">{fmt_usd(u)} · 実現 {fmt_usd(t["last"].get("realized_usd", 0))} ／ 含み {fmt_usd(t["last"].get("unrealized_usd", 0))}</div></div>
  <div class="tile"><div class="k">差 1 中央値</div><div class="v"><span class="chip {status_of(m)}">{MARK[status_of(m)]} {m if m is not None else "—"} bp</span></div><div class="s">n={len(t["diff1"])} · 正 ＝ 不利</div></div>
  <div class="tile"><div class="k">約定 ／ 注文</div><div class="v num">{t["n_filled"]} ／ {t["n_orders"]}</div><div class="s">＋ 内部移転 {t["n_transfers"]}</div></div>
  <div class="tile"><div class="k">建玉</div><div class="v small">{h(holdings_text(t))}</div><div class="s">原価 {fmt_usd(t["last"].get("cost_in_use_usd", 0))}</div></div>
  <div class="tile"><div class="k">手数料（差 2）</div><div class="v num">{fmt_usd(t["last"].get("fees_usd", 0)) or "$0.00"}</div><div class="s">dry-run の写し</div></div>
  <div class="tile"><div class="k">最終日</div><div class="v small">{h(t["last"].get("date", "—"))}</div><div class="s">起動なし {len(miss)} 日</div></div>
</div>
<div class="dgrid">
  <div class="panel"><h3>損益の推移 <span class="small">予算に対する %</span></h3>{pnl}{ph}</div>
  <div class="panel"><h3>差 1 の推移 <span class="small">注文ごと · 背景は閾値の帯（✅ ≤ 5 ／ ⚠ 5〜10 ／ ❌ ＞ 10）</span></h3>{"".join(d1)}</div>
  <div class="panel wide"><h3>行動の推移 <span class="small">銘柄 × 営業日 · マスに触れると合図と約定</span></h3>{grid}
    <div class="keyrow"><span class="key"><i class="k-hold"></i>保有</span><span class="key"><i class="k-buy"></i>買 ／ 売</span><span class="key"><i class="k-skip"></i>見送り</span><span class="key"><i class="k-none"></i>動きなし</span><span class="key"><i class="k-nostart"></i>起動なし</span></div></div>
  <div class="panel wide"><h3>注文の履歴 <span class="small">新しい順 · 内部移転を含む</span></h3><div class="scroll-x">{orders}</div></div>
</div>
"""
    title = page_kw.pop("title", f"{t['name']} · 管理画面モック")
    return page(title, body, "", **page_kw)


# ================================================================ 2 回目: 並べ方は「3 段」に決めて、見た目のデザインを 3 通り

DESIGNS = [
    {"dir": "design-1-dark", "label": "デザイン 1 いまの延長", "note": "黒ベース・情報を詰める（§15 のまま）",
     "css": (), "cls": "d1", "lane_h": 70, "ov_h": 230, "hero": False},
    {"dir": "design-2-light", "label": "デザイン 2 明るい地", "note": "白い地・余白を多めに・グラフを大きく",
     "css": ("theme-light.css",), "cls": "d2", "lane_h": 110, "ov_h": 290, "hero": False},
    {"dir": "design-3-numbers", "label": "デザイン 3 数字とグラフが主役", "note": "大きな数字 1 つとグラフが主役。監視は細い帯に畳み、表は全体の詳細へ",
     "css": ("design-numbers.css",), "cls": "d3", "lane_h": 100, "ov_h": 300, "hero": True},
]
MOCKBAR_2 = ("🧪 <b>モック</b> — 見た目のデザインを 3 通り比べるためのもの（Phase 5 の 2 回目。並べ方は「3 段」に決定）。"
             "⚠ <b>数字は執行器のモック 20 営業日の値で【実測】ではない</b>。10-19 はわざと起動していない")


def design_side(dz: dict, active: str, D: dict) -> str:
    def link(href: str, label: str, extra: str = "") -> str:
        return f'<a href="{href}" class="{"on" if active == href else ""}">{extra}{label}</a>'
    traders = "".join(link(f'trader-{t["name"]}.html', h(t["name"]), f'<i class="sw dot {t["cls"]}"></i>') for t in D["traders"])
    designs = "".join(f'<a href="../{d["dir"]}/{active}" class="{"on" if d is dz else ""}">{h(d["label"].replace("デザイン ", ""))}</a>' for d in DESIGNS)
    return f"""<div class="brand">ai-income-lab<br><span class="muted">管理画面</span> <span class="ver">モック</span></div>
<nav class="side-nav">
  <div class="grp">見る</div>
  {link("overview.html", "概要")}
  {link("overall.html", "全体の詳細")}
  <div class="grp">トレーダー</div>
  {traders}
  <div class="grp">ローカル面だけ</div>
  <a href="#">操作 <span class="muted small">（解除・履歴）</span></a>
  <a href="#">記録と判定 <span class="muted small">（観点 A まで）</span></a>
  <div class="grp">デザイン（比べる用）</div>
  {designs}
</nav>
<div class="side-foot"><span class="pill pill-local">ローカル面 · ループバックのみ</span></div>
"""


def status_strip(mon: dict) -> str:
    ms = mon["monitors"]
    parts = []
    for env in ("cert", "prod"):
        a = (ms.get(env) or {}).get("auth") or {}
        parts.append(f'<span class="badge badge-{env}">{env.upper()}</span> <b>{"✅ 認証中" if a.get("ok") else "❌ 未認証"}</b> 残り {a.get("remaining_s", 0):.0f} 秒')
    p = ms.get("prod") or {}
    s, dx = p.get("account_stream") or {}, p.get("dxlink") or {}
    parts.append(f'接続 <b>{"✅ 2 本" if s.get("state") == "connected" and dx.get("state") == "connected" else "⚠ 切断あり"}</b>')
    parts.append(f'口座 <b>${float((p.get("balances") or {}).get("cash-balance") or 0):,.0f}</b>')
    wo = sum(len((ms.get(e) or {}).get("live_orders") or []) for e in ("cert", "prod"))
    parts.append(f'働いている注文 <b>{wo}</b>')
    parts.append(f'事象 <b>{len(mon.get("events") or [])}</b>')
    return '<div class="strip">' + '<span class="sep"></span>'.join(f"<span>{x}</span>" for x in parts) + "</div>"


def hero_row(D: dict) -> str:
    tr = D["traders"]
    u = sum(pnl_now(t)[0] for t in tr)
    budget = sum(t["budget_usd"] for t in tr)
    pct = u / budget * 100 if budget else 0.0
    m = D["diff1_median"]
    st = status_of(m)
    miss = len(D["missing"])
    return f"""<div class="hero-row">
  <div class="hero"><div class="k">全トレーダーの損益（予算 ${budget:,.0f} に対する %）</div><div class="v">{fmt_pct(pct)}</div>
    <div class="s">{fmt_usd(u)} · {len(tr)} 人 · ⚠ 損益で手法を採らない（色も付けない）</div></div>
  <div class="stat"><div class="k">差 1 中央値（判定の対象）</div><div class="v"><span class="chip {st}">{MARK[st]} {m if m is not None else "—"} bp</span></div><div class="s">n={D["diff1_n"]} · 正 ＝ 不利</div></div>
  <div class="stat"><div class="k">約定 ／ 注文</div><div class="v num">{D["filled"]} ／ {D["orders"]}</div><div class="s">内部移転 {D["transfers"]} · 問題 {D["bad"]}</div></div>
  <div class="stat"><div class="k">起動しなかった日</div><div class="v {"ng" if miss else ""}">{"❌ " if miss else "✅ "}{miss} 日</div><div class="s">{h(" ".join(D["missing"])) or "なし"}</div></div>
</div>"""


def events_table(mon: dict) -> str:
    rows = "".join(f'<tr><td class="nw muted">{h(e.get("at", "")[:19].replace("T", " "))}</td><td>{h((e.get("env") or "").upper())}</td>'
                   f'<td><span class="ev ev-{h(e.get("kind", ""))}">{h(e.get("kind", ""))}</span></td>'
                   f'<td class="small muted">{h(json.dumps(e.get("detail") or {}, ensure_ascii=False)[:80])}</td></tr>' for e in (mon.get("events") or [])[:30])
    return f'<table class="small"><tr><th>時刻（UTC）</th><th>環境</th><th>事象</th><th>中身</th></tr>{rows}</table>'


def d_overview(D: dict, mon: dict, dz: dict) -> str:
    bd = D["bd"]
    head = (f'<div class="sec"><h2>全体の概要</h2><span class="muted small">直近 {len(bd)} 営業日（{bd[0]}〜{bd[-1]}）</span>'
            '<span class="grow"></span><a href="overall.html">全体の詳細 →</a></div>')
    if dz["hero"]:
        top = (status_strip(mon) + hero_row(D)
               + f'<div class="charts-2">{pnl_panel(D, w=760, hgt=dz["ov_h"])}{diff_panel(D)}</div>')
    else:
        top = (tiles(mon) + f'<div class="ov-grid">{pnl_panel(D, w=760, hgt=dz["ov_h"])}{diff_panel(D)}</div>' + latest_panel(D))
    traders = ('<div class="sec"><h2>トレーダー</h2><span class="muted small">1 人 1 段。損益と行動を同じ時間の軸で揃える · 並びは設定の順で固定（損益の順にしない）· 名前を押すと詳細</span></div>'
               + traders_lanes(D, lane_h=dz["lane_h"]))
    return head + top + traders


def d_overall(D: dict, mon: dict, dz: dict) -> str:
    bd = D["bd"]
    return (f'<div class="backs"><a href="overview.html">← 概要</a></div>'
            f'<div class="sec"><h2>全体の詳細</h2><span class="muted small">全トレーダーまとめて · 直近 {len(bd)} 営業日（{bd[0]}〜{bd[-1]}）· 概要から移した日次と、注文・監視の事象の履歴</span></div>'
            + daily_table(D)
            + '<div class="sec"><h2>注文の履歴</h2><span class="muted small">全トレーダー · 新しい順 · 内部移転を含む</span></div>'
            + f'<div class="panel"><div class="scroll-x">{orders_table(D)}</div></div>'
            + '<div class="sec"><h2>監視の事象</h2><span class="muted small">直近 30 件（モックのデモの値）</span></div>'
            + f'<div class="panel"><div class="scroll-x">{events_table(mon)}</div></div>')


def design_readme(dz: dict, D: dict) -> str:
    names = " ・ ".join(f"[{t['name']}](trader-{t['name']}.html)" for t in D["traders"])
    return f"""# {dz["label"]}（モック）

[3 通りのデザイン](../README.md) の 1 つ。{dz["note"]}。
⚠ **数字は執行器のモック 20 営業日の値で【実測】ではない**。

| 開く | 中身 |
| --- | --- |
| [概要](overview.html) | 上に全体の概要、その下にトレーダー（1 人 1 段）。名前を押すと詳細 |
| [全体の詳細](overall.html) | 日次（概要から移した）・注文の履歴・監視の事象 |
| {names} | トレーダーの詳細 |

![概要](overview.png)
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live-dir", required=True, type=Path)
    ap.add_argument("--monitor", default=HERE / "fixtures" / "monitor-demo.json", type=Path)
    args = ap.parse_args()
    D = load(args.live_dir)
    mon = json.loads(args.monitor.read_text(encoding="utf-8"))
    # 1 回目: トレーダーの一覧の並べ方 3 パターン（layouts/。「3 段」に決まった）
    out1 = HERE / "layouts"
    out1.mkdir(exist_ok=True)
    views = {"pattern-1-table.html": (traders_table(D), "1 人 1 行。数字の列に小さな折れ線"),
             "pattern-2-cards.html": (traders_cards(D), "1 人 1 枚のカードを横に並べる"),
             "pattern-3-lanes.html": (traders_lanes(D), "1 人 1 段。損益と行動を同じ時間の軸で横に揃える")}
    for fname, lab in PATTERNS:
        body, note = views[fname]
        (out1 / fname).write_text(pattern_page(D, mon, fname, lab, body, note), encoding="utf-8")
    for t in D["traders"]:
        (out1 / f"trader-{t['name']}.html").write_text(trader_page(D, t), encoding="utf-8")
    # 2 回目: 見た目のデザイン 3 通り（design-*/。日次は概要から「全体の詳細」へ移した）
    for dz in DESIGNS:
        out = HERE / dz["dir"]
        out.mkdir(exist_ok=True)
        kw = {"css_extra": dz["css"], "body_class": dz["cls"], "mockbar": MOCKBAR_2}
        (out / "overview.html").write_text(page(f'{dz["label"]} · 概要', d_overview(D, mon, dz), "", side=design_side(dz, "overview.html", D), **kw), encoding="utf-8")
        (out / "overall.html").write_text(page(f'{dz["label"]} · 全体の詳細', d_overall(D, mon, dz), "", side=design_side(dz, "overall.html", D), **kw), encoding="utf-8")
        for t in D["traders"]:
            f = f"trader-{t['name']}.html"
            (out / f).write_text(trader_page(D, t, backs='<a href="overview.html">← 概要</a> <a href="overall.html">全体の詳細</a>',
                                             title=f'{dz["label"]} · {t["name"]}', side=design_side(dz, f, D), **kw), encoding="utf-8")
        (out / "README.md").write_text(design_readme(dz, D), encoding="utf-8")
    print(f"トレーダー {len(D['traders'])} 人 · 営業日 {len(D['bd'])} · 起動なし {D['missing']} · 注文 {D['orders']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
