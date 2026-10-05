#!/usr/bin/env python3
"""手じまい: 指定した人の売買履歴の持ち株を成行で売り、約定を売買履歴に写す（プラン docs/plans/liquidate.md。2026-10-05 利用者の指示）。

    python liquidate.py --traders T1,T3                                   # cert: dry-run（既定）。持ち株を全部
    python liquidate.py --traders T1 --symbols T,PFE --mode submit        # cert: 銘柄を絞って発注
    python liquidate.py --traders T1,T3 --env prod --mode dry-run --allow-prod-dry-run
    TT_ALLOW_PROD_ORDERS=1 python liquidate.py --traders T1,T3 --env prod --mode submit --i-know-this-is-real-money --while-halted

⚠ **毎日の執行器（run_day.py）とは別の 1 本**で、部品（発注・控え・売買履歴・記録・許可・印・ロック）はそのまま使う ＝ 毎日の経路は変えない。
⚠ 売るのは **その人の売買履歴にある持ち株だけ**。口座にあって売買履歴に無い株は売らない（差を推測で誰かに割り振らない ＝ live-trading.md §0-8）。
⚠ 許可は毎日の執行器と同じ 3 段（既定 dry-run ／ 本番の dry-run は --allow-prod-dry-run ／ 本番の発注は TT_ALLOW_PROD_ORDERS=1 ＋ --i-know-this-is-real-money）。
   「本番の機械ではない」印・シミュレーションモードでは拒む。run.lock を取る（毎日の執行器と同時に動かない）。
⚠ HALT がある間は拒む。--while-halted を付けたときだけ通す（止めてから手じまいする、が普通の順）。
⚠ 動くのは市場が開いている間（NYSE の営業日の 9:30〜引け）。15:45〜16:05 の時間帯には限らない。--ignore-market-hours はテスト・モック用。
⚠ 記録は毎日の執行器と同じ out/<日付>/（orders.jsonl の行に liquidate: true）。状態を書くのは --mode submit のときだけ。
"""

from __future__ import annotations

import argparse
import atexit
import os
import sys
from datetime import datetime, time

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
sys.path.insert(0, HERE)
sys.path.insert(0, SAMPLE_DIR)

import market_calendar  # noqa: E402
import record  # noqa: E402
from ttclient import ApiError  # noqa: E402

import ledger  # noqa: E402
import mode as modes  # noqa: E402
import recovery  # noqa: E402
import run_day as day  # noqa: E402  DayRecorder・make_client・now_et・is_loopback（毎日の執行器の部品をそのまま使う）
from execute import UNKNOWN, Executor, allocate_fills  # noqa: E402
from journal import Journal  # noqa: E402
from plan import NetOrder  # noqa: E402
from state import load_state, save_state  # noqa: E402
from trader import load_traders  # noqa: E402
from ttclient import load_env  # noqa: E402

MARKET_OPEN = time(9, 30)
DEFAULT_CANCEL_AFTER = 300.0   # 成行なのでふつうは数秒で約定する。未約定は 5 分で取り消す


def market_refusal(now_et: datetime) -> str | None:
    """市場が閉まっていれば理由を返す（開いていれば None）。暦は毎日の執行器と同じ `market_calendar`（半日立会は 13:00 ET 引け）。"""
    cal = market_calendar.nyse()
    d = now_et.date()
    if now_et.weekday() >= 5:
        return f"{d} は土日"
    if cal.is_holiday(d):
        return f"{d} は NYSE の休場日"
    close = cal.close_et(d)
    t = now_et.time().replace(tzinfo=None)
    if t < MARKET_OPEN:
        return f"市場が開く前（{MARKET_OPEN:%H:%M} ET から）"
    if t >= close:
        return f"引けの後（{d} は {close:%H:%M} ET 引け）"
    return None


def sell_orders(states: dict, only: set[str] | None, blocked: set[str], quotes: dict[str, float]) -> tuple[list[NetOrder], list[dict]]:
    """各人の売買履歴の持ち株 → 成行の売り（1 注文 1 トレーダー）。`only` で銘柄を絞る。`blocked`（口座と帳尻の合わない銘柄）は見送る。"""
    orders: list[NetOrder] = []
    events: list[dict] = []
    for name, st in states.items():
        for sym in sorted(st.holdings):
            h = st.holdings[sym]
            if h.shares <= 0 or (only is not None and sym not in only):
                continue
            if sym in blocked:
                events.append({"kind": "blocked_symbol", "trader": name, "symbol": sym, "side": "sell",
                               "note": "口座と売買履歴の帳尻が合っていない銘柄（position_short ／ journal_unresolved）。今日は売らない"})
                continue
            shares = round(h.shares, 6)
            usd = round(shares * quotes.get(sym, h.avg_price), 2)
            orders.append(NetOrder(sym, "sell", shares, usd, "shares", [{"trader": name, "shares": shares, "usd": usd}]))
    return orders, events


def main() -> int:
    ap = argparse.ArgumentParser(description="手じまい: 指定した人の売買履歴の持ち株を全部売る")
    ap.add_argument("--traders", required=True, help="カンマ区切り（config/traders/<名前>.toml）")
    ap.add_argument("--symbols", default=None, help="カンマ区切りで銘柄を絞る（既定は持ち株の全部）")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（既定は今日 ET）")
    ap.add_argument("--env", default=None, choices=["cert", "prod"], help="既定は .env の TT_ENV、無ければ cert")
    ap.add_argument("--mode", default="dry-run", choices=["dry-run", "submit"])
    ap.add_argument("--while-halted", action="store_true", help="HALT があっても売る（止めてから手じまいする、が普通の順）")
    ap.add_argument("--ignore-market-hours", action="store_true", help="市場が閉まっていても動かす（テスト・モック用）")
    ap.add_argument("--out-dir", default=os.environ.get("LT_OUT_DIR") or os.path.join(HERE, "out"))
    ap.add_argument("--state-dir", default=None, help="既定は state/<env>/")
    ap.add_argument("--traders-dir", default=os.environ.get("LT_TRADERS_DIR") or os.path.join(HERE, "config", "traders"))
    ap.add_argument("--allow-prod-dry-run", action="store_true")
    ap.add_argument("--i-know-this-is-real-money", action="store_true", help="本番で発注を許す（TT_ALLOW_PROD_ORDERS=1 も要る）")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--retry-interval", type=float, default=60.0)
    ap.add_argument("--auth-retry-wait", type=float, default=30.0)
    ap.add_argument("--cancel-after", type=float, default=DEFAULT_CANCEL_AFTER, help="未約定を取り消すまでの秒数")
    ap.add_argument("--serial", action="store_true", help="発注を 1 本ずつ約定まで待つ形（既定は 2 段 ＝ 先に全部出してから約定を確かめる）")
    args = ap.parse_args()

    cfg = load_env(os.environ.get("TT_ENV_FILE") or os.path.join(SAMPLE_DIR, ".env"))
    env = args.env or cfg.get("TT_ENV", "cert")
    allow_prod_orders = args.i_know_this_is_real_money and cfg.get("TT_ALLOW_PROD_ORDERS") == "1"
    is_mock = bool(cfg.get("TT_REST_BASE") or cfg.get("TT_PROD_REST_BASE"))

    # ---- 0. モード・置き場・印・許可・ロック（毎日の執行器と同じ順。⚠ 手じまいは本物の時計だけ ＝ 仮の時計は無い）
    try:
        machine = modes.read_mode()
    except modes.ModeError as exc:
        print(f"拒否: {exc}", file=sys.stderr)
        return 5
    sample_out = os.environ.get("TT_OUT_DIR") or os.path.join(SAMPLE_DIR, "out")
    halt_file = os.environ.get("TT_HALT_FILE") or os.path.join(sample_out, "HALT")
    state_dir = args.state_dir or os.environ.get("LT_STATE_DIR") or os.path.join(HERE, "state", env)
    in_sim_tree = [p for p in (args.out_dir, state_dir, halt_file) if modes.inside(p, modes.sim_base())]
    if in_sim_tree:
        print(f"拒否: シミュレーションの置き場を指している: {in_sim_tree}", file=sys.stderr)
        return 2
    if modes.overridden() and any(modes.inside(p, os.path.join(HERE, d)) for p, d in ((args.out_dir, "out"), (state_dir, "state"))):
        print("拒否: LT_MODE_DIR（テスト用の MODE の置き場）を使うときは、LT_OUT_DIR ／ LT_STATE_DIR も本物の out/・state/ の外へ向ける", file=sys.stderr)
        return 2
    print(modes.banner(machine))
    date = args.date or day.now_et().strftime("%Y-%m-%d")
    run_id = day.CLOCK.now().strftime("%Y%m%dT%H%M%SZ")
    names = [t.strip() for t in args.traders.split(",") if t.strip()]
    if any(n.startswith(modes.SIM_TRADER_PREFIX) for n in names):
        print(f"拒否: 手じまいは本物の人だけ（{modes.SIM_TRADER_PREFIX} で始まる名前は不可）", file=sys.stderr)
        return 2
    traders = load_traders(names, args.traders_dir)
    only = None if not args.symbols else {s.strip() for s in args.symbols.split(",") if s.strip()}
    rec = day.DayRecorder(args.out_dir, date, env, run_id, is_mock)
    meta = {"kind": "liquidate_start", "mode": args.mode, "traders": [t.name for t in traders], "symbols": sorted(only) if only else None,
            "test": any(t.test for t in traders), "halt_file": halt_file, "while_halted": args.while_halted,
            "now_et": day.now_et().isoformat(timespec="seconds"), "sdk": record.sdk_versions()}
    if env == "prod" and args.mode == "submit":
        mark = modes.read_not_production()
        if mark is not None:
            rec.write("events", {**meta, "kind": "refused_not_production", **mark.as_dict(), "note": "「本番の機械ではない」印があるので本番の発注はしない（本番は 13500t）"})
            print(f"拒否: この機械は本番の機械ではない（{modes.not_production_file()}: {mark.describe()}）", file=sys.stderr)
            return 7
        if not allow_prod_orders:
            print("拒否: 本番の発注には TT_ALLOW_PROD_ORDERS=1 と --i-know-this-is-real-money の両方が要る（取消・dry-run の鍵では開かない）", file=sys.stderr)
            return 2
    if env == "prod" and args.mode == "dry-run" and not (args.allow_prod_dry_run or allow_prod_orders):
        print("拒否: 本番の dry-run には --allow-prod-dry-run が要る", file=sys.stderr)
        return 2
    if machine.is_sim:
        rec.write("events", {**meta, "kind": "refused_mode_sim", "sim_name": machine.name, "since": machine.since,
                             "note": "機械がシミュレーションモードなので手じまいは動かない（戻すのは simctl.py mode real）"})
        print(f"拒否: 機械がシミュレーションモード（{machine.name}・{machine.since} から）", file=sys.stderr)
        return 5
    if modes.overridden() and not day.is_loopback(cfg.get("TT_PROD_REST_BASE" if env == "prod" else "TT_REST_BASE")) and args.mode == "submit":
        print("拒否: LT_MODE_DIR（テスト用の MODE ／ run.lock の置き場）は、モック以外への submit では使えない", file=sys.stderr)
        return 2
    lock = modes.RunLock()
    if not lock.acquire(machine.mode, f"liquidate {env} {args.mode}"):
        rec.write("events", {**meta, "kind": "refused_lock_busy", "holder": lock.holder()})
        print(f"拒否: 別の執行器 ／ 運転手が動いている（run.lock の持ち主 {lock.holder()}）", file=sys.stderr)
        return 6
    atexit.register(lock.release)
    print(f"=== 手じまい {date} {env} mode={args.mode} traders={[t.name for t in traders]} 記録: {rec.dir} ===")

    if os.path.exists(halt_file) and not args.while_halted:
        rec.write("events", {**meta, "kind": "halted", "note": "HALT があるので売らない（手じまいは --while-halted で通す）"})
        print(f"拒否: 停止フラグがある（{halt_file}）。手じまいなら --while-halted", file=sys.stderr)
        return 3
    refusal = None if args.ignore_market_hours else market_refusal(day.now_et())
    if refusal:
        rec.write("events", {**meta, "kind": "out_of_market", "reason": refusal, "note": "市場が閉まっている間は成行を出さない"})
        print(f"拒否: {refusal}。テストなら --ignore-market-hours", file=sys.stderr)
        return 4
    rec.write("events", meta)
    # ⚠ --while-halted のとき、発注の部品（Executor）には「無い」道を渡す ＝ 部品の HALT の見張りをこの実行だけ外す（本物の HALT は触らない）
    exec_halt_file = halt_file if not args.while_halted else halt_file + ".ignored-by-liquidate"

    # ---- 1. 認証・口座・建玉・残高 → 控えの未完 → 突き合わせ（毎日の執行器と同じ順。売買の前に済ませる）
    try:
        client = day.make_client(cfg, env, args.allow_prod_dry_run, allow_prod_orders, rec, args.auth_retry_wait)
    except ApiError as exc:
        rec.write("events", {"kind": "auth_failed", "status": exc.status, "code": exc.code})
        print(f"中断: 認証失敗 {exc}", file=sys.stderr)
        return 1
    accounts = client.list_accounts()
    account = cfg.get("TT_PROD_ACCOUNT_NUMBER" if env == "prod" else "TT_ACCOUNT_NUMBER") or accounts[0]["account-number"]
    rec.mask.add_account(account)
    balances = client.get_balances(account)
    positions = client.list_positions(account)
    rec.write("balances", {"when": "before", "balances": record.excerpt(balances, limit=40)})
    rec.write("positions", {"when": "before", "positions": [{k: p.get(k) for k in ("symbol", "quantity", "quantity-direction", "average-open-price")} for p in positions]})
    journal = Journal(state_dir, date, now=day.CLOCK.now)
    rec_events, blocked = recovery.recover_unfinished(journal, client, account, state_dir, date, write=args.mode == "submit")
    for ev in rec_events:
        rec.write("events", ev)
        print(f"⚠ 控えの未完: {ev['trader']} {ev['side']} {ev['symbol']} → {ev.get('outcome') or ev.get('reason')}", file=sys.stderr)
    if any(ev["kind"] == "journal_recovered" and ev.get("shares") for ev in rec_events):
        positions = client.list_positions(account)
    states = {t.name: load_state(state_dir, t.name) for t in traders}
    all_states = {**recovery.load_all_states(state_dir), **states}
    want = {sym for st in states.values() for sym in st.holdings}
    outside, short = recovery.check_positions(positions, all_states, want)
    if outside:
        rec.write("events", {"kind": "positions_outside_ledger", "symbols": outside, "note": "口座のほうが多い ＝ 売買履歴の外の株。手じまいでは売らない"})
    for sym, row in short.items():
        rec.write("events", {"kind": "position_short", "symbol": sym, **row, "note": "売買履歴にあるはずの株が口座に無い ＝ この銘柄は売らない。reconcile.py で合わせる"})
        print(f"⚠ 口座の建玉が売買履歴より少ない: {sym} 口座 {row['account']} ／ 売買履歴 {row['ledger']}。売らない（reconcile.py）", file=sys.stderr)
    blocked |= set(short)

    # ---- 2. 気配（記録のため。注文は成行）→ 注文
    quote_client = client
    if env != "prod" and cfg.get("TT_PROD_CLIENT_SECRET") and cfg.get("TT_PROD_REFRESH_TOKEN"):
        try:
            quote_client = day.make_client(cfg, "prod", False, False, rec, args.auth_retry_wait)
        except (ApiError, SystemExit) as exc:
            rec.write("events", {"kind": "quote_client_failed", "note": str(exc)[:200]})
    quotes: dict[str, float] = {}
    quotes_raw: dict[str, dict] = {}
    for sym in sorted(want):
        try:
            q = quote_client.get_quote(sym)
            bid, ask, last = (float(q.get(k) or 0) for k in ("bid", "ask", "last"))
            mid = (bid + ask) / 2 if bid and ask else (last or bid or ask)
            quotes[sym] = mid
            quotes_raw[sym] = {"bid": bid, "ask": ask, "last": last, "mid": mid, "updated-at": q.get("updated-at"),
                               "server_date": getattr(quote_client, "last_date_header", None), "read_at": day.CLOCK.now().isoformat(timespec="milliseconds")}
        except ApiError as exc:
            rec.write("events", {"kind": "quote_failed", "symbol": sym, "status": exc.status, "code": exc.code})
    rec.write("quotes", {"quotes": quotes_raw})
    orders, ev = sell_orders(states, only, blocked, quotes)
    for e in ev:
        rec.write("events", e)
    print(f"持ち株 {sum(len(st.holdings) for st in states.values())} 本 → 売り注文 {len(orders)} 件")
    for o in orders:
        print(f"  {o.parts[0]['trader']:6s} {o.symbol:6s} {o.shares:>10} 株（約 ${o.value_usd:,.2f}）")

    # ---- 3. 執行と売買履歴（submit のときだけ状態を書く。1 注文ごとに 控え → 約定 → 保存 → 控えを閉じる）
    ledger_errors = 0
    fills_all: list[dict] = []

    def settle(res) -> None:
        nonlocal ledger_errors
        o = res.order
        rec.write("orders", {
            "liquidate": True,
            "symbol": o.symbol, "side": o.side, "sizing": o.sizing, "shares": o.shares, "value_usd": o.value_usd, "parts": o.parts,
            "external_id": res.external_id, "mode": args.mode, "quote_at_signal": res.quote_at_signal,
            "dry_run": res.dry_run, "submitted": res.submitted, "transitions": res.transitions, "final_status": res.final_status,
            "fills": [f.__dict__ for f in res.fills], "amounts": res.amounts(), "attempts": res.attempts, "cancelled": res.cancelled, "error": res.error,
            "elapsed_ms": res.elapsed_ms, "test": any(t.test for t in traders if t.name in {p["trader"] for p in o.parts}),
        })
        print(f"  {o.side:4s} {o.symbol:6s} {o.shares:>10} → {res.final_status} {('' if not res.error else res.error.get('message', ''))[:80]}")
        fills = allocate_fills(res)
        fills_all.extend(fills)
        if args.mode != "submit":
            return
        if res.final_status == UNKNOWN:
            rec.write("events", {"kind": "order_unknown", "trader": o.parts[0]["trader"], "symbol": o.symbol, "side": o.side,
                                 "external_id": res.external_id, "order_id": (res.submitted or {}).get("order_id"),
                                 "note": "状態を読めなかった注文。控えは開いたまま ＝ 次の起動が照会して売買履歴に戻す（急ぐなら reconcile.py resolve）"})
            print(f"⚠ 状態を読めなかった注文: {o.side} {o.symbol}（控えは開いたまま）", file=sys.stderr)
            return
        for f in fills:
            try:
                states[f["trader"]].apply_sell(f["symbol"], f["shares"], f["price"], date, fee=f.get("fee", 0.0), note="手じまい")
            except ValueError as exc:
                ledger_errors += 1
                rec.write("events", {"kind": "ledger_error", "fill": f, "note": str(exc)[:300]})
                print(f"⚠ 売買履歴に入れられない約定: {exc}", file=sys.stderr)
            save_state(state_dir, states[f["trader"]])
        journal.done(res.external_id, str(res.final_status), shares=sum(f["shares"] for f in fills))

    ex = Executor(client, account, None, exec_halt_file, mode=args.mode, retries=args.retries, retry_interval=args.retry_interval,
                  cancel_after=args.cancel_after, journal=journal, pipeline=not args.serial)
    results = ex.run_all(orders, quotes_raw, on_result=settle)

    # ---- 4. 後: 残高・建玉・各人の日次の行・口座と売買履歴の突き合わせ
    diff: dict[str, dict] = {}
    if args.mode == "submit":
        for t in traders:
            states[t.name].last_date = date
            save_state(state_dir, states[t.name])
        try:
            rec.write("balances", {"when": "after", "balances": record.excerpt(client.get_balances(account), limit=40)})
            after = client.list_positions(account)
            rec.write("positions", {"when": "after", "positions": [{k: p.get(k) for k in ("symbol", "quantity", "quantity-direction", "average-open-price")} for p in after]})
            all_after = {**recovery.load_all_states(state_dir), **states}
            outside_after, short_after = recovery.check_positions(after, all_after, want)
            diff = {**outside_after, **short_after}      # {銘柄: {account, ledger, diff, holders}}（多いほうも少ないほうも）
        except ApiError as exc:
            rec.write("events", {"kind": "after_read_failed", "status": exc.status, "code": exc.code})
    for t in traders:
        rec.write("ledger", ledger.daily_row(t, states[t.name], quotes, date))

    bad = [r for r in results if r.final_status in ("error", "guarded", "halted", "not_submitted", UNKNOWN)]
    rec.write("events", {"kind": "liquidate_end", "now_et": day.now_et().isoformat(timespec="seconds"), "orders": len(results), "bad": len(bad),
                         "fills": len(fills_all), "sold_usd": round(sum(f["shares"] * f["price"] for f in fills_all), 2),
                         **({"ledger_errors": ledger_errors} if ledger_errors else {}), **({"blocked_symbols": sorted(blocked)} if blocked else {}),
                         **({"diff_after": diff} if diff else {})})
    left = {t.name: sorted(states[t.name].holdings) for t in traders if states[t.name].holdings}
    print(f"完了。注文 {len(results)} 件・約定 {len(fills_all)} 件・問題 {len(bad) + ledger_errors} 件・残った持ち株 {left or 'なし'}"
          + (f"・⚠ 口座 − 売買履歴 の差 {diff}" if diff else "") + f"。記録: {rec.dir}")
    return 1 if bad or ledger_errors or blocked or diff else 0


if __name__ == "__main__":
    sys.exit(main())
