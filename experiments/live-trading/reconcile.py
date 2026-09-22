#!/usr/bin/env python3
"""人が台帳を口座に合わせる（口座の建玉と台帳の帳尻を合わせる 3 段のうちの段 3。live-trading.md §0-8）。

    python reconcile.py --env prod show                                   # 台帳の合計・最後に記録した口座の建玉・その差・控えの未完
    python reconcile.py --env prod add T1 KO 2 --price 70.15              # T1 の台帳に KO を 2 株足す（口座にはあるのに台帳に無い）
    python reconcile.py --env prod remove T2 KO 3                         # T2 の台帳から KO を 3 株消す（台帳にあるのに口座に無い。原価で消す ＝ 損益 0）
    python reconcile.py --env prod remove T2 KO 3 --price 69.80           # 売れていたと分かっているとき: その値段の売りとして入れる（実現損益・受渡し待ちに入る）
    python reconcile.py --env prod resolve lt-0123abcd --filled 4 --price 25.61   # 控えの未完を人が閉じる（約定していた）／ --not-filled（出ていなかった）

⚠ **ネットワークを使わない**（口座は読まない・注文も出さない）。口座の側は、口座の画面か注文履歴を人が見て確かめる。
⚠ **誰のぶんかは人が決める**。執行器は差を推測で割り振らない（トレーダーごとの成績が混ざる）。
⚠ `run.lock` を取る（執行器・運転手が動いている間は拒否）。機械のモードに従う木だけを触る（実売買 ＝ `state/<env>/`・シミュレーション ＝ `sim/<名前>/state/cert/`）。
   直した内容は台帳の `history` に `note: "reconcile"` で、`reconcile.log` に 1 行ずつ残す。
"""

from __future__ import annotations

import argparse
import getpass
import glob
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mode as modes  # noqa: E402
import recovery  # noqa: E402
from journal import Journal  # noqa: E402
from _livefs import livefs  # noqa: E402
from state import QTY_TOL, has_state, load_state, save_state, state_path  # noqa: E402


def last_recorded_positions(out_dir: str) -> tuple[str | None, list[dict]]:
    for path in reversed(livefs.find(out_dir, "*/positions.jsonl")):
        rows = [json.loads(line) for line in livefs.read_lines(path) if line.strip()]
        if rows:
            return f"{rows[-1].get('date')}（{rows[-1].get('when')}）", rows[-1].get("positions") or []
    return None, []


def show(state_dir: str, out_dir: str) -> int:
    states = recovery.load_all_states(state_dir)
    when, positions = last_recorded_positions(out_dir)
    print(f"台帳: {state_dir}（{len(states)} 人）   口座の建玉: {'最後の記録 ' + when if when else '記録なし'}  ⚠ いまの口座ではない（口座の画面で確かめる）")
    outside, short = recovery.check_positions(positions, states, set())
    account = recovery.account_quantities(positions)
    symbols = sorted({s for st in states.values() for s in st.holdings} | set(account))
    print(f"  {'銘柄':6s} {'口座':>12s} {'台帳の合計':>12s} {'差':>12s}  持っている人")
    for sym in symbols:
        holders = {n: st.holdings[sym].shares for n, st in states.items() if sym in st.holdings}
        ledger = sum(holders.values())
        mark = "⚠ 口座が少ない ＝ 売買が止まる" if sym in short else ("台帳の外の株（止めない）" if sym in outside else "")
        print(f"  {sym:6s} {account.get(sym, 0.0):12.6f} {ledger:12.6f} {account.get(sym, 0.0) - ledger:12.6f}  {holders or '—'}  {mark}")
    open_entries = Journal(state_dir).unfinished()
    print(f"控えの未完: {len(open_entries)} 件" + ("（次の起動で執行器が照会する。照会できなければ resolve で閉じる）" if open_entries else ""))
    for e in open_entries:
        print(f"  {e['ext']}  {e.get('date')}  {e['trader']} {e['side']} {e['symbol']} {e.get('shares') or e.get('value_usd')}  注文番号 {e.get('order_id', '—')}")
    return 1 if short or open_entries else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="人が台帳を口座に合わせる（ネットワークを使わない）")
    ap.add_argument("--env", choices=["cert", "prod"], default=None, help="実売買モードでは必須（台帳は state/<env>/）。シミュレーションモードでは cert")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--out-dir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    for name in ("add", "remove"):
        p = sub.add_parser(name)
        p.add_argument("trader"), p.add_argument("symbol"), p.add_argument("shares", type=float)
        p.add_argument("--price", type=float, required=name == "add", default=None)
        p.add_argument("--date", default=None, help="YYYY-MM-DD（既定は今日 UTC）")
        p.add_argument("--reason", default="")
    r = sub.add_parser("resolve")
    r.add_argument("ext")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--filled", type=float, default=None, metavar="SHARES")
    g.add_argument("--not-filled", action="store_true")
    r.add_argument("--price", type=float, default=None)
    r.add_argument("--reason", default="")
    args = ap.parse_args()

    try:
        machine = modes.read_mode()
    except modes.ModeError as exc:
        print(f"拒否: {exc}", file=sys.stderr)
        return 5
    print(modes.banner(machine))
    if machine.is_sim:
        root = modes.sim_root(machine.name)
        state_dir, out_dir = args.state_dir or os.path.join(root, "state", "cert"), args.out_dir or os.path.join(root, "out")
        if not (modes.inside(state_dir, root) and modes.inside(out_dir, root)):
            print(f"拒否: シミュレーションモードでは {root} の下だけを触る", file=sys.stderr)
            return 2
    else:
        if not args.env and not args.state_dir:
            print("拒否: 実売買モードでは --env cert ／ prod を指定する（台帳は環境ごと）", file=sys.stderr)
            return 2
        state_dir = args.state_dir or os.environ.get("LT_STATE_DIR") or os.path.join(HERE, "state", args.env)
        out_dir = args.out_dir or os.environ.get("LT_OUT_DIR") or os.path.join(HERE, "out")
        if modes.inside(state_dir, modes.sim_base()):
            print("拒否: 実売買モードでシミュレーションの台帳は触らない", file=sys.stderr)
            return 2
    lock = modes.RunLock()
    if not lock.acquire(machine.mode, f"reconcile {args.cmd}"):
        print(f"拒否: 執行器 ／ 運転手が動いている（run.lock の持ち主 {lock.holder()}）", file=sys.stderr)
        return 6
    try:
        if args.cmd == "show":
            return show(state_dir, out_dir)
        now = datetime.now(timezone.utc)
        log = {"at": now.isoformat(timespec="seconds"), "by": getpass.getuser(), "mode": machine.mode, "cmd": args.cmd, "reason": getattr(args, "reason", "")}
        journal = Journal(state_dir)
        if args.cmd == "resolve":
            entry = next((e for e in journal.unfinished() if e["ext"] == args.ext), None)
            if entry is None:
                print(f"拒否: 控えに未完の {args.ext} は無い（reconcile.py show）", file=sys.stderr)
                return 2
            if args.filled is not None and (args.filled <= 0 or args.price is None or args.price <= 0):
                print("拒否: --filled には株数（正）と --price（約定価格）が要る", file=sys.stderr)
                return 2
            trader, symbol, side, date = entry["trader"], entry["symbol"], entry["side"], entry.get("date") or now.date().isoformat()
            shares, price = (args.filled or 0.0), args.price
        else:
            trader, symbol, shares, price = args.trader, args.symbol.upper(), args.shares, args.price
            side, date = ("buy" if args.cmd == "add" else "sell"), args.date or now.date().isoformat()
            if shares <= 0 or (price is not None and price <= 0):
                print("拒否: 株数と価格は正の数", file=sys.stderr)
                return 2
            if not has_state(state_dir, trader):
                print(f"拒否: {trader} の台帳が無い（{state_path(state_dir, trader)}）", file=sys.stderr)
                return 2
        st = load_state(state_dir, trader)
        before = st.holdings[symbol].shares if symbol in st.holdings else 0.0
        try:
            if args.cmd == "resolve" and args.not_filled:
                pass
            elif side == "buy":
                st.apply_buy(symbol, shares, price, date, fee=float(entry.get("fee_usd") or 0) if args.cmd == "resolve" else 0.0, note="reconcile")
            elif price is not None:
                st.apply_sell(symbol, shares, price, date, fee=float(entry.get("fee_usd") or 0) if args.cmd == "resolve" else 0.0, note="reconcile")
            else:
                h = st.holdings.get(symbol)                                   # 原価で消す（損益 0・受渡し待ちにも入れない）
                if not h or shares > h.shares + QTY_TOL:
                    raise ValueError(f"{trader}: {symbol} の持ち分 {h.shares if h else 0} を超えて消せない")
                h.shares = round(h.shares - min(shares, h.shares), 6)
                if h.shares <= QTY_TOL:
                    del st.holdings[symbol]
                st.history.append({"date": date, "symbol": symbol, "side": "remove", "shares": shares, "price": None, "fee": 0.0, "note": "reconcile"})
        except ValueError as exc:
            print(f"拒否: {exc}", file=sys.stderr)
            return 2
        save_state(state_dir, st)
        if args.cmd == "resolve":
            journal.close(args.ext, "manual_not_filled" if args.not_filled else "manual_filled", shares=shares, price=price, by=log["by"])
        after = st.holdings[symbol].shares if symbol in st.holdings else 0.0
        log.update(trader=trader, symbol=symbol, side=side, shares=shares, price=price, date=date, before=before, after=after, ext=getattr(args, "ext", None))
        livefs.append(os.path.join(state_dir, "reconcile.log"), json.dumps(log, ensure_ascii=False))
        print(f"{trader} の {symbol}: {before:g} 株 → {after:g} 株（{args.cmd}。台帳 {state_path(state_dir, trader)}・履歴 reconcile.log）")
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
