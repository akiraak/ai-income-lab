"""紙上の対照（プラン Phase 3）: 同じ合図を「公式終値・片道 2.5bp」で回し、差 1〜4 を `daily.csv` に 1 日 1 行 × トレーダーで書く。

    python3 paper.py                                   # out/ の全日付 → out/daily.csv（毎回ぜんぶ作り直す ＝ 何度流しても同じ）
    python3 paper.py --out-dir sim/sim1/out --close-from quotes    # シミュレーション ／ モック（公式終値が無い）: 合図時の気配の中値を終値の代役に

⚠ **読むだけ**（ネットワークを使わない・発注しない・台帳を書かない）。入力は執行器の記録（`signals` ／ `orders` ／ `ledger` ／ `events`）と
   公式終値（既定は `../feature-discovery/data-live/adjusted/d/` ＝ `live_update.sh` の置き場）。
⚠ **紙上の状態機械はバックテストと同じ**（`ail/validation/simulate.py`・rules.md 13-4）: 未保有で 買い% > θ なら建てる ／ 保有中で 出口% > θ なら
   手仕舞う ／ 足 t の終値で執行 ／ 建てた日と手仕舞った日にだけ片道 `cost_bp / 2` ／ 銘柄は等加重（予算 ÷ 銘柄数。13-7）。
   ⚠ fold 末尾の強制清算だけは無い（実売買は終わらない）。
⚠ **紙上は合図どおりに必ず執行できる**（予算・整数株・受渡し待ち・拒否を知らない）。実物が見送ったぶんは差 3 に出るので、`unexecuted` の列で数える。
⚠ **損益で手法を採らない**（CLAUDE.md の 2 つ目の例外）。この表は執行の差を読むためのもの。

日付 d の行の意味（紙上も実物も同じ数え方にする）:
  その日の損益 ＝ 前の営業日の終値から d の終値までに、d の売買の**前**から持っていた分が稼いだもの − d の売買のコスト。
  実物 ＝ 台帳（`ledger.jsonl` の d の最後の行）の 実現損益 ＋ Σ 株数 ×（d の終値 − 取得単価）− 手数料、の前日差。
  bp はどれも**その人の予算に対して**。
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _livefs import livefs  # noqa: E402

DEFAULT_BARS = os.path.normpath(os.path.join(HERE, "..", "feature-discovery", "data-live", "adjusted", "d"))
COST_BP = 5.0          # 往復。片道 2.5bp（rules.md 13-4。バックテストと同じ）
UNATTENDED = ("retry", "auth_5xx_retry", "auth_failed", "halted", "out_of_window", "journal_unresolved", "position_short", "ledger_error")

COLUMNS = ["date", "trader", "test", "budget_usd", "n_symbols", "n_signals",
           "paper_held", "paper_trades", "paper_bp", "paper_cum_bp",
           "real_usd", "real_bp", "real_cum_bp", "diff3_bp", "diff3_cum_bp",
           "bh_bp", "bh_cum_bp",
           "orders", "filled", "not_filled", "unexecuted",
           "diff1_quote_to_fill_bp", "diff1_fill_to_close_bp", "diff2_half_spread_bp", "diff2_fees_usd",
           "diff4_events", "close_missing", "close_source"]


def _jsonl(path: str) -> list[dict]:
    """執行器の記録（⚠ 2026-09-21 から DB の lines ＝ `livefs`。道はそのまま）。"""
    return [json.loads(l) for l in livefs.read_lines(path) if l.strip()]


def record_dates(out_dir: str) -> list[str]:
    """記録のある日付（`out/<YYYY-MM-DD>/`）。"""
    return [d for d in livefs.listdir(out_dir) if len(d) == 10 and d[4] == "-" and d[7] == "-" and d[:4].isdigit()]


def load_closes_from_bars(bars_dir: str, symbols: set[str]) -> dict[str, dict[str, float]]:
    """銘柄 → {日付: 終値}。ファイル名は `BRK/B` → `BRK-B.csv`（feature-discovery の流儀）。"""
    from datetime import datetime, timezone
    out: dict[str, dict[str, float]] = {}
    for s in symbols:
        path = os.path.join(bars_dir, s.replace("/", "-") + ".csv")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", newline="") as f:
            out[s] = {datetime.fromtimestamp(int(r["time_ms"]) / 1000, tz=timezone.utc).date().isoformat(): float(r["close"])
                      for r in csv.DictReader(f)}
    return out


def load_closes_from_quotes(out_dir: str, dates: list[str]) -> dict[str, dict[str, float]]:
    """公式終値が無い記録（モック ／ シミュレーション）用: その日の最後の気配の中値を終値の代役にする。"""
    out: dict[str, dict[str, float]] = {}
    for d in dates:
        for row in _jsonl(os.path.join(out_dir, d, "quotes.jsonl")):
            for s, q in (row.get("quotes") or {}).items():
                mid = q.get("mid") or q.get("last")
                if mid:
                    out.setdefault(s, {})[d] = float(mid)
    return out


def _median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 2) if xs else None


def build_rows(out_dir: str, closes: dict[str, dict[str, float]], close_source: str, cost_bp: float = COST_BP,
               last_date: str | None = None) -> list[dict]:
    """全日付 × トレーダーの行。`last_date` より後の日は書かない（公式終値がまだ無い今日を外すため）。"""
    dates = record_dates(out_dir)
    if last_date:
        dates = [d for d in dates if d <= last_date]
    half = cost_bp / 2.0
    # トレーダーごとの紙上の状態（日をまたいで持つ）
    paper_pos: dict[str, dict[str, int]] = {}
    seen_symbols: dict[str, list[str]] = {}
    prev_date: dict[str, str] = {}
    cum: dict[str, dict[str, float]] = {}
    prev_real: dict[str, float] = {}
    bh_started: dict[str, bool] = {}
    rows: list[dict] = []

    for d in dates:
        day_dir = os.path.join(out_dir, d)
        signals = _jsonl(os.path.join(day_dir, "signals.jsonl"))
        orders = _jsonl(os.path.join(day_dir, "orders.jsonl"))
        ledger = _jsonl(os.path.join(day_dir, "ledger.jsonl"))
        events = _jsonl(os.path.join(day_dir, "events.jsonl"))
        # 同じ日に何度か起動したら、合図は（トレーダー, 銘柄）ごとに最後の行
        sig: dict[str, dict[str, dict]] = {}
        for r in signals:
            sig.setdefault(r["trader"], {})[r["symbol"]] = r
        led = {}
        for r in ledger:
            led[r["trader"]] = r                     # 最後の行が勝つ
        traders = sorted(set(sig) | set(led) | set(paper_pos))
        for t in traders:
            L = led.get(t) or {}
            budget = float(L.get("budget_usd") or 0) or None
            syms = seen_symbols.setdefault(t, [])
            for s in sig.get(t, {}):
                if s not in syms:
                    syms.append(s)
            pos = paper_pos.setdefault(t, {})
            c = cum.setdefault(t, {"paper": 0.0, "real": 0.0, "diff3": 0.0, "bh": 0.0})
            n = len(syms)
            pd_ = prev_date.get(t)
            missing = [s for s in syms if closes.get(s, {}).get(d) is None]

            # --- 紙上: 前の営業日から持っていた分の値動き（等加重）
            paper_bp = bh_bp = 0.0
            if pd_ and n:
                for s in syms:
                    c0, c1 = closes.get(s, {}).get(pd_), closes.get(s, {}).get(d)
                    if c0 and c1:
                        y = math.log(c1 / c0) * 1e4
                        paper_bp += pos.get(s, 0) * y / n
                        bh_bp += y / n
            # --- 紙上: d の終値での売買（状態機械はバックテストと同じ）
            theta = _threshold(sig.get(t, {}), out_dir, t)
            trades = 0
            for s, r in sig.get(t, {}).items():
                p = pos.get(s, 0)
                if p == 0 and float(r["buy"]) > theta:
                    pos[s], trades = 1, trades + 1
                elif p == 1 and float(r["exit"]) > theta:
                    pos[s], trades = 0, trades + 1
            if n:
                paper_bp -= half * trades / n
                if not bh_started.get(t) and sig.get(t):
                    bh_bp -= half                      # B&H は最初の日に全部買う（片道 1 回）
                    bh_started[t] = True

            # --- 実物: 台帳を d の終値で値洗い
            real_usd = None
            if L:
                real_usd = float(L.get("realized_usd") or 0.0) - float(L.get("fees_usd") or 0.0)
                for s, h in (L.get("holdings") or {}).items():
                    px = closes.get(s, {}).get(d)
                    if px is None:
                        px = float(h.get("avg_price") or 0.0)       # 終値が無ければ原価（＝ 含み損益 0）で置く。close_missing に出る
                        if s not in missing:
                            missing.append(s)
                    real_usd += float(h.get("shares") or 0.0) * (px - float(h.get("avg_price") or 0.0))
            real_bp = None
            if real_usd is not None and budget:
                real_bp = (real_usd - prev_real.get(t, 0.0)) / budget * 1e4
                prev_real[t] = real_usd

            # --- 差 1・差 2（注文ごと → その日の中央値）
            mine = [o for o in orders if any(p.get("trader") == t for p in (o.get("parts") or []))]
            q2f, f2c, spread, fees = [], [], [], 0.0
            filled = 0
            for o in mine:
                fills = o.get("fills") or []
                qty = sum(float(f.get("shares") or 0) for f in fills)
                q = o.get("quote_at_signal") or {}
                if q.get("bid") and q.get("ask") and q.get("mid"):
                    spread.append((float(q["ask"]) - float(q["bid"])) / 2.0 / float(q["mid"]) * 1e4)
                if qty <= 0:
                    continue
                filled += 1
                px = sum(float(f["shares"]) * float(f["price"]) for f in fills) / qty
                sign = 1.0 if o.get("side") == "buy" else -1.0       # 正 ＝ 不利（買いは高く・売りは安く約定）
                if q.get("mid"):
                    q2f.append(sign * (px - float(q["mid"])) / float(q["mid"]) * 1e4)
                cl = closes.get(o["symbol"], {}).get(d)
                if cl:
                    f2c.append(sign * (px - cl) / cl * 1e4)
                fees += float((o.get("amounts") or {}).get("fee_usd") or 0.0)
            # 紙上は売買したのに実物は見送った ／ 断られた合図
            unexecuted = sum(1 for e in events if e.get("trader") == t and e.get("kind") in ("over_budget", "too_small", "no_quote", "blocked_symbol"))
            unexecuted += sum(1 for o in mine if not (o.get("fills") or []) and o.get("mode") == "submit")
            ev4 = sum(1 for e in events if e.get("kind") in UNATTENDED and e.get("trader") in (None, t))

            c["paper"] += paper_bp
            c["bh"] += bh_bp
            diff3 = None
            if real_bp is not None:
                c["real"] += real_bp
                # ⚠ **差 3 の累計 ＝ 紙上の累計 − 実物の累計**（いつでも成り立つ形にする）。台帳の無い日（執行器が起動しなかった日）は
                #    実物の行が空で、その間の紙上の動きは次に台帳が出た日の差 3 にまとめて入る
                diff3 = (c["paper"] - c["real"]) - c["diff3"]
                c["diff3"] = c["paper"] - c["real"]
            rows.append({
                "date": d, "trader": t, "test": bool(L.get("test") or any(r.get("test") for r in sig.get(t, {}).values())),
                "budget_usd": budget, "n_symbols": n, "n_signals": len(sig.get(t, {})),
                "paper_held": sum(pos.values()), "paper_trades": trades,
                "paper_bp": round(paper_bp, 2), "paper_cum_bp": round(c["paper"], 2),
                "real_usd": None if real_usd is None else round(real_usd, 4),
                "real_bp": None if real_bp is None else round(real_bp, 2), "real_cum_bp": round(c["real"], 2) if real_bp is not None else None,
                "diff3_bp": None if diff3 is None else round(diff3, 2), "diff3_cum_bp": round(c["diff3"], 2) if diff3 is not None else None,
                "bh_bp": round(bh_bp, 2), "bh_cum_bp": round(c["bh"], 2),
                "orders": len(mine), "filled": filled, "not_filled": len(mine) - filled, "unexecuted": unexecuted,
                "diff1_quote_to_fill_bp": _median(q2f), "diff1_fill_to_close_bp": _median(f2c),
                "diff2_half_spread_bp": _median(spread), "diff2_fees_usd": round(fees, 4),
                "diff4_events": ev4, "close_missing": len(missing), "close_source": close_source,
            })
            prev_date[t] = d
    return rows


_THETA_CACHE: dict[tuple[str, str], float] = {}


def _threshold(sig_rows: dict[str, dict], out_dir: str, trader: str) -> float:
    """θ は合図の行に無いので、トレーダーの設定から読む（記録の隣の config → 既定の config → 50）。"""
    key = (out_dir, trader)
    if key not in _THETA_CACHE:
        theta = 50.0
        for base in (os.path.join(os.path.dirname(os.path.abspath(out_dir)), "config", "traders"), os.path.join(HERE, "config", "traders"),
                     os.environ.get("LT_TRADERS_DIR") or ""):
            path = os.path.join(base, f"{trader}.toml") if base else ""
            if path and os.path.exists(path):
                import tomllib
                with open(path, "rb") as f:
                    theta = float(tomllib.load(f).get("threshold", 50.0))
                break
        _THETA_CACHE[key] = theta
    return _THETA_CACHE[key]


def write_csv(path: str, rows: list[dict]) -> None:
    """`daily.csv` を丸ごと書く（⚠ 2026-09-21 から DB の docs。前の中身は docs_history に残る）。"""
    f = io.StringIO(newline="")
    w = csv.DictWriter(f, fieldnames=COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in COLUMNS})
    livefs.write_doc(path, f.getvalue())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.environ.get("LT_OUT_DIR") or os.path.join(HERE, "out"))
    ap.add_argument("--bars-dir", default=DEFAULT_BARS, help="公式終値（日足の CSV）の置き場")
    ap.add_argument("--close-from", choices=["bars", "quotes"], default="bars",
                    help="quotes ＝ 合図時の気配の中値を終値の代役にする（モック ／ シミュレーション。⚠ 差 1 の「約定 → 終値」は意味を持たない）")
    ap.add_argument("--last-date", default=None, help="この日まで（既定: 終値が 1 本でもある最後の日）")
    ap.add_argument("--csv", default=None, help="既定は <out-dir>/daily.csv")
    args = ap.parse_args()

    dates = record_dates(args.out_dir)
    if not dates:
        print(f"{args.out_dir} に日付の記録が無い", file=sys.stderr)
        return 1
    if args.close_from == "quotes":
        closes = load_closes_from_quotes(args.out_dir, dates)
    else:
        symbols = {r["symbol"] for d in dates for r in _jsonl(os.path.join(args.out_dir, d, "signals.jsonl"))}
        symbols |= {s for d in dates for r in _jsonl(os.path.join(args.out_dir, d, "ledger.jsonl")) for s in (r.get("holdings") or {})}
        closes = load_closes_from_bars(args.bars_dir, symbols)
    have = sorted({d for m in closes.values() for d in m})
    last = args.last_date or next((d for d in reversed(dates) if d in have), None)
    if last is None:
        print("終値のある日が 1 日も無い（先に live_update.sh を流すか --close-from quotes）", file=sys.stderr)
        return 1
    rows = build_rows(args.out_dir, closes, args.close_from, last_date=last)
    path = args.csv or os.path.join(args.out_dir, "daily.csv")
    write_csv(path, rows)
    print(f"{path}: {len(rows)} 行（{rows[0]['date']} 〜 {rows[-1]['date']}・終値は {args.close_from}）" if rows else f"{path}: 0 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
