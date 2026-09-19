"""概要・トレーダーの詳細のグラフ（サーバで組む SVG。dashboard.md §15-5）。

⚠ **スクリプトもインラインの style も使わない**（CSP: `script-src 'self'; style-src 'self'`）。幾何は SVG の属性、色はクラス（app.css）。
hover は SVG の `<title>` だけ。テンプレートからは Jinja の関数として呼び、⚠ **`Redactor` を通した後のデータから描く**。
見本は docs/plans/assets/dashboard-patterns/（build.py）。損益には勝ち負けの色を付けない（CLAUDE.md の例外・§15-2）。
"""

from __future__ import annotations

import math
from html import escape

from markupsafe import Markup


def h(x) -> str:
    return escape(str(x), quote=True)


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return ("−" if v < 0 else "＋" if v > 0 else "") + f"{abs(v):.2f}%"


def fmt_usd(v: float | None) -> str:
    if v is None:
        return "—"
    return ("−" if v < 0 else "＋" if v > 0 else "") + f"${abs(v):,.2f}"


def axis_pct(v: float) -> str:
    return f"{v:+.1f}%" if v else "0%"


def nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    span = hi - lo or 1.0
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    out, v = [], math.floor(lo / step) * step
    while v <= hi + step * 0.001:
        out.append(round(v, 10))
        v += step
    return out


def pnl_domain(traders: list[dict]) -> tuple[float, float]:
    """トレーダーを並べる小さな図は縦軸を揃える（ばらばらだと見比べを誤る）。"""
    vals = [v for t in traders for v in t.get("pnl_pct") or [] if v is not None] + [0.0]
    return min(vals), max(vals)


def _series_cls(s: dict) -> str:
    return s["cls"] + (" dash" if s.get("dash") else "")


def line_chart(series: list[dict], bd: list[str], missing: list[str], *, w: int, hgt: int, label_fmt=None,
               pad_l: int = 52, pad_r: int = 120, pad_t: int = 14, pad_b: int = 22, show_x: bool = True,
               end_labels: bool = True, aria: str = "", domain: tuple[float, float] | None = None, n_ticks: int = 4,
               miss_text: bool = True, fmt=axis_pct) -> Markup:
    """折れ線（1 軸だけ）。欠けた営業日は線を切り、帯で示す。日ごとの透明な帯に全系列の値を `<title>` で持たせる。"""
    n = len(bd)
    if n == 0:
        return Markup("")
    vals = [v for s in series for v in s["pts"] if v is not None] + [0.0]
    lo, hi = domain or (min(vals), max(vals))
    ticks = nice_ticks(lo, hi, n_ticks)
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1])
    pw, ph = w - pad_l - pad_r, hgt - pad_t - pad_b
    step = pw / (n - 1) if n > 1 else pw

    def x(i: int) -> float:
        return pad_l + (i * step if n > 1 else pw / 2)

    def y(v: float) -> float:
        return pad_t + (hi - v) / ((hi - lo) or 1) * ph

    o = [f'<svg class="chart" viewBox="0 0 {w} {hgt}" role="img" aria-label="{h(aria)}">']
    for tv in ticks:
        o.append(f'<line class="{"zero" if abs(tv) < 1e-12 else "grid"}" x1="{pad_l}" x2="{pad_l + pw}" y1="{y(tv):.1f}" y2="{y(tv):.1f}"/>')
        o.append(f'<text class="tick" x="{pad_l - 6}" y="{y(tv) + 3:.1f}" text-anchor="end">{h(fmt(tv))}</text>')
    for d in missing:
        if d not in bd:
            continue
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
        if d_attr:
            o.append(f'<g class="{_series_cls(s)}"><path class="ln" d="{d_attr}"/></g>')
    ends = []
    for s in series:
        idx = max((i for i, v in enumerate(s["pts"]) if v is not None), default=None)
        if idx is None or s.get("no_end"):
            continue
        ends.append({"s": s, "i": idx, "px": x(idx), "py": y(s["pts"][idx]), "ly": y(s["pts"][idx])})
        o.append(f'<circle class="dt {s["cls"]}" cx="{x(idx):.1f}" cy="{y(s["pts"][idx]):.1f}" r="4"/>')
    if end_labels and ends:
        # 端のラベルは重ならないように押し分け、押し分けたら引き出し線でつなぐ
        ends.sort(key=lambda e: e["py"])
        for k in range(1, len(ends)):
            if ends[k]["ly"] - ends[k - 1]["ly"] < 13:
                ends[k]["ly"] = ends[k - 1]["ly"] + 13
        for e in ends:
            lx = e["px"] + 10
            if abs(e["ly"] - e["py"]) > 0.5:
                o.append(f'<line class="axis" x1="{e["px"] + 4:.1f}" y1="{e["py"]:.1f}" x2="{lx - 2:.1f}" y2="{e["ly"]:.1f}"/>')
            lab = label_fmt(e["s"], e["i"]) if label_fmt else fmt(e["s"]["pts"][e["i"]])
            name = f'{h(e["s"]["name"])} ' if len([s for s in series if not s.get("no_end")]) > 1 else ""
            o.append(f'<text class="lab" x="{lx:.1f}" y="{e["ly"] + 4:.1f}">{name}<tspan class="lab-m">{h(lab)}</tspan></text>')
    for i, d in enumerate(bd):
        parts = [d] + [f'{s["name"]}: {s["tips"][i]}' for s in series if s.get("tips") and s["tips"][i]]
        o.append(f'<rect class="hit" x="{x(i) - step / 2:.1f}" y="{pad_t}" width="{step:.1f}" height="{ph}"><title>{h(chr(10).join(parts))}</title></rect>')
    o.append("</svg>")
    return Markup("".join(o))


def pnl_tips(t: dict) -> list[str]:
    return [f"{fmt_pct(p)}（{fmt_usd(u)}）" if p is not None else "" for p, u in zip(t["pnl_pct"], t["pnl_usd"])]


def pnl_overview(b: dict, *, hgt: int = 300) -> Markup:
    """概要: 全トレーダーの損益の推移（予算に対する %）。並びは設定の順。"""
    series = [{"cls": t["cls"], "dash": t.get("dash"), "name": t["name"], "pts": t["pnl_pct"], "tips": pnl_tips(t)} for t in b["traders"]]
    return line_chart(series, b["bd"], b["missing"], w=760, hgt=hgt, label_fmt=lambda s, i: fmt_pct(s["pts"][i]),
                      aria="トレーダー別の損益の推移（予算に対する %）")


def pnl_lane(b: dict, t: dict, *, k: int, hgt: int = 100) -> Markup:
    """トレーダーの段: 損益の推移（縦軸は全員で揃える）。"""
    return line_chart([{"cls": t["cls"], "dash": t.get("dash"), "name": t["name"], "pts": t["pnl_pct"], "tips": pnl_tips(t)}],
                      b["bd"], b["missing"], w=1010, hgt=hgt, pad_l=52, pad_r=40, pad_b=4, show_x=False, end_labels=False,
                      aria=f'{t["name"]} の損益の推移', domain=pnl_domain(b["traders"]), n_ticks=2, miss_text=(k == 0))


def pnl_trader(b: dict, t: dict) -> Markup:
    """トレーダーの詳細: 実物の損益 ＋ ⚠ 紙上の損益（仮データ。点線）。"""
    series = [{"cls": t["cls"], "name": "実物", "pts": t["pnl_pct"], "tips": pnl_tips(t)}]
    if t.get("paper_pct"):
        series.append({"cls": t["cls"], "dash": True, "name": "紙上・仮", "pts": t["paper_pct"],
                       "tips": [f"{fmt_pct(v)}（仮データ）" if v is not None else "" for v in t["paper_pct"]]})
    return line_chart(series, b["bd"], b["missing"], w=620, hgt=220, pad_r=150, label_fmt=lambda s, i: fmt_pct(s["pts"][i]),
                      aria=f'{t["name"]} の損益の推移')


def diff1_dots(traders: list[dict], *, w: int = 520) -> Markup:
    """差 1 の中央値（トレーダー別）。背景は閾値の帯（判定の対象は執行の差なので状態の色を付けてよい。§15-4）。"""
    vals = [v for t in traders for _, v in t["diff1"]]
    if not traders:
        return Markup("")
    lo, hi = min([0.0] + vals) - 2, max([15.0] + vals) + 3
    pad_l, pad_r, row = 70, 70, 26
    hgt = 22 + row * len(traders) + 18
    pw = w - pad_l - pad_r

    def x(v: float) -> float:
        return pad_l + (v - lo) / (hi - lo) * pw

    top, bot = 16, hgt - 18
    o = [f'<svg class="chart" viewBox="0 0 {w} {hgt}" role="img" aria-label="トレーダー別の差 1 の中央値">']
    for cls, a, b_, t in (("band-ok", max(lo, 0), 5, "✅ ≤ 5"), ("band-warn", 5, 10, "⚠ 5〜10"), ("band-ng", 10, hi, "❌ ＞ 10")):
        o.append(f'<rect class="{cls}" x="{x(a):.1f}" y="{top}" width="{x(b_) - x(a):.1f}" height="{bot - top}"/>')
        o.append(f'<text class="band-t" x="{(x(a) + x(b_)) / 2:.1f}" y="{top - 4}" text-anchor="middle">{t}</text>')
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
    return Markup("".join(o))


def diff1_timeline(b: dict, t: dict) -> Markup:
    """トレーダーの詳細: 差 1 の推移（注文ごとの点。背景は閾値の帯）。"""
    bd, n = b["bd"], len(b["bd"])
    if n == 0:
        return Markup("")
    vals = [v for _, v in t["diff1"]]
    lo, hi = min([0.0] + vals) - 2, max([15.0] + vals) + 3
    w2, h2, pl, pr, pt, pb = 620, 220, 52, 20, 12, 22
    cw = (w2 - pl - pr) / (n - 1) if n > 1 else (w2 - pl - pr)

    def X(i: int) -> float:
        return pl + (i * cw if n > 1 else (w2 - pl - pr) / 2)

    def Y(v: float) -> float:
        return pt + (hi - v) / (hi - lo) * (h2 - pt - pb)

    o = [f'<svg class="chart" viewBox="0 0 {w2} {h2}" role="img" aria-label="{h(t["name"])} の差 1（注文ごと）">']
    for cls, a, b_ in (("band-ok", max(lo, 0), 5), ("band-warn", 5, 10), ("band-ng", 10, hi)):
        o.append(f'<rect class="{cls}" x="{pl}" y="{Y(b_):.1f}" width="{w2 - pl - pr}" height="{Y(a) - Y(b_):.1f}"/>')
    for tv in nice_ticks(lo, hi, 4):
        if lo <= tv <= hi:
            o.append(f'<text class="tick" x="{pl - 6}" y="{Y(tv) + 3:.1f}" text-anchor="end">{tv:g}</text>')
    o.append(f'<line class="zero" x1="{pl}" x2="{w2 - pr}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    for i, d in enumerate(bd):
        if i % 5 == 0 or i == n - 1:
            o.append(f'<text class="tick" x="{X(i):.1f}" y="{h2 - 6}" text-anchor="middle">{d[5:]}</text>')
    for d in b["missing"]:
        if d in bd:
            o.append(f'<rect class="miss" x="{X(bd.index(d)) - cw / 2:.1f}" y="{pt}" width="{cw:.1f}" height="{h2 - pt - pb}"><title>{d}: 起動なし</title></rect>')
    for d, v in t["diff1"]:
        if d in bd:
            o.append(f'<circle class="dt {t["cls"]}" cx="{X(bd.index(d)):.1f}" cy="{Y(v):.1f}" r="4"><title>{d}: 差 1 {v} bp</title></circle>')
    o.append("</svg>")
    return Markup("".join(o))


def action_grid(b: dict, t: dict, *, w: int = 1010, pad_l: int = 52, pad_r: int = 40, cell_h: int = 20, show_x: bool = True) -> Markup:
    """行動のマス目（銘柄 × 営業日）。⚠ 状態の色は使わない（買い・売りは良い悪いではない）。系列の色の濃淡と文字で分ける。"""
    bd, n = b["bd"], len(b["bd"])
    rows = t["symbols"]
    if n == 0 or not rows:
        return Markup("")
    cw = (w - pad_l - pad_r) / n
    hgt = cell_h * len(rows) + (16 if show_x else 2)
    o = [f'<svg class="chart" viewBox="0 0 {w} {hgt}" role="img" aria-label="{h(t["name"])} の行動（銘柄 × 日）">']
    cls_of = {"none": "c-none", "hold": f'c-hold {t["cls"]}', "buy": f'c-buy {t["cls"]}', "sell": f'c-sell {t["cls"]}',
              "skip": "c-skip", "nostart": "c-nostart"}
    text_of = {"buy": ("c-t", "買"), "sell": ("c-t", "売"), "skip": ("c-t-m", "見"), "nostart": ("c-t-ng", "✕")}
    for r, s in enumerate(rows):
        yy = r * cell_h
        o.append(f'<text class="tick" x="{pad_l - 6}" y="{yy + cell_h / 2 + 3:.1f}" text-anchor="end">{h(s)}</text>')
        for i, c in enumerate(t["grid"].get(s) or []):
            xx = pad_l + i * cw
            o.append(f'<rect class="{cls_of[c["a"]]}" x="{xx + 1:.1f}" y="{yy + 1}" width="{cw - 2:.1f}" height="{cell_h - 2}" rx="2"><title>{h(c["tip"])}</title></rect>')
            if c["a"] in text_of:
                tc, tx = text_of[c["a"]]
                o.append(f'<text class="{tc}" x="{xx + cw / 2:.1f}" y="{yy + cell_h / 2 + 3.5:.1f}" text-anchor="middle">{tx}</text>')
    if show_x:
        for i, d in enumerate(bd):
            if i % 5 == 0 or i == n - 1:
                o.append(f'<text class="tick" x="{pad_l + i * cw + cw / 2:.1f}" y="{hgt - 3}" text-anchor="middle">{d[5:]}</text>')
    o.append("</svg>")
    return Markup("".join(o))
