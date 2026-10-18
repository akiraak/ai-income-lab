"""実売買の物差し A〜E（トレーダーのループ Phase 2 の定義）を、執行器の記録だけから 1 日 1 行 × トレーダーで `yardstick.csv` に書く。

    python3 yardstick.py                    # out/ → out/yardstick.csv（毎回ぜんぶ作り直す ＝ 何度流しても同じ）
    python3 yardstick.py --print            # 書かずに、人ごとのいまの期間のまとめを出す

プラン docs/plans/daily-record-auto.md（2026-10-13 利用者決定「全部おススメで進めて」＝ K1〜K4）。
⚠ **読むだけ**（ネットワークを使わない・発注しない・売買履歴を書かない）。`paper.py` の後に流す（E は `daily.csv` を写すだけ）。
⚠ **損益で人を採らない**（B は見るだけ。CLAUDE.md の 2 つ目の例外）。

物差し（定義の正本は docs/plans/trader-loop.md Phase 2。ここは計算の決まりだけ）:
  A 差 1 ＝ 注文ごとに 合図時の気配の中値 → 約定の平均（bp。正 ＝ 不利）。その日の値を `;` でつなぎ、中央値と最大を横に置く
  B 持ち続けるとの差 ＝ その人の損益 − 基準の損益（USD と bp of 予算。期間の初日からの累計）
      その人の損益 ＝（実現 − 手数料 ＋ 含み）の、期間の初日の前の最後の売買履歴の行からの増え（含みは売買履歴の 15:51 ET の中値）
      基準（K3）＝ 期間の初日の 15:51 ET の中値で、予算 ÷ 銘柄数 を整数株に丸めて買い（`sizing = "notional"` は金額）、
                   手数料なしで持ち続けた場合の 中値の値動き × 株数
  C 机上との一致 ＝ 合図（買い% ／ 出口%）・θ・合図の前の持ち高（`position_before`）から規則（rules.md 13-4 と同じ）で出る判断と、
      実物の判断（注文の向き ／ hold ／ skip）が違う銘柄の数。見送り（too_small 等）は不一致に数えず `held_back` に数える。手じまいの日は見ない
  D 無人運転 ＝ 起動したか ／ 問題なく終わったか ／ 人手（K4: 執行器の回に効いた操作だけ ＝ 同じ日の 2 回目以降の発注の回・
      一時停止 ／ 手じまいの指示が効いた最初の日・`HALT` が効いた最初の日・`liquidate.py`・`reconcile.py` の書き込み。取り消した指示・
      投入の手順〔名簿の開始〕は記録に効かないので数えない）／ 口座 − 売買履歴（全員ぶん。株数の差の絶対値の和）
  E 差 3（そろえた紙上）＝ `daily.csv` の `diff3a_bp` ／ `diff3a_cum_bp` を写す（計算しない）

期間: その人が発注の回（`start` の mode = submit・同じ回に拒否の事象なし）で、一時停止 ／ 手じまいでなく合図を読んだ最初の日に開き、
  手じまいが済んだ日（`liquidate_complete`）に閉じる（`paper.py` のそろえた紙上と同じ）。発注の回があるのに名簿に居ない日にも閉じる。
  記録の無い営業日（起動しなかった日）も、開いている人の行を「起動なし」で出す。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import statistics
import sys
from datetime import date as _date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _livefs import livefs  # noqa: E402

COLUMNS = ["date", "trader", "test", "since", "day_n", "status",
           "signals", "buys", "sells", "holds", "skips", "held_back",
           "orders", "filled", "a_diff1_bp", "a_median_bp", "a_max_bp",
           "b_real_usd", "b_hold_usd", "b_diff_usd", "b_diff_bp",
           "c_mismatch", "c_detail",
           "d_ran", "d_ok", "d_manual", "d_manual_detail", "d_gap_shares",
           "e_diff3a_bp", "e_diff3a_cum_bp",
           "problems"]

# 物差しの線（trader-loop.md Phase 2 ＝ 20 営業日の判定と同じ線）
A_LINE_BP = 5.0        # 差 1 の中央値
D_LINE_RATE = 0.95     # 執行できた日
E_LINE_BP = 5.0        # 差 3（そろえた）の |日次| 平均

REFUSED = ("out_of_window", "halted", "refused_mode_sim", "refused_lock_busy", "refused_not_production", "auth_failed", "signal_error")
HELD_BACK = ("too_small", "no_quote", "blocked_symbol", "over_budget")
# 異常として一覧に出す事象（人 ／ 全員）。⚠ 直った事象（journal_recovered・retry）は出さない
ANOMALY = ("auth_failed", "signal_error", "quote_failed", "quote_client_failed", "after_read_failed", "journal_unresolved",
           "positions_outside_ledger", "position_short", "order_unknown", "ledger_error", "liquidate_mark_failed",
           "drawdown_warning", "blocked_symbol", "out_of_window", "halted", "over_total_budget",
           "refused_mode_sim", "refused_lock_busy", "refused_not_production")
FLAG_KINDS = ("paused", "liquidate_flag")
# D の「問題なく終わった」を崩さないもの（執行の差・配線・含み損の警告は別の物差しで見る）
NOT_FAILURE = {"diff1_over", "c_mismatch", "drawdown_warning", "close_kept", "close_out_of_range"}
# 終値の控え（paper.py の out/closes.json）から出す問題。⚠ 日足の癖で、執行の失敗ではない（D を崩さない）
#   close_kept ＝ 終値だけの書き換え（権利落ち日の前日の配当の調整）を拒んで控えの値を使った ／ close_out_of_range ＝ 控えの終値が安値〜高値の外


def _jsonl(path: str) -> list[dict]:
    return [json.loads(l) for l in livefs.read_lines(path, missing_ok=True) if l.strip()]


def _f(x) -> float:
    try:
        return float(x or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _r(x: float | None, nd: int = 2):
    return None if x is None else round(x, nd)


def _calendar():
    sample = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
    if sample not in sys.path:
        sys.path.insert(0, sample)
    import market_calendar
    return market_calendar.nyse()


def business_days(first: str, last: str) -> list[str]:
    return [d.isoformat() for d in _calendar().trading_days(_date.fromisoformat(first), _date.fromisoformat(last))]


def expected_action(buy: float, exit_: float, held: bool, theta: float) -> str:
    """机上の規則（rules.md 13-4・`paper.py` と同じ）: 持っていて 出口% > θ なら売り ／ 持っていれば持つ ／
    持っていなくて 買い% > θ なら買い ／ それ以外は見送り。"""
    if held:
        return "sell" if exit_ > theta else "hold"
    return "buy" if buy > theta else "skip"


def hold_shares(budget: float, mids: dict[str, float], symbols: list[str], sizing: str) -> dict[str, float]:
    """基準（K3）の株数: 予算 ÷ 銘柄数 を初日の中値で整数株に丸める（`plan.size_intents` と同じ丸め）。金額指定はセント切り捨て。"""
    if not symbols or not budget:
        return {}
    per = budget / len(symbols)
    out = {}
    for s in symbols:
        px = mids.get(s)
        if not px:
            continue
        out[s] = float(math.floor(per / px)) if sizing == "shares" else (math.floor(per * 100) / 100) / px
    return out


def _mids(quotes: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in quotes:
        for s, q in (row.get("quotes") or {}).items():
            m = q.get("mid") or q.get("last")
            if m:
                out[s] = float(m)
    return out


def _pnl(ledger_row: dict | None) -> float:
    if not ledger_row:
        return 0.0
    return _f(ledger_row.get("realized_usd")) - _f(ledger_row.get("fees_usd")) + _f(ledger_row.get("unrealized_usd"))


def _positions_gap(positions: list[dict], held: dict[str, dict[str, float]]) -> float | None:
    """口座（その日の最後の `after`、無ければ `before`）− 売買履歴（全員の持ち株の和）の株数の差の絶対値の和。"""
    rows = [p for p in positions if p.get("when") == "after"] or [p for p in positions if p.get("when") == "before"]
    if not rows:
        return None
    acct: dict[str, float] = {}
    for p in rows[-1].get("positions") or []:
        q = _f(p.get("quantity"))
        acct[p["symbol"]] = acct.get(p["symbol"], 0.0) + (-q if p.get("quantity-direction") == "Short" else q)
    led: dict[str, float] = {}
    for h in held.values():
        for s, q in h.items():
            led[s] = led.get(s, 0.0) + q
    return round(sum(abs(acct.get(s, 0.0) - led.get(s, 0.0)) for s in set(acct) | set(led)), 6)


def _daily_map(daily_csv: str | None) -> dict[tuple[str, str], dict]:
    if not daily_csv:
        return {}
    text = livefs.read_doc(daily_csv, missing_ok=True)
    if not text:
        return {}
    return {(r["date"], r["trader"]): r for r in csv.DictReader(io.StringIO(text))}


def _num(s) -> float | None:
    if s in (None, ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def build_rows(out_dir: str, daily_csv: str | None = None, state_dir: str | None = None) -> list[dict]:
    import paper  # θ・銘柄・sizing の読み方は紙上の対照と同じ（記録の隣の config → 既定の config）

    dates = sorted(paper.record_dates(out_dir))
    if not dates:
        return []
    daily = _daily_map(daily_csv)
    recon = _reconcile_log(out_dir, state_dir)
    have = set(dates)
    days = sorted(set(business_days(dates[0], dates[-1])) | have)

    period: dict[str, dict] = {}            # 人 → {since, n, base_pnl, base_mids, shares, budget, last_mids}
    last_ledger: dict[str, dict] = {}       # 人 → 最後の売買履歴の行（期間の外も。B の基準と口座 − 売買履歴に使う）
    prev_flags: dict[str, set] = {}         # 人 → 前の発注の回で効いていた指示
    prev_halted = False
    rows: list[dict] = []

    for d in days:
        if d not in have:
            for t in sorted(period):
                rows.append(_blank_row(d, t, period[t], status="no_run", problems=["no_run"], recon=recon, halted_new=False))
            continue
        day_dir = os.path.join(out_dir, d)
        signals = _jsonl(os.path.join(day_dir, "signals.jsonl"))
        orders = _jsonl(os.path.join(day_dir, "orders.jsonl"))
        transfers = _jsonl(os.path.join(day_dir, "transfers.jsonl"))   # 内部移転（2026-09-19 にやめた形。古い記録だけ）
        events = _jsonl(os.path.join(day_dir, "events.jsonl"))
        ledger = _jsonl(os.path.join(day_dir, "ledger.jsonl"))
        quotes = _jsonl(os.path.join(day_dir, "quotes.jsonl"))
        positions = _jsonl(os.path.join(day_dir, "positions.jsonl"))
        mids = _mids(quotes)

        # 回（run_id）ごとに分ける。発注の回 ＝ start の mode = submit で、同じ回に拒否の事象が無い
        by_run: dict[str | None, list[dict]] = {}
        for e in events:
            by_run.setdefault(e.get("run_id"), []).append(e)
        submit_runs = []
        for rid, evs in by_run.items():
            st = next((e for e in evs if e.get("kind") == "start"), None)
            if st and st.get("mode") == "submit" and not any(e.get("kind") in REFUSED for e in evs):
                submit_runs.append((rid, st, evs))
        halted_today = any(e.get("kind") == "halted" for e in events)
        global_anoms = sorted({e["kind"] for e in events if e.get("kind") in ANOMALY and not e.get("trader")})

        led_today: dict[str, dict] = {}
        for r in ledger:
            led_today[r["trader"]] = r
        before_ledger = dict(last_ledger)
        last_ledger.update(led_today)
        held = {t: {s: _f(h.get("shares")) for s, h in (r.get("holdings") or {}).items()} for t, r in last_ledger.items()}
        gap = _positions_gap(positions, held)

        on_roster = {t for _, st, _ in submit_runs for t in (st.get("traders") or [])}
        sig_by: dict[str, list[dict]] = {}
        for r in signals:
            sig_by.setdefault(r["trader"], []).append(r)
        candidates = sorted(set(period) | {t for t in sig_by})

        for t in candidates:
            runs_t = [(rid, st, evs) for rid, st, evs in submit_runs if t in (st.get("traders") or [])]
            ev_t = [e for _, _, evs in runs_t for e in evs if e.get("trader") == t]
            flags = {e["kind"] for e in ev_t if e.get("kind") in FLAG_KINDS}
            held_off = any(e.get("kind") in ("paused", "over_total_budget") for e in ev_t)
            liquidating = "liquidate_flag" in flags
            if t not in period:
                if not (runs_t and sig_by.get(t) and not held_off and not liquidating):
                    if runs_t:
                        prev_flags[t] = flags
                    continue
                L = led_today.get(t) or {}
                conf = paper._conf(out_dir, t)
                budget = _f(L.get("budget_usd")) or None
                syms = conf["symbols"] or sorted({r["symbol"] for r in sig_by[t]})
                period[t] = {"since": d, "n": 0, "base_pnl": _pnl(before_ledger.get(t)), "base_mids": dict(mids),
                             "shares": hold_shares(budget or 0.0, mids, syms, conf["sizing"]), "budget": budget, "last_mids": {}}
            P = period[t]
            if submit_runs and t not in on_roster:
                del period[t]                       # 発注の回があるのに名簿に居ない ＝ 期間の外へ
                continue
            if not runs_t:
                kinds = sorted({e["kind"] for e in events if e.get("kind") in REFUSED}) or ["no_run"]
                rows.append(_blank_row(d, t, P, status="refused" if kinds != ["no_run"] else "no_run", problems=kinds, recon=recon,
                                       halted_new=halted_today and not prev_halted))
                continue
            rows.append(_row(d, t, P, runs_t, ev_t, sig_by.get(t, []), orders, transfers, led_today.get(t), mids, out_dir,
                             flags=flags, prev_flags=prev_flags.get(t, set()), halted_new=halted_today and not prev_halted,
                             liquidating=liquidating, held_off=held_off, gap=gap, global_anoms=global_anoms,
                             daily=daily.get((d, t)), recon=recon, paper=paper))
            prev_flags[t] = flags
            if any(e.get("kind") == "liquidate_complete" for e in ev_t):
                del period[t]
        prev_halted = halted_today
    _add_close_problems(rows, out_dir, paper)
    return rows


def _close_problems(out_dir: str) -> dict[str, dict[str, set[str]]]:
    """日付 → {銘柄: 問題}（`out/closes.json` が無ければ空）。"""
    text = livefs.read_doc(os.path.join(out_dir, "closes.json"), missing_ok=True)
    if not text:
        return {}
    snap = json.loads(text)
    out: dict[str, dict[str, set[str]]] = {}
    for k in snap.get("kept") or []:
        out.setdefault(k["date"], {}).setdefault(k["symbol"], set()).add("close_kept")
    for s, m in (snap.get("bars") or {}).items():
        for d, b in m.items():
            if "low" in b and "high" in b and not (b["low"] <= b["close"] <= b["high"]):
                out.setdefault(d, {}).setdefault(s, set()).add("close_out_of_range")
    return out


def _add_close_problems(rows: list[dict], out_dir: str, paper) -> None:
    """その人の銘柄（設定。読めなければ全部）に当たった日の行に、控えの問題を足す。"""
    cp = _close_problems(out_dir)
    if not cp:
        return
    for r in rows:
        day = cp.get(r["date"])
        if not day:
            continue
        syms = set(paper._conf(out_dir, r["trader"])["symbols"]) or set(day)
        add = sorted({k for s, ks in day.items() if s in syms for k in ks})
        have = [p for p in (r.get("problems") or "").split(";") if p]
        r["problems"] = ";".join(have + [k for k in add if k not in have])


def _reconcile_log(out_dir: str, state_dir: str | None) -> list[dict]:
    """`reconcile.py` の書き込み（人の手）。置き場は `state/<env>/reconcile.log`（記録の隣）。"""
    base = state_dir or os.path.join(os.path.dirname(os.path.abspath(out_dir)), "state")
    out: list[dict] = []
    for env in ("prod", "cert"):
        out += _jsonl(os.path.join(base, env, "reconcile.log"))
    return out


def _recon_count(recon: list[dict], d: str, t: str) -> int:
    return sum(1 for r in recon if str(r.get("date") or r.get("at") or "")[:10] == d and r.get("trader") == t)


def _blank_row(d: str, t: str, P: dict, *, status: str, problems: list[str], recon: list[dict], halted_new: bool) -> dict:
    """発注の回が無い日（起動しなかった ／ 断られた）の行。"""
    P["n"] += 1
    manual = []
    if halted_new:
        manual.append("halt")
    rc = _recon_count(recon, d, t)
    if rc:
        manual.append(f"reconcile {rc}")
    row = {k: None for k in COLUMNS}
    row.update({"date": d, "trader": t, "test": False, "since": P["since"], "day_n": P["n"], "status": status,
                "d_ran": 0, "d_ok": 0, "d_manual": _manual_count(manual), "d_manual_detail": " ／ ".join(manual),
                "problems": ";".join(problems)})
    return row


def _manual_count(manual: list[str]) -> int:
    return sum(int(m.split()[-1]) if m.split()[-1].isdigit() else 1 for m in manual)


def _row(d, t, P, runs_t, ev_t, sigs, orders, transfers, L, mids, out_dir, *, flags, prev_flags, halted_new, liquidating, held_off,
         gap, global_anoms, daily, recon, paper) -> dict:
    P["n"] += 1
    run_ids = {rid for rid, _, _ in runs_t}
    status = "liquidating" if liquidating else "paused" if held_off else "active"

    # --- 判断の内訳と C
    mine = [o for o in orders if o.get("run_id") in run_ids and o.get("mode") == "submit"
            and any(p.get("trader") == t for p in (o.get("parts") or []))]
    acts: dict[tuple, str] = {}
    for o in mine:
        acts[(o.get("run_id"), o["symbol"])] = o.get("side")
    for x in transfers:
        if x.get("run_id") in run_ids and t in (x.get("buyer"), x.get("seller")):
            acts[(x.get("run_id"), x["symbol"])] = "buy" if x.get("buyer") == t else "sell"
    for e in ev_t:
        k = e.get("kind")
        if k in ("hold", "skip") and e.get("symbol"):
            acts.setdefault((e.get("run_id"), e["symbol"]), k)
        elif k in HELD_BACK and e.get("symbol"):
            acts.setdefault((e.get("run_id"), e["symbol"]), "held_back")
    theta = paper._threshold({}, out_dir, t)
    mismatch, detail, held_back = 0, [], 0
    counts = {"buy": 0, "sell": 0, "hold": 0, "skip": 0}
    for (rid, s), a in acts.items():
        if a in counts:
            counts[a] += 1
        elif a == "held_back":
            held_back += 1
    if not liquidating and not held_off:
        for r in sigs:
            if r.get("run_id") not in run_ids:
                continue
            want = expected_action(_f(r.get("buy")), _f(r.get("exit")), _f(r.get("position_before")) > 0, theta)
            got = acts.get((r.get("run_id"), r["symbol"]))
            if got == "held_back":
                continue
            if got != want:
                mismatch += 1
                detail.append(f"{r['symbol']}: 規則 {want} ／ 実物 {got or 'なし'}")

    # --- A
    a_vals = []
    filled = 0
    for o in mine:
        fills = o.get("fills") or []
        qty = sum(_f(f.get("shares")) for f in fills)
        if qty <= 0:
            continue
        filled += 1
        mid = _f((o.get("quote_at_signal") or {}).get("mid"))
        if mid:
            px = sum(_f(f["shares"]) * _f(f["price"]) for f in fills) / qty
            sign = 1.0 if o.get("side") == "buy" else -1.0
            a_vals.append(round(sign * (px - mid) / mid * 1e4, 2))

    # --- B（期間の初日からの累計）
    b_real = b_hold = b_diff = b_bp = None
    P["last_mids"].update(mids)
    if L:
        b_real = _pnl(L) - P["base_pnl"]
    if P["shares"] and all(s in P["base_mids"] for s in P["shares"]):
        px = P["last_mids"]
        if all(s in px for s in P["shares"]):
            b_hold = sum(q * (px[s] - P["base_mids"][s]) for s, q in P["shares"].items())
    if b_real is not None and b_hold is not None:
        b_diff = b_real - b_hold
        budget = P["budget"] or _f((L or {}).get("budget_usd"))
        b_bp = b_diff / budget * 1e4 if budget else None

    # --- D
    ends = [next((e for e in evs if e.get("kind") == "end"), None) for _, _, evs in runs_t]
    bad = sum(int(_f(e.get("bad"))) for e in ends if e)
    anoms = sorted({e["kind"] for e in ev_t if e.get("kind") in ANOMALY} | set(global_anoms))
    problems = list(anoms)
    if any(e is None for e in ends):
        problems.append("no_end")
    if bad:
        problems.append("bad")
    if mismatch:
        problems.append("c_mismatch")
    if a_vals and max(a_vals) > A_LINE_BP:
        problems.append("diff1_over")
    if gap:
        problems.append("gap")
    manual = []
    if len(runs_t) > 1:
        manual.append(f"rerun {len(runs_t) - 1}")
    for k in sorted(flags - prev_flags):
        manual.append(k)
    if halted_new:
        manual.append("halt")
    liq = sum(1 for _, _, evs in runs_t for e in evs if e.get("kind") == "liquidate_start"
              and (e.get("trader") == t or t in (e.get("traders") or [])))
    if liq:
        manual.append(f"liquidate.py {liq}")
    rc = _recon_count(recon, d, t)
    if rc:
        manual.append(f"reconcile {rc}")
    n_manual = _manual_count(manual)

    e3 = _num((daily or {}).get("diff3a_bp"))
    e3c = _num((daily or {}).get("diff3a_cum_bp"))
    return {"date": d, "trader": t, "test": bool(any(r.get("test") for r in sigs) or (L or {}).get("test")),
            "since": P["since"], "day_n": P["n"], "status": status,
            "signals": len([r for r in sigs if r.get("run_id") in run_ids]),
            "buys": counts["buy"], "sells": counts["sell"], "holds": counts["hold"], "skips": counts["skip"], "held_back": held_back,
            "orders": len(mine), "filled": filled,
            "a_diff1_bp": ";".join(f"{v:.2f}" for v in a_vals),
            "a_median_bp": _r(statistics.median(a_vals)) if a_vals else None, "a_max_bp": max(a_vals) if a_vals else None,
            "b_real_usd": _r(b_real, 4), "b_hold_usd": _r(b_hold, 4), "b_diff_usd": _r(b_diff, 4), "b_diff_bp": _r(b_bp),
            "c_mismatch": mismatch, "c_detail": " ／ ".join(detail),
            "d_ran": 1, "d_ok": int(not (set(problems) - NOT_FAILURE)),
            "d_manual": n_manual, "d_manual_detail": " ／ ".join(manual), "d_gap_shares": gap,
            "e_diff3a_bp": e3, "e_diff3a_cum_bp": e3c,
            "problems": ";".join(problems)}


def summarize(rows: list[dict], trader: str) -> dict | None:
    """その人の**いまの期間**（最後の `since`）のまとめ。画面と `--print` が使う（⚠ 画面はここで数えるだけ ＝ 数え直さない）。
    rows は `build_rows` の行でも、`yardstick.csv` を読んだ行（文字列）でもよい。"""
    mine = [r for r in rows if r.get("trader") == trader]
    if not mine:
        return None
    since = mine[-1]["since"]
    mine = [r for r in mine if r["since"] == since]
    a = [float(x) for r in mine for x in str(r.get("a_diff1_bp") or "").split(";") if x]
    last_b = next((r for r in reversed(mine) if _num(r.get("b_diff_usd")) is not None), None)
    e = [abs(v) for v in (_num(r.get("e_diff3a_bp")) for r in mine) if v is not None]
    days = len(mine)
    ok = sum(int(_num(r.get("d_ok")) or 0) for r in mine)
    ran = sum(int(_num(r.get("d_ran")) or 0) for r in mine)
    manual = sum(int(_num(r.get("d_manual")) or 0) for r in mine)
    c_days = sum(1 for r in mine if (_num(r.get("c_mismatch")) or 0) > 0)
    a_med = round(statistics.median(a), 2) if a else None
    e_mean = round(sum(e) / len(e), 2) if e else None
    return {
        "trader": trader, "since": since, "last": mine[-1]["date"], "days": days,
        "a": {"median_bp": a_med, "max_bp": max(a) if a else None, "n": len(a),
              "ok": None if a_med is None else a_med <= A_LINE_BP},
        "b": None if not last_b else {"diff_usd": _num(last_b["b_diff_usd"]), "diff_bp": _num(last_b["b_diff_bp"]),
                                       "real_usd": _num(last_b["b_real_usd"]), "hold_usd": _num(last_b["b_hold_usd"]),
                                       "date": last_b["date"]},
        "c": {"days": c_days, "ok": c_days == 0},
        "d": {"ok_days": ok, "ran_days": ran, "days": days, "manual": manual, "gap_shares": _num(mine[-1].get("d_gap_shares")),
              "ok": days > 0 and ok / days >= D_LINE_RATE and manual == 0},
        "e": {"mean_abs_bp": e_mean, "n": len(e), "ok": None if e_mean is None else e_mean <= E_LINE_BP},
    }


def write_csv(path: str, rows: list[dict]) -> None:
    f = io.StringIO(newline="")
    w = csv.DictWriter(f, fieldnames=COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in COLUMNS})
    livefs.write_doc(path, f.getvalue())


def read_csv(path: str) -> list[dict]:
    text = livefs.read_doc(path, missing_ok=True)
    return list(csv.DictReader(io.StringIO(text))) if text else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.environ.get("LT_OUT_DIR") or os.path.join(HERE, "out"))
    ap.add_argument("--daily", default=None, help="E の出どころ（既定は <out-dir>/daily.csv。無ければ E は空欄）")
    ap.add_argument("--state-dir", default=None, help="reconcile.log の置き場の親（既定は記録の隣の state/）")
    ap.add_argument("--csv", default=None, help="既定は <out-dir>/yardstick.csv")
    ap.add_argument("--print", action="store_true", help="書かずに、人ごとのいまの期間のまとめを出す")
    args = ap.parse_args()

    daily = args.daily or os.path.join(args.out_dir, "daily.csv")
    rows = build_rows(args.out_dir, daily, args.state_dir)
    if args.print:
        for t in sorted({r["trader"] for r in rows}):
            print(json.dumps(summarize(rows, t), ensure_ascii=False))
        return 0
    path = args.csv or os.path.join(args.out_dir, "yardstick.csv")
    write_csv(path, rows)
    print(f"{path}: {len(rows)} 行（{rows[0]['date']} 〜 {rows[-1]['date']}）" if rows else f"{path}: 0 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
