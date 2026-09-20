"""実売買の画面（`/live`）。トレーダー別の予算・モデル・建玉・損益・今日の合図と注文・差 1〜4 の直近 20 日。

⚠ **読むだけ。発注は画面から出さない**（公開面でも見える）。停止は既存の停止ボタン（`HALT` を執行器も見る）。
正本は執行器（`experiments/live-trading/`）が書いたもので、この画面は数え直さない・書かない
（[dashboard.md §13](../../docs/specs/dashboard.md)）:

  - トレーダーの定義        → `config/traders/*.toml`
  - 台帳（持ち分・実現損益） → `state/<env>/<名前>.json`
  - 日次の記録              → `out/<日付>/{signals,quotes,orders,transfers,ledger,events,positions,balances}.jsonl`

⚠ **標準ライブラリだけ**。⚠ **どのファイルが無くても落とさない**（g3plus には執行器の記録を置かないので空でも 200）。
⚠ 記録は執行器が `Masker` を通して書いているが、画面の応答はさらに `Redactor` を通す。
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

DAYS = 20
COMBINE_LABEL = {"asis": "そのまま", "mean": "平均", "majority": "多数決", "unanimous": "全員一致"}
SIZING_LABEL = {"shares": "整数株", "notional": "金額指定"}
KIND_LABEL = {"fixed": "固定", "file": "CSV", "experiment": "実験"}
STATUS_CLASS = {"Filled": "ok", "dry-run": "na", "planned": "na", "Cancelled": "warn", "Rejected": "ng", "error": "ng",
                "guarded": "warn", "halted": "warn", "not_submitted": "ng", "Expired": "warn"}


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    # ⚠ 偶数個のときの平均は二進の端数が出る（17.615000000000002）。差 1 は 0.01bp まで（`_diff1_bp` と同じ桁）
    return round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2, 2)


# ---------------------------------------------------------------- トレーダー

def traders(live_dir: Path) -> list[dict]:
    out = []
    for path in sorted((live_dir / "config" / "traders").glob("*.toml")):
        try:
            with path.open("rb") as f:
                doc = tomllib.load(f)
        except (OSError, ValueError):
            continue
        models = []
        for m in doc.get("models") or []:
            kind = str(m.get("kind", ""))
            models.append({"kind": kind, "kind_label": KIND_LABEL.get(kind, kind), "name": str(m.get("name") or kind),
                           "buy": m.get("buy"), "exit": m.get("exit"), "path": m.get("path")})
        symbols = list(doc.get("symbols") or [])
        out.append({
            "name": str(doc.get("name") or path.stem),
            "file": path.name,
            "test": bool(doc.get("test", False)),
            "budget_usd": float(doc.get("budget_usd", 0) or 0),
            "symbols": symbols,
            "universe": doc.get("universe"),
            "n_symbols": len(symbols) if symbols else None,
            "models": models,
            "combine": doc.get("combine", "asis"),
            "combine_label": COMBINE_LABEL.get(str(doc.get("combine", "asis")), str(doc.get("combine", "asis"))),
            "threshold": doc.get("threshold", 50.0),
            "sizing": doc.get("sizing", "shares"),
            "sizing_label": SIZING_LABEL.get(str(doc.get("sizing", "shares")), str(doc.get("sizing", "shares"))),
            "note": doc.get("note", ""),
        })
    return out


def states(live_dir: Path) -> dict[str, dict[str, dict]]:
    """env → トレーダー名 → 台帳。"""
    out: dict[str, dict[str, dict]] = {}
    root = live_dir / "state"
    if not root.is_dir():
        return out
    for env_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(env_dir.glob("*.json")):
            st = _read_json(path)
            if not st:
                continue
            holdings = st.get("holdings") or {}
            out.setdefault(env_dir.name, {})[path.stem] = {
                "holdings": holdings,
                "cost_in_use_usd": round(sum(float(h.get("shares", 0)) * float(h.get("avg_price", 0)) for h in holdings.values()), 2),
                "realized_usd": round(float(st.get("realized_usd", 0) or 0), 2),
                "fees_usd": round(float(st.get("fees_usd", 0) or 0), 4),
                "last_date": st.get("last_date"),
                "n_trades": len(st.get("history") or []),
                "pending_settlement": st.get("pending_settlement") or [],
            }
    return out


# ---------------------------------------------------------------- 日次

def _diff1_bp(order: dict) -> list[float]:
    """差 1: 合図時の気配（mid）→ 約定。⚠ **正 ＝ 不利**（買いは高く・売りは安く約定）。"""
    q = order.get("quote_at_signal") or {}
    mid = q.get("mid")
    if not mid:
        return []
    out = []
    for f in order.get("fills") or []:
        px = f.get("price")
        if not px:
            continue
        signed = (px - mid) / mid * 1e4
        out.append(round(signed if order.get("side") == "buy" else -signed, 2))
    return out


def day(live_dir: Path, date: str) -> dict:
    d = live_dir / "out" / date
    orders = _read_jsonl(d / "orders.jsonl")
    events = _read_jsonl(d / "events.jsonl")
    signals = _read_jsonl(d / "signals.jsonl")
    transfers = _read_jsonl(d / "transfers.jsonl")
    ledger = _read_jsonl(d / "ledger.jsonl")
    positions = _read_jsonl(d / "positions.jsonl")
    balances = _read_jsonl(d / "balances.jsonl")
    for o in orders:
        o["diff1_bp"] = _diff1_bp(o)
        o["status_class"] = STATUS_CLASS.get(str(o.get("final_status")), "na")
        o["fills_qty"] = round(sum(float(f.get("shares", 0)) for f in o.get("fills") or []), 6)
        o["fill_price"] = (sum(float(f.get("shares", 0)) * float(f.get("price", 0)) for f in o.get("fills") or []) / o["fills_qty"]) if o["fills_qty"] else None
        o["traders"] = [p.get("trader") for p in o.get("parts") or []]
    envs = sorted({r.get("env") for r in orders + events + signals if r.get("env")})
    modes = sorted({r.get("mode") for r in orders if r.get("mode")})
    all_diff1 = [x for o in orders for x in o["diff1_bp"]]
    starts = [e for e in events if e.get("kind") == "start"]
    problems = [e for e in events if e.get("kind") in ("halted", "out_of_window", "auth_failed", "signal_error", "quote_failed", "over_budget", "over_day_cap", "auth_5xx_retry",
                                                        # 2026-09-19: 起動を拒否した日（機械がシミュレーションモード ／ 二重起動）と、台帳に入れられなかった約定
                                                        "refused_mode_sim", "refused_lock_busy", "ledger_error",
                                                        # 含み損が予算の 20% 以上（⚠ 執行器は警告だけ。止めるのは人 ＝ 停止ボタン）
                                                        "drawdown_warning",
                                                        # 口座の建玉と台帳の帳尻（live-trading.md §0-8）: 前の実行の約定を控えから戻した ／ 照会できない ／ 口座が台帳より少ない
                                                        "journal_recovered", "journal_unresolved", "position_short")]
    retries = sum(1 for o in orders for t in o.get("transitions") or [] if "retry" in str(t.get("status", "")))
    # 差 4（無人運転）: 拒否・再送・HALT・窓の外。差 3 は Phase 3（紙上の対照）の後で埋まる
    return {
        "date": date,
        "envs": envs,
        "modes": modes,
        "mock": any(r.get("mock") for r in orders + events),
        "test": any(r.get("test") for r in orders + signals),
        "runs": len(starts),
        "signals": signals,
        "orders": orders,
        "transfers": transfers,
        "ledger": ledger,
        "positions": positions,
        "balances": [{"when": b.get("when"), "env": b.get("env"), **{k: (b.get("balances") or {}).get(k) for k in ("cash-balance", "equity-buying-power", "net-liquidating-value")}} for b in balances],
        "events": events,
        "problems": problems,
        "n_orders": len(orders),
        "n_filled": sum(1 for o in orders if o.get("final_status") == "Filled"),
        "n_bad": sum(1 for o in orders if o["status_class"] == "ng"),
        "retries": retries,
        "diff1_median_bp": _median(all_diff1),
        "diff1_max_bp": max(all_diff1) if all_diff1 else None,
        "no_signal": sum(1 for e in events if e.get("kind") == "no_signal"),
        "skipped": sum(1 for e in events if e.get("kind") in ("too_small", "over_budget", "over_day_cap", "no_quote")),
    }


def dates(live_dir: Path) -> list[str]:
    root = live_dir / "out"
    if not root.is_dir():
        return []
    return sorted((p.name for p in root.iterdir() if p.is_dir() and len(p.name) == 10 and p.name[4] == "-"), reverse=True)


DAILY_NUM = ("budget_usd", "paper_bp", "paper_cum_bp", "real_usd", "real_bp", "real_cum_bp", "diff3_bp", "diff3_cum_bp", "bh_bp", "bh_cum_bp",
             "diff1_quote_to_fill_bp", "diff1_fill_to_close_bp", "diff2_half_spread_bp", "diff2_fees_usd")
DAILY_INT = ("n_symbols", "n_signals", "paper_held", "paper_trades", "orders", "filled", "not_filled", "unexecuted", "diff4_events", "close_missing")


def daily_rows(live_dir: Path) -> list[dict]:
    """紙上の対照（実売買の Phase 3。執行器の `paper.py` が書く `out/daily.csv`）。⚠ **読むだけ**（ここで計算しない）。無ければ空。"""
    import csv
    path = live_dir / "out" / "daily.csv"
    if not path.exists():
        return []
    rows = []
    try:
        with path.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                row: dict = {"date": r.get("date"), "trader": r.get("trader"), "test": r.get("test") == "True",
                             "close_source": r.get("close_source") or ""}
                for k in DAILY_NUM:
                    row[k] = float(r[k]) if r.get(k) not in (None, "") else None
                for k in DAILY_INT:
                    row[k] = int(float(r[k])) if r.get(k) not in (None, "") else None
                rows.append(row)
    except (OSError, ValueError, KeyError):
        return []
    return rows


def index(live_dir: Path, days: int = DAYS) -> dict:
    tr = traders(live_dir)
    st = states(live_dir)
    ds = dates(live_dir)
    recent = [day(live_dir, d) for d in ds[:days]]
    today = recent[0] if recent else None
    # トレーダー別の直近の台帳（ledger.jsonl の最新行）
    latest_ledger: dict[str, dict] = {}
    for dd in recent:
        for row in dd["ledger"]:
            key = f"{row.get('env')}/{row.get('trader')}"
            if key not in latest_ledger:
                latest_ledger[key] = row
    for t in tr:
        t["states"] = {env: s.get(t["name"]) for env, s in st.items() if s.get(t["name"])}
        t["ledger"] = {env: latest_ledger.get(f"{env}/{t['name']}") for env in {k.split("/")[0] for k in latest_ledger} if latest_ledger.get(f"{env}/{t['name']}")}
    all_diff1 = [x for dd in recent for o in dd["orders"] for x in o["diff1_bp"]]
    summary = {
        "days": len(recent),
        "orders": sum(dd["n_orders"] for dd in recent),
        "filled": sum(dd["n_filled"] for dd in recent),
        "bad": sum(dd["n_bad"] for dd in recent),
        "retries": sum(dd["retries"] for dd in recent),
        "problem_days": sum(1 for dd in recent if dd["problems"] or dd["n_bad"]),
        "diff1_median_bp": _median(all_diff1),
        "diff1_n": len(all_diff1),
        "real_days": sum(1 for dd in recent if not dd["mock"] and not dd["test"] and "submit" in dd["modes"]),
    }
    daily = daily_rows(live_dir)
    return {
        # ⚠ 紙上の対照は本物（daily.csv）があるときだけ出す。仮データは出さない
        **({"daily": daily[-days * max(1, len(tr)):]} if daily else {}),
        "live_dir": str(live_dir),
        "empty": not tr and not recent,
        "traders": tr,
        "configured": [t for t in tr if not t["test"]],
        "test_traders": [t for t in tr if t["test"]],
        "envs": sorted(st.keys()),
        "today": today,
        "recent": recent,
        "summary": summary,
        "days": days,
    }


# ---------------------------------------------------------------- 概要とトレーダーの詳細（2026-09-18。dashboard.md §13・§15）

ACT_LABEL = {"buy": "買い", "sell": "売り", "hold": "保有", "skip": "見送り", "none": "動きなし", "nostart": "起動なし"}
SKIP_KINDS = ("too_small", "over_budget", "over_day_cap", "no_quote")
N_SERIES = 3          # 系列の色の数（§15-2。状態・環境・アクセントと取り違えない条件では 3 色まで）
PAPER_PLACEHOLDER_BP_PER_DAY = 2.0   # ⚠ 仮データ（紙上の損益）の傾き。本物は実売買の Phase 3（紙上の対照）の後


CALENDAR_WARN_DAYS = 90   # 暦の終わりまでこの日数を切ったら、全体の詳細に「次の年を足す」注意を出す


def _calendar():
    """NYSE の暦（サンプルの `market_calendar`。執行器と同じもの）。⚠ `import_sample` の後に呼ぶ。"""
    import market_calendar

    return market_calendar.nyse()


def business_days(first: str, last: str) -> list[str]:
    """NYSE の営業日（休場日を除く平日）。⚠ 暦に載っていない年は平日をすべて営業日とみなす（`calendar_info` の `covered` が False）。"""
    from datetime import date as _date
    return [d.isoformat() for d in _calendar().trading_days(_date.fromisoformat(first), _date.fromisoformat(last))]


def calendar_info(first: str | None, last: str | None, today=None) -> dict:
    """暦の出どころと、いま見ている範囲を暦が答えられるか。画面は `covered` が False のときだけ「仮」の印を出す。"""
    from datetime import date as _date, timedelta
    cal = _calendar()
    today = today or _date.today()
    covered, holidays = True, []
    if first and last:
        d, end = _date.fromisoformat(first), _date.fromisoformat(last)
        covered = cal.covered(d) and cal.covered(end)
        while d <= end:
            if cal.is_holiday(d):
                holidays.append(d.isoformat())
            d += timedelta(days=1)
    left = cal.days_left(today)
    return {"covered": covered, "source": cal.source, "fetched": cal.fetched, "last_covered": cal.last_covered.isoformat(),
            "days_left": left, "expiring": left < CALENDAR_WARN_DAYS, "holidays": holidays}


def board(live_dir: Path, days: int = DAYS, today=None) -> dict:
    """概要・全体の詳細・トレーダーの詳細が使う形。トレーダー別の推移（損益・行動のマス目・差 1）と起動しなかった日。

    ⚠ 仮データ（紙上の損益・差 3）は `placeholder` の印を付けて返す。`/api/live` には出さない。
    """
    tr = traders(live_dir)
    ds = dates(live_dir)[:days][::-1]          # 古い順の直近 days 日
    dd = {d: day(live_dir, d) for d in ds}
    bd = business_days(ds[0], ds[-1]) if ds else []
    # today: シミュレーションでは仮の今日（暦の残り日数を仮の時計で数える）。None なら本物の今日
    cal_info = calendar_info(ds[0], ds[-1], today=today) if ds else calendar_info(None, None, today=today)
    missing = [d for d in bd if d not in dd]
    daily = daily_rows(live_dir)
    for i, t in enumerate(tr):
        name = t["name"]
        t["cls"] = f"s{i % N_SERIES + 1}"
        t["dash"] = i >= N_SERIES             # 4 人目からは線の形で分ける（§15-2）
        ledger = {d: r for d in ds for r in dd[d]["ledger"] if r.get("trader") == name}
        t["pnl_usd"] = [round(ledger[d].get("realized_usd", 0) + ledger[d].get("unrealized_usd", 0), 2) if d in ledger else None for d in bd]
        t["pnl_pct"] = [round(v / t["budget_usd"] * 100, 4) if v is not None and t["budget_usd"] else None for v in t["pnl_usd"]]
        t["last"] = next((ledger[d] for d in reversed(ds) if d in ledger), {})
        mine = {r["date"]: r for r in daily if r["trader"] == name}
        t["paper_real"] = bool(mine)
        if mine:
            # ✅ 本物: 執行器の paper.py が書いた daily.csv（同じ合図を公式終値・片道 2.5bp で回した累計。予算に対する %）
            t["paper_pct"] = [round(mine[d]["paper_cum_bp"] / 100, 4) if d in mine and mine[d]["paper_cum_bp"] is not None else None for d in bd]
            t["bh_pct"] = [round(mine[d]["bh_cum_bp"] / 100, 4) if d in mine and mine[d]["bh_cum_bp"] is not None else None for d in bd]
            t["daily"] = [mine[d] for d in sorted(mine, reverse=True)][:days]
            last_row = next((mine[d] for d in sorted(mine, reverse=True) if mine[d]["diff3_cum_bp"] is not None), None)
            t["diff3_cum_bp"] = last_row["diff3_cum_bp"] if last_row else None
            t["diff3_median_bp"] = _median([r["diff3_bp"] for r in mine.values() if r["diff3_bp"] is not None])
            t["close_source"] = next(iter(mine.values()))["close_source"]
        else:
            # ⚠ 仮データ: 紙上の損益 ＝ 実物の損益に 1 営業日あたり 2bp（予算に対して）を足した線。本物ではない（daily.csv がまだ無いとき）
            k = 0
            paper = []
            for v in t["pnl_pct"]:
                if v is None:
                    paper.append(None)
                    continue
                k += 1
                paper.append(round(v + PAPER_PLACEHOLDER_BP_PER_DAY / 100 * k, 4))
            t["paper_pct"] = paper
        grid: dict[str, list[dict]] = {s: [] for s in t["symbols"]}
        diff1: list[list] = []
        n_orders = n_filled = n_transfers = 0
        for d in bd:
            day_ = dd.get(d)
            for s in t["symbols"]:
                if day_ is None:
                    grid[s].append({"a": "nostart", "tip": f"{d} {s}: 起動なし（営業日なのに執行器の記録が無い）"})
                    continue
                sig = next((x for x in day_["signals"] if x.get("trader") == name and x.get("symbol") == s), None)
                sig_t = f"（合図 買い {sig.get('buy', 0):.0f} ／ 出口 {sig.get('exit', 0):.0f}）" if sig else ""
                act, detail = "none", ""
                for o in day_["orders"]:
                    part = next((p for p in o.get("parts") or [] if p.get("trader") == name), None)
                    if part and o.get("symbol") == s and o.get("fills_qty"):
                        act = "buy" if o.get("side") == "buy" else "sell"
                        detail = f"{part.get('shares')} 株 @ {o['fill_price']:.2f}"
                for x in day_["transfers"]:
                    if x.get("symbol") == s and name in (x.get("buyer"), x.get("seller")):
                        act = "buy" if x.get("buyer") == name else "sell"
                        other = x.get("seller") if act == "buy" else x.get("buyer")
                        detail = f"{x.get('shares')} 株 @ {x.get('price')}（内部移転。相手 {other}）"
                skips = [e.get("kind") for e in day_["events"] if e.get("kind") in SKIP_KINDS and e.get("trader") == name and e.get("symbol") == s]
                if act == "none" and skips:
                    act, detail = "skip", skips[0]
                if act == "none" and s in ((ledger.get(d) or {}).get("holdings") or {}):
                    act = "hold"
                grid[s].append({"a": act, "tip": f"{d} {s}: {ACT_LABEL[act]} {detail}{sig_t}".strip()})
            if day_ is None:
                continue
            for o in day_["orders"]:
                if name in o["traders"]:
                    n_orders += 1
                    n_filled += 1 if o.get("final_status") == "Filled" else 0
                    diff1 += [[d, v] for v in o["diff1_bp"]]
            n_transfers += sum(1 for x in day_["transfers"] if name in (x.get("buyer"), x.get("seller")))
        t["grid"] = grid
        t["diff1"] = diff1
        t["diff1_median"] = _median([v for _, v in diff1])
        t["n_orders"], t["n_filled"], t["n_transfers"] = n_orders, n_filled, n_transfers
        t["today"] = [[s, grid[s][-1]["a"]] for s in t["symbols"] if grid[s]]
        t["today_text"] = " · ".join(f"{s} {ACT_LABEL[a]}" for s, a in t["today"] if a != "none") or "動きなし"
        t["holdings_text"] = " · ".join(f'{s} {v.get("shares", 0):g} @ {float(v.get("avg_price", 0)):.2f}'
                                        for s, v in ((t["last"] or {}).get("holdings") or {}).items()) or "なし"
        t["pnl_now_usd"] = next((v for v in reversed(t["pnl_usd"]) if v is not None), None)
        t["pnl_now_pct"] = next((v for v in reversed(t["pnl_pct"]) if v is not None), None)
    active = [t for t in tr if t["pnl_now_usd"] is not None]
    budget = sum(t["budget_usd"] for t in active)
    total = sum(t["pnl_now_usd"] for t in active) if active else None
    all_diff1 = [v for d in ds for o in dd[d]["orders"] for v in o["diff1_bp"]]
    return {
        "live_dir": str(live_dir),
        "empty": not tr and not ds,
        "traders": tr,
        "configured": [t for t in tr if not t["test"]],
        "test_traders": [t for t in tr if t["test"]],
        "bd": bd,
        "missing": missing,
        "dates": ds,
        "days": [dd[d] for d in ds],
        "latest": dd[ds[-1]] if ds else None,
        "total": {"pnl_usd": total, "budget_usd": budget, "pnl_pct": (total / budget * 100) if total is not None and budget else None,
                  "n_traders": len(active)},
        "summary": {
            "orders": sum(dd[d]["n_orders"] for d in ds), "filled": sum(dd[d]["n_filled"] for d in ds),
            "bad": sum(dd[d]["n_bad"] for d in ds), "retries": sum(dd[d]["retries"] for d in ds),
            "problem_days": sum(1 for d in ds if dd[d]["problems"] or dd[d]["n_bad"]),
            "transfers": sum(len(dd[d]["transfers"]) for d in ds),
            "diff1_median_bp": _median(all_diff1), "diff1_n": len(all_diff1),
            "fees_usd": round(sum(float(t["last"].get("fees_usd", 0) or 0) for t in tr), 4),
        },
        "calendar": cal_info,
        # ⚠ 仮データの印（画面はこれを見てバッジを出す）。暦は、見ている範囲が NYSE の暦の外に出たときだけ仮（平日＝営業日）
        # ⚠ 紙上の損益・差 3 は、daily.csv（実売買の Phase 3）がある人は本物・無い人は仮データ（`t.paper_real` で人ごとに分かれる）
        "placeholder": {"paper": not any(t.get("paper_real") for t in tr), "diff3_bp_per_day": PAPER_PLACEHOLDER_BP_PER_DAY, "calendar": not cal_info["covered"]},
        "diff3_median_bp": _median([r["diff3_bp"] for r in daily if r["diff3_bp"] is not None and not r["test"]]),
        "close_source": daily[0]["close_source"] if daily else None,
    }
