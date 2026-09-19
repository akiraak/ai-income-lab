#!/usr/bin/env python3
"""1 営業日の窓を 1 回通す（プラン §2-5）: 確認 → 合図 → 計画 → dry-run → 発注 → 約定確認 → 取消 → 記録。

    python run_day.py --traders test_a --mode plan                 # 合図と計画だけ（口座を読むが注文は組まない）
    python run_day.py --traders test_a --mode dry-run              # cert: dry-run まで
    python run_day.py --traders test_a --mode submit               # cert: 発注まで
    python run_day.py --traders test_a --env prod --mode dry-run --allow-prod-dry-run
    TT_ALLOW_PROD_ORDERS=1 python run_day.py --traders test_a --env prod --mode submit --i-know-this-is-real-money

⚠ 本番の鍵は `ttclient.Client` の 3 段そのまま。⚠ **鍵を入れて起動するのは利用者**（CLAUDE.md の例外）。
⚠ 記録は `out/<日付>/*.jsonl`（`Masker` 経由。口座番号・トークンは出ない）。状態は `state/<トレーダー>.json`。
⚠ `--mode submit` 以外では状態を書き換えない。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
sys.path.insert(0, HERE)
sys.path.insert(0, SAMPLE_DIR)

import market_calendar  # noqa: E402
import record  # noqa: E402
from ttclient import ApiError, Client, ProductionGuard, load_env  # noqa: E402

import ledger  # noqa: E402
import plan as planning  # noqa: E402
import signals as signalling  # noqa: E402
from execute import Executor, allocate_fills  # noqa: E402
from state import load_state, save_state  # noqa: E402
from trader import load_traders  # noqa: E402

ET = ZoneInfo("America/New_York")
WINDOW_START = (15, 45)
WINDOW_END = (16, 5)
DEFAULT_MAX_TOTAL_BUDGET = 1000.0   # live-trading.md §0-2。全トレーダーの予算の合計の上限（口座の残高）
DEFAULT_MAX_DAY_USD = 1000.0        # 1 日の買いの合計の上限


def window_refusal(now_et: datetime) -> str | None:
    """執行の窓の外なら理由を返す（中なら None）。営業日は NYSE の暦（`market_calendar`。管理画面と同じもの）で見る。

    ⚠ **半日立会（13:00 ET 引け）の日も拒否する**: 窓 15:45〜16:05 は引けの後で、成行は通らない。
       半日の日に窓を動かすかは決めごと（live-trading.md §0-2）で、まだ決めていない。
    """
    cal = market_calendar.nyse()
    day = now_et.date()
    if now_et.weekday() >= 5:
        return f"{day} は土日"
    if cal.is_holiday(day):
        return f"{day} は NYSE の休場日"
    if cal.is_early_close(day):
        return f"{day} は半日立会（{cal.close_et(day):%H:%M} ET 引け）。執行の窓は引けの後になる"
    if not WINDOW_START <= (now_et.hour, now_et.minute) < WINDOW_END:
        return "執行の窓（15:45〜16:05 ET）の外"
    return None


def in_window(now_et: datetime) -> bool:
    return window_refusal(now_et) is None


def now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(ET)


class DayRecorder:
    """`out/<日付>/<種類>.jsonl` に 1 行ずつ。全行が Masker を通る。"""

    def __init__(self, out_dir: str, date: str, env: str, run_id: str, mock: bool):
        self.dir = os.path.join(out_dir, date)
        os.makedirs(self.dir, exist_ok=True)
        self.date, self.env, self.run_id, self.mock = date, env, run_id, mock
        self.mask = record.Masker()

    def write(self, kind: str, row: dict) -> None:
        row = {"date": self.date, "env": self.env, "run_id": self.run_id, **row}
        if self.mock:
            row["mock"] = True
        with open(os.path.join(self.dir, f"{kind}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(self.mask(row), ensure_ascii=False) + "\n")


def make_client(cfg: dict, env: str, allow_prod_dry_run: bool, allow_prod_orders: bool, rec: DayRecorder, auth_retry_wait: float = 30.0) -> Client:
    if env == "prod":
        secret, refresh, cid = cfg.get("TT_PROD_CLIENT_SECRET"), cfg.get("TT_PROD_REFRESH_TOKEN"), cfg.get("TT_PROD_CLIENT_ID")
        base = cfg.get("TT_PROD_REST_BASE") or None
    else:
        secret, refresh, cid = cfg.get("TT_CLIENT_SECRET"), cfg.get("TT_REFRESH_TOKEN"), cfg.get("TT_CLIENT_ID")
        base = cfg.get("TT_REST_BASE") or None
    if not secret or not refresh:
        raise SystemExit(f"エラー: {env} の資格情報（client secret ／ refresh token）が無い")
    client = Client(env=env, allow_prod_orders=allow_prod_orders, allow_prod_dry_run=allow_prod_dry_run, rest_base=base)
    rec.mask.add(secret, f"<{env}_client_secret:masked>")
    rec.mask.add(refresh, f"<{env}_refresh_token:masked>")
    for attempt in (1, 2):
        try:
            client.authenticate(client_secret=secret, refresh_token=refresh, client_id=cid or None)
            break
        except ApiError as exc:
            # ⚠ 401（資格情報の誤り）は再試行しない（IP ブロック）。5xx（nginx の 502。cert で実測）だけ 1 回待って取り直す
            if exc.status >= 500 and attempt == 1:
                rec.write("events", {"kind": "auth_5xx_retry", "env": env, "status": exc.status, "wait_s": auth_retry_wait})
                time.sleep(auth_retry_wait)
                continue
            raise
    rec.mask.add(client.token.access_token, f"<{env}_access_token:masked>")
    return client


def main() -> int:
    ap = argparse.ArgumentParser(description="実売買の執行器: 1 営業日の窓を 1 回通す")
    ap.add_argument("--traders", required=True, help="カンマ区切り（config/traders/<名前>.toml）")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（既定は今日 ET）")
    ap.add_argument("--env", default=None, choices=["cert", "prod"], help="既定は .env の TT_ENV、無ければ cert")
    ap.add_argument("--mode", default="dry-run", choices=["plan", "dry-run", "submit"])
    ap.add_argument("--ignore-window", action="store_true", help="15:45〜16:05 ET の外でも動かす（テスト・モック用）")
    ap.add_argument("--predict", default=None, help="experiment モデルが読む predict.jsonl（既定 out/<日付>/predict.jsonl）")
    ap.add_argument("--out-dir", default=os.environ.get("LT_OUT_DIR") or os.path.join(HERE, "out"))
    ap.add_argument("--state-dir", default=None, help="既定は state/<env>/（cert と prod の台帳を混ぜない）")
    ap.add_argument("--traders-dir", default=os.environ.get("LT_TRADERS_DIR") or os.path.join(HERE, "config", "traders"))
    ap.add_argument("--allow-prod-dry-run", action="store_true")
    ap.add_argument("--i-know-this-is-real-money", action="store_true", help="本番で発注を許す（TT_ALLOW_PROD_ORDERS=1 も要る）")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--retry-interval", type=float, default=60.0, help="Session offline ／ 5xx の再送間隔（秒）")
    ap.add_argument("--auth-retry-wait", type=float, default=30.0, help="認証が 5xx のとき 1 回だけ待って取り直す秒数（401 は再試行しない）")
    ap.add_argument("--cancel-after", type=float, default=600.0, help="未約定を取り消すまでの秒数（既定 10 分 ＝ 16:05）")
    ap.add_argument("--max-day-usd", type=float, default=float(os.environ.get("LT_MAX_DAY_USD") or DEFAULT_MAX_DAY_USD))
    ap.add_argument("--max-total-budget", type=float, default=float(os.environ.get("LT_MAX_TOTAL_BUDGET_USD") or DEFAULT_MAX_TOTAL_BUDGET))
    args = ap.parse_args()

    cfg = load_env(os.environ.get("TT_ENV_FILE") or os.path.join(SAMPLE_DIR, ".env"))
    env = args.env or cfg.get("TT_ENV", "cert")
    allow_prod_orders = args.i_know_this_is_real_money and cfg.get("TT_ALLOW_PROD_ORDERS") == "1"
    is_mock = bool(cfg.get("TT_REST_BASE") or cfg.get("TT_PROD_REST_BASE"))
    sample_out = os.environ.get("TT_OUT_DIR") or os.path.join(SAMPLE_DIR, "out")
    halt_file = os.environ.get("TT_HALT_FILE") or os.path.join(sample_out, "HALT")
    date = args.date or now_et().strftime("%Y-%m-%d")
    state_dir = args.state_dir or os.environ.get("LT_STATE_DIR") or os.path.join(HERE, "state", env)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    traders = load_traders([t.strip() for t in args.traders.split(",") if t.strip()], args.traders_dir)
    total_budget = sum(t.budget_usd for t in traders)
    if total_budget > args.max_total_budget + 1e-9:
        print(f"拒否: 予算の合計 ${total_budget:.2f} が上限 ${args.max_total_budget:.2f} を超える（live-trading.md §0-2）", file=sys.stderr)
        return 2
    if env == "prod" and args.mode == "submit" and not allow_prod_orders:
        print("拒否: 本番の発注には TT_ALLOW_PROD_ORDERS=1 と --i-know-this-is-real-money の両方が要る（取消・dry-run の鍵では開かない）", file=sys.stderr)
        return 2
    if env == "prod" and args.mode == "dry-run" and not (args.allow_prod_dry_run or allow_prod_orders):
        print("拒否: 本番の dry-run には --allow-prod-dry-run が要る", file=sys.stderr)
        return 2

    rec = DayRecorder(args.out_dir, date, env, run_id, is_mock)
    meta = {"kind": "start", "mode": args.mode, "traders": [t.name for t in traders], "test": any(t.test for t in traders),
            "halt_file": halt_file, "now_et": now_et().isoformat(timespec="seconds"), "sdk": record.sdk_versions()}
    print(f"=== {date} {env} mode={args.mode} traders={[t.name for t in traders]} 記録: {rec.dir} ===")

    if args.mode != "plan" and os.path.exists(halt_file):
        rec.write("events", {**meta, "kind": "halted", "note": "HALT があるので発注しない（取消は管理画面の停止ボタンが済ませている）"})
        print(f"拒否: 停止フラグがある（{halt_file}）", file=sys.stderr)
        return 3
    refusal = None if args.mode == "plan" or args.ignore_window else window_refusal(now_et())
    if refusal:
        rec.write("events", {**meta, "kind": "out_of_window", "reason": refusal, "note": "NYSE の営業日の 15:45〜16:05 ET の外では発注しない"})
        print(f"拒否: {refusal}。テストなら --ignore-window", file=sys.stderr)
        return 4
    rec.write("events", meta)

    states = {t.name: load_state(state_dir, t.name) for t in traders}

    # ---- 1. 認証・口座・建玉・残高
    try:
        client = make_client(cfg, env, args.allow_prod_dry_run, allow_prod_orders, rec, args.auth_retry_wait)
    except ApiError as exc:
        rec.write("events", {"kind": "auth_failed", "status": exc.status, "code": exc.code, "note": "認証失敗は再試行しない（IP ブロック）"})
        print(f"中断: 認証失敗 {exc}", file=sys.stderr)
        return 1
    accounts = client.list_accounts()
    account = cfg.get("TT_PROD_ACCOUNT_NUMBER" if env == "prod" else "TT_ACCOUNT_NUMBER") or accounts[0]["account-number"]
    rec.mask.add_account(account)
    balances = client.get_balances(account)
    positions = client.list_positions(account)
    rec.write("balances", {"when": "before", "balances": record.excerpt(balances, limit=40)})
    rec.write("positions", {"when": "before", "positions": [{k: p.get(k) for k in ("symbol", "quantity", "quantity-direction", "average-open-price")} for p in positions]})

    # 気配は本番の資格情報で読む（cert は配信しない）。本番の資格情報が無ければ同じ client（モックはこちら）
    quote_client = client
    if env != "prod" and cfg.get("TT_PROD_CLIENT_SECRET") and cfg.get("TT_PROD_REFRESH_TOKEN"):
        try:
            quote_client = make_client(cfg, "prod", False, False, rec, args.auth_retry_wait)
        except (ApiError, SystemExit) as exc:
            rec.write("events", {"kind": "quote_client_failed", "note": str(exc)[:200]})

    # ---- 2. 合図
    predict_path = args.predict or os.path.join(rec.dir, "predict.jsonl")
    try:
        sigs, sig_events = signalling.collect(traders, date, predict_path)
    except signalling.SignalError as exc:
        rec.write("events", {"kind": "signal_error", "note": str(exc)})
        print(f"中断: {exc}", file=sys.stderr)
        return 1
    for ev in sig_events:
        rec.write("events", ev)
    for sg in sigs:
        rec.write("signals", {"trader": sg.trader, "symbol": sg.symbol, "buy": sg.buy, "exit": sg.exit, "inputs": [list(i) for i in sg.inputs],
                              "position_before": states[sg.trader].position(sg.symbol), "test": sg.test})

    # ---- 3. 状態機械 → 気配 → 株数 → 合算
    raw_by_trader = {}
    need_quotes: set[str] = set()
    for t in traders:
        raw, ev = planning.decide(t, states[t.name], sigs)
        raw_by_trader[t.name] = raw
        need_quotes.update(r["symbol"] for r in raw)
        need_quotes.update(states[t.name].holdings.keys())
        for e in ev:
            rec.write("events", e)
    quotes: dict[str, float] = {}
    quotes_raw: dict[str, dict] = {}
    for sym in sorted(need_quotes):
        try:
            q = quote_client.get_quote(sym)
            bid, ask, last = (float(q.get(k) or 0) for k in ("bid", "ask", "last"))
            mid = (bid + ask) / 2 if bid and ask else (last or bid or ask)
            quotes[sym] = mid
            quotes_raw[sym] = {"bid": bid, "ask": ask, "last": last, "mid": mid, "updated-at": q.get("updated-at"),
                               "server_date": getattr(quote_client, "last_date_header", None), "read_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}
        except ApiError as exc:
            rec.write("events", {"kind": "quote_failed", "symbol": sym, "status": exc.status, "code": exc.code})
    rec.write("quotes", {"quotes": quotes_raw})

    intents = []
    for t in traders:
        its, ev = planning.size_intents(t, states[t.name], raw_by_trader[t.name], quotes, date, args.max_day_usd)
        intents.extend(its)
        for e in ev:
            rec.write("events", e)
    orders, transfers = planning.aggregate(intents, quotes)
    for tr in transfers:
        rec.write("transfers", {"symbol": tr.symbol, "seller": tr.seller, "buyer": tr.buyer, "shares": tr.shares, "price": tr.price, "note": "内部移転。口座には出ない（差 3 のコスト 0）"})
    print(f"合図 {len(sigs)} 本 → 意図 {len(intents)} 件 → 口座への注文 {len(orders)} 件・内部移転 {len(transfers)} 件")

    # ---- 4. 執行
    ex = Executor(client, account, None, halt_file, mode=args.mode, retries=args.retries, retry_interval=args.retry_interval, cancel_after=args.cancel_after)
    results = ex.run_all(orders, quotes_raw)
    fills_by_trader: list[dict] = []
    for res in results:
        o = res.order
        rec.write("orders", {
            "symbol": o.symbol, "side": o.side, "sizing": o.sizing, "shares": o.shares, "value_usd": o.value_usd, "parts": o.parts,
            "external_id": res.external_id, "mode": args.mode, "quote_at_signal": res.quote_at_signal,
            "dry_run": res.dry_run, "submitted": res.submitted, "transitions": res.transitions, "final_status": res.final_status,
            "fills": [f.__dict__ for f in res.fills], "attempts": res.attempts, "cancelled": res.cancelled, "error": res.error,
            "elapsed_ms": res.elapsed_ms, "test": any(t.test for t in traders if t.name in {p['trader'] for p in o.parts}),
        })
        print(f"  {o.side:4s} {o.symbol:6s} {o.sizing:8s} {o.shares or o.value_usd:>10} → {res.final_status} {('' if not res.error else res.error.get('message', ''))[:80]}")
        fills_by_trader.extend(allocate_fills(res))

    # ---- 5. 台帳（submit のときだけ状態を書く）
    if args.mode == "submit":
        for tr in transfers:
            states[tr.seller].apply_sell(tr.symbol, tr.shares, tr.price, date, note="internal")
            states[tr.buyer].apply_buy(tr.symbol, tr.shares, tr.price, date, note="internal")
        for f in fills_by_trader:
            if f["side"] == "buy":
                states[f["trader"]].apply_buy(f["symbol"], f["shares"], f["price"], date)
            else:
                states[f["trader"]].apply_sell(f["symbol"], f["shares"], f["price"], date)
        for t in traders:
            states[t.name].last_date = date
            save_state(state_dir, states[t.name])
        try:
            rec.write("balances", {"when": "after", "balances": record.excerpt(client.get_balances(account), limit=40)})
            after = client.list_positions(account)
            rec.write("positions", {"when": "after", "positions": [{k: p.get(k) for k in ("symbol", "quantity", "quantity-direction", "average-open-price")} for p in after]})
        except ApiError as exc:
            rec.write("events", {"kind": "after_read_failed", "status": exc.status, "code": exc.code})
    for t in traders:
        rec.write("ledger", ledger.daily_row(t, states[t.name], quotes, date))

    bad = [r for r in results if r.final_status in ("error", "guarded", "halted", "not_submitted")]
    rec.write("events", {"kind": "end", "orders": len(results), "bad": len(bad), "fills": len(fills_by_trader)})
    print(f"完了。注文 {len(results)} 件・約定 {len(fills_by_trader)} 件・問題 {len(bad)} 件。記録: {rec.dir}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
