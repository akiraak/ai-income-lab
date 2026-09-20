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
⚠ **実売買とシミュレーションは排他**（live-trading.md §0-7 (a)）: 起動時に `MODE` を見て `run.lock` を取る。`MODE` が無ければ real ＝ 今までどおり。
   `--sim-clock`（仮の時計）は MODE が sim ＆ 接続先がループバックのモック ＆ prod でない ＆ 本番の鍵が無いときだけ受け付ける。
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
sys.path.insert(0, HERE)
sys.path.insert(0, SAMPLE_DIR)

import market_calendar  # noqa: E402
import record  # noqa: E402
from ttclient import ApiError, Client, ProductionGuard, load_env  # noqa: E402

import ledger  # noqa: E402
import mode as modes  # noqa: E402
import plan as planning  # noqa: E402
import recovery  # noqa: E402
import simclock  # noqa: E402
from journal import Journal  # noqa: E402
import signals as signalling  # noqa: E402
from execute import Executor, allocate_fills  # noqa: E402
from state import load_state, save_state  # noqa: E402
from trader import load_traders  # noqa: E402

ET = ZoneInfo("America/New_York")
WINDOW_START = (15, 45)
WINDOW_END = (16, 5)
# live-trading.md §0-2。予算は 2 つの規模を並べて持つ（2026-09-19 の利用者決定）: 規模 A ＝ $1,000（実際の入金額）／ 規模 B ＝ $10,000。
# ⚠ 既定は規模 A。⚠ **規模 B は実際の取引で使えない可能性がある**（口座にその額が入っていない）ので、使うときは
#   `--max-total-budget 10000 --max-day-usd 10000`（または LT_MAX_TOTAL_BUDGET_USD / LT_MAX_DAY_USD）を明示する。
DEFAULT_MAX_TOTAL_BUDGET = 1000.0   # 全トレーダーの予算の合計の上限（口座の残高）
DEFAULT_MAX_DAY_USD = 1000.0        # 1 日の買いの合計の上限
DRAWDOWN_WARN_PCT = 20.0            # 含み損（実現 ＋ 含みの損）が予算のこの % 以上で警告（§0-2。⚠ 警告だけ）


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


# 執行器の「いま」と待ちはここ 1 か所から引く。⚠ 既定は本物の時計。仮の時計に替えるのは main() の sim_clock_refusal() を通ったときだけ
CLOCK = simclock.RealClock()


def now_et() -> datetime:
    return CLOCK.now().astimezone(ET)


def is_loopback(url: str | None) -> bool:
    return (urlparse(url or "").hostname or "") in ("127.0.0.1", "localhost", "::1")


def sim_clock_refusal(machine: modes.Mode, cfg: dict, env: str, args) -> str | None:
    """仮の時計を受け付けない理由（受け付けるなら None）。live-trading.md §0-7 (a)。⚠ 1 つでも外れたら起動しない。"""
    if not machine.is_sim:
        return "MODE が real（実売買モードでは仮の時計を使えない。切り替えは simctl.py mode sim <名前>）"
    if env == "prod":
        return "--env prod に仮の時計は渡せない"
    if args.allow_prod_dry_run or args.i_know_this_is_real_money or any(cfg.get(k) for k in ("TT_ALLOW_PROD_ORDERS", "TT_ALLOW_PROD_DRY_RUN")):
        return "本番の鍵（TT_ALLOW_PROD_* ／ --allow-prod-dry-run ／ --i-know-this-is-real-money）が付いている"
    if not is_loopback(cfg.get("TT_REST_BASE")):
        return f"接続先がループバックのモックではない（TT_REST_BASE={cfg.get('TT_REST_BASE') or '無し'}）"
    return None


class DayRecorder:
    """`out/<日付>/<種類>.jsonl` に 1 行ずつ。全行が Masker を通る。"""

    def __init__(self, out_dir: str, date: str, env: str, run_id: str, mock: bool, sim: bool = False):
        self.dir = os.path.join(out_dir, date)
        os.makedirs(self.dir, exist_ok=True)
        self.date, self.env, self.run_id, self.mock, self.sim = date, env, run_id, mock, sim
        self.mask = record.Masker()

    def write(self, kind: str, row: dict) -> None:
        row = {"date": self.date, "env": self.env, "run_id": self.run_id, **row}
        if self.sim:
            row.update(sim=True, test=True)   # シミュレーションの全行に sim・mock・test（§0-7 (a)）
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
                CLOCK.sleep(auth_retry_wait)
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
    ap.add_argument("--serial", action="store_true", help="発注を 1 本ずつ約定まで待つ元の形に戻す（既定は 2 段 ＝ 先に全部出してから約定を確かめる）")
    ap.add_argument("--max-day-usd", type=float, default=float(os.environ.get("LT_MAX_DAY_USD") or DEFAULT_MAX_DAY_USD))
    ap.add_argument("--max-total-budget", type=float, default=float(os.environ.get("LT_MAX_TOTAL_BUDGET_USD") or DEFAULT_MAX_TOTAL_BUDGET))
    ap.add_argument("--sim-clock", action="store_true", default=os.environ.get("LT_SIM_CLOCK") == "1",
                    help="仮の時計で動かす（シミュレーション。時計と記録の置き場は MODE の名前から決まる。⚠ モック以外では起動を拒否）")
    args = ap.parse_args()

    cfg = load_env(os.environ.get("TT_ENV_FILE") or os.path.join(SAMPLE_DIR, ".env"))
    env = args.env or cfg.get("TT_ENV", "cert")
    allow_prod_orders = args.i_know_this_is_real_money and cfg.get("TT_ALLOW_PROD_ORDERS") == "1"
    is_mock = bool(cfg.get("TT_REST_BASE") or cfg.get("TT_PROD_REST_BASE"))

    # ---- 0. モード（実売買 ／ シミュレーション。排他）。⚠ 仮の時計はこの検査を通ったときだけ入る
    global CLOCK
    try:
        machine = modes.read_mode()
    except modes.ModeError as exc:
        print(f"拒否: {exc}", file=sys.stderr)
        return 5
    sim = bool(args.sim_clock)
    if sim:
        refusal = sim_clock_refusal(machine, cfg, env, args)
        if refusal:
            print(f"拒否: 仮の時計は使えない — {refusal}", file=sys.stderr)
            return 2
        root = modes.sim_root(machine.name)
        CLOCK = simclock.SimClock(modes.control_file(machine.name))
        # ⚠ シミュレーションの置き場は MODE の名前から決まる（本物の out/・state/・HALT に 1 バイトも書かない）
        args.out_dir = os.environ.get("LT_OUT_DIR") or os.path.join(root, "out")
        args.traders_dir = os.environ.get("LT_TRADERS_DIR") or os.path.join(root, "config", "traders")
        state_dir = args.state_dir or os.environ.get("LT_STATE_DIR") or os.path.join(root, "state", env)
        halt_file = os.environ.get("TT_HALT_FILE") or os.path.join(root, "HALT")
        outside = [p for p in (args.out_dir, args.traders_dir, state_dir, halt_file, args.predict) if p and not modes.inside(p, root)]
        if outside:
            print(f"拒否: シミュレーションの置き場（{root}）の外を指している: {outside}", file=sys.stderr)
            return 2
    else:
        sample_out = os.environ.get("TT_OUT_DIR") or os.path.join(SAMPLE_DIR, "out")
        halt_file = os.environ.get("TT_HALT_FILE") or os.path.join(sample_out, "HALT")
        state_dir = args.state_dir or os.environ.get("LT_STATE_DIR") or os.path.join(HERE, "state", env)
        in_sim_tree = [p for p in (args.out_dir, state_dir, halt_file) if modes.inside(p, modes.sim_base())]
        if in_sim_tree:
            print(f"拒否: 本物の時計の実行がシミュレーションの置き場を指している: {in_sim_tree}", file=sys.stderr)
            return 2
        if modes.overridden() and any(modes.inside(p, os.path.join(HERE, d)) for p, d in ((args.out_dir, "out"), (state_dir, "state"))):
            # テスト用の MODE で動いた結果（拒否の記録も含む）を、本物の記録に混ぜない
            print("拒否: LT_MODE_DIR（テスト用の MODE の置き場）を使うときは、LT_OUT_DIR ／ LT_STATE_DIR も本物の out/・state/ の外へ向ける", file=sys.stderr)
            return 2
    print(modes.banner(machine, CLOCK.describe() if sim else ""))
    date = args.date or now_et().strftime("%Y-%m-%d")
    run_id = CLOCK.now().strftime("%Y%m%dT%H%M%SZ")

    names = [t.strip() for t in args.traders.split(",") if t.strip()]
    misnamed = [n for n in names if n.startswith(modes.SIM_TRADER_PREFIX) != sim]
    if misnamed:
        print(f"拒否: {misnamed} — シミュレーションのトレーダーは名前が {modes.SIM_TRADER_PREFIX} で始まり、本物はそうでないこと", file=sys.stderr)
        return 2
    traders = load_traders(names, args.traders_dir)
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

    rec = DayRecorder(args.out_dir, date, env, run_id, is_mock, sim=sim)
    meta = {"kind": "start", "mode": args.mode, "traders": [t.name for t in traders], "test": any(t.test for t in traders),
            "halt_file": halt_file, "now_et": now_et().isoformat(timespec="seconds"), "sdk": record.sdk_versions()}
    if sim:
        meta["sim_name"], meta["sim_speed"] = machine.name, CLOCK.control()["speed"]
    if machine.is_sim and not sim:
        # ⚠ 本番の鍵と確認の引数があっても起動しない。本物の記録に残す ＝「起動しなかった日」の理由が後から分かる
        rec.write("events", {**meta, "kind": "refused_mode_sim", "sim_name": machine.name, "since": machine.since,
                             "note": "機械がシミュレーションモードなので本物の執行器は起動しない（戻すのは simctl.py mode real）"})
        print(f"拒否: 機械がシミュレーションモード（{machine.name}・{machine.since} から）。本物の執行器は起動しない。戻すのは simctl.py mode real", file=sys.stderr)
        return 5
    if modes.overridden() and not is_loopback(cfg.get("TT_PROD_REST_BASE" if env == "prod" else "TT_REST_BASE")) and args.mode == "submit":
        print("拒否: LT_MODE_DIR（テスト用の MODE ／ run.lock の置き場）は、モック以外への submit では使えない", file=sys.stderr)
        return 2
    lock = modes.RunLock()
    if lock.acquire(machine.mode, f"run_day {env} {args.mode}"):
        atexit.register(lock.release)
    elif not (sim and lock.held_by_parent_sim()):
        # 運転手（simrun）が鍵を持ったまま起こした執行器だけは通す。それ以外の二重起動は拒否
        rec.write("events", {**meta, "kind": "refused_lock_busy", "holder": lock.holder()})
        print(f"拒否: 別の執行器 ／ 運転手が動いている（run.lock の持ち主 {lock.holder()}）", file=sys.stderr)
        return 6
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

    # ---- 1-2. 口座の建玉と台帳の帳尻（live-trading.md §0-8）: 控えの未完を戻す → 突き合わせる。⚠ 売買の前に済ませる
    journal = Journal(state_dir, date, now=CLOCK.now)
    rec_events, blocked = recovery.recover_unfinished(journal, client, account, state_dir, date, write=args.mode == "submit")
    for ev in rec_events:
        rec.write("events", ev)
        print(f"⚠ 控えの未完: {ev['trader']} {ev['side']} {ev['symbol']} → {ev.get('outcome') or ev.get('reason')}", file=sys.stderr)
    if any(ev["kind"] == "journal_recovered" and ev.get("shares") for ev in rec_events):
        positions = client.list_positions(account)
    states = {t.name: load_state(state_dir, t.name) for t in traders}       # ⚠ 戻した約定が入った後の台帳を読み直す
    all_states = {**recovery.load_all_states(state_dir), **states}          # その回に動かさない人の持ち分も口座には入っている
    outside, short = recovery.check_positions(positions, all_states, {sym for t in traders for sym in t.symbols})
    if outside:
        rec.write("events", {"kind": "positions_outside_ledger", "symbols": outside,
                             "note": "口座のほうが多い ＝ 台帳の外の株（利用者の手持ちなど）。トレーダーは自分の台帳の株しか売らないので売買は続ける"})
    for sym, row in short.items():
        rec.write("events", {"kind": "position_short", "symbol": sym, **row,
                             "note": "台帳にあるはずの株が口座に無い ＝ この銘柄は今日売買しない。reconcile.py で台帳を口座に合わせる"})
        print(f"⚠ 口座の建玉が台帳より少ない: {sym} 口座 {row['account']} ／ 台帳 {row['ledger']}。今日は売買しない（reconcile.py）", file=sys.stderr)
    blocked |= set(short)

    # 気配は本番の資格情報で読む（cert は配信しない）。本番の資格情報が無ければ同じ client（モックはこちら）
    quote_client = client
    if not sim and env != "prod" and cfg.get("TT_PROD_CLIENT_SECRET") and cfg.get("TT_PROD_REFRESH_TOKEN"):
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
        for r in [r for r in raw if r["symbol"] in blocked]:
            rec.write("events", {"kind": "blocked_symbol", "trader": t.name, "symbol": r["symbol"], "side": r["side"],
                                 "note": "口座と台帳の帳尻が合っていない銘柄（position_short ／ journal_unresolved）。今日は発注しない"})
        raw = [r for r in raw if r["symbol"] not in blocked]
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
                               "server_date": getattr(quote_client, "last_date_header", None), "read_at": CLOCK.now().isoformat(timespec="milliseconds")}
        except ApiError as exc:
            rec.write("events", {"kind": "quote_failed", "symbol": sym, "status": exc.status, "code": exc.code})
    rec.write("quotes", {"quotes": quotes_raw})

    intents = []
    for t in traders:
        its, ev = planning.size_intents(t, states[t.name], raw_by_trader[t.name], quotes, date, args.max_day_usd)
        intents.extend(its)
        for e in ev:
            rec.write("events", e)
    orders = planning.to_orders(intents, quotes)   # 1 意図 1 注文（合算しない・内部移転しない。売りが先）
    print(f"合図 {len(sigs)} 本 → 意図 {len(intents)} 件 → 口座への注文 {len(orders)} 件")

    # ---- 4. 執行と台帳（submit のときだけ状態を書く）
    # ⚠ 台帳の保存は 1 注文ごと: 発注の直前に控え（journal）→ 約定 → その人の台帳を保存 → 控えを閉じる。
    #    途中で落ちても、失うのは高々 1 注文で、それも次の起動で控えから戻る（§0-8 の段 1）
    ledger_errors = 0
    fills_by_trader: list[dict] = []
    def settle(res) -> None:
        nonlocal ledger_errors
        if sim and os.environ.get("LT_SIM_CRASH") == "after_submit":
            # 筋書き crash_mid（シミュレーションだけ）: 発注の後・記録と台帳の保存の前に落ちる。⚠ 仮の時計の検査を通った実行でしか効かない
            os._exit(137)
        o = res.order
        rec.write("orders", {
            "symbol": o.symbol, "side": o.side, "sizing": o.sizing, "shares": o.shares, "value_usd": o.value_usd, "parts": o.parts,
            "external_id": res.external_id, "mode": args.mode, "quote_at_signal": res.quote_at_signal,
            "dry_run": res.dry_run, "submitted": res.submitted, "transitions": res.transitions, "final_status": res.final_status,
            "fills": [f.__dict__ for f in res.fills], "amounts": res.amounts(), "attempts": res.attempts, "cancelled": res.cancelled, "error": res.error,
            "elapsed_ms": res.elapsed_ms, "test": any(t.test for t in traders if t.name in {p['trader'] for p in o.parts}),
        })
        print(f"  {o.side:4s} {o.symbol:6s} {o.sizing:8s} {o.shares or o.value_usd:>10} → {res.final_status} {('' if not res.error else res.error.get('message', ''))[:80]}")
        fills = allocate_fills(res)
        fills_by_trader.extend(fills)
        if args.mode != "submit":
            return
        for f in fills:
            # ⚠ 1 件の食い違いで落ちない: ここで落ちると、約定済みのほかの売買まで状態に残らない
            try:
                if f["side"] == "buy":
                    states[f["trader"]].apply_buy(f["symbol"], f["shares"], f["price"], date, fee=f.get("fee", 0.0))
                else:
                    states[f["trader"]].apply_sell(f["symbol"], f["shares"], f["price"], date, fee=f.get("fee", 0.0))
            except ValueError as exc:
                ledger_errors += 1
                rec.write("events", {"kind": "ledger_error", "fill": f, "note": str(exc)[:300]})
                print(f"⚠ 台帳に入れられない約定: {exc}", file=sys.stderr)
            save_state(state_dir, states[f["trader"]])
        journal.done(res.external_id, str(res.final_status), shares=sum(f["shares"] for f in fills))

    ex = Executor(client, account, None, halt_file, mode=args.mode, retries=args.retries, retry_interval=args.retry_interval, cancel_after=args.cancel_after,
                  sleep=CLOCK.sleep, clock=CLOCK if sim else None, journal=journal, pipeline=not args.serial)
    results = ex.run_all(orders, quotes_raw, on_result=settle)

    if args.mode == "submit":
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
        row = ledger.daily_row(t, states[t.name], quotes, date)
        rec.write("ledger", row)
        # 含み損の警告（§0-2。2026-09-19 の利用者決定「警告だけ・止めるのは人」）。⚠ 執行器は売買を止めない。止めるのは利用者（停止ボタン ＝ HALT）
        dd = row.get("drawdown_pct_of_budget")
        if dd is not None and dd >= DRAWDOWN_WARN_PCT:
            rec.write("events", {"kind": "drawdown_warning", "trader": t.name, "drawdown_pct_of_budget": dd, "threshold_pct": DRAWDOWN_WARN_PCT,
                                 "note": "含み損が予算の 20% 以上。執行器は止めない（投げ売りもしない）。止めるなら停止ボタン（HALT）"})
            print(f"⚠ 含み損の警告: {t.name} が予算の {dd}%（線 {DRAWDOWN_WARN_PCT}%）。執行器は止めない ＝ 止めるなら停止ボタン（HALT）", file=sys.stderr)

    bad = [r for r in results if r.final_status in ("error", "guarded", "halted", "not_submitted")]
    rec.write("events", {"kind": "end", "now_et": now_et().isoformat(timespec="seconds"), "orders": len(results), "bad": len(bad), "fills": len(fills_by_trader), **({"ledger_errors": ledger_errors} if ledger_errors else {}),
                         **({"blocked_symbols": sorted(blocked)} if blocked else {})})
    print(f"完了。注文 {len(results)} 件・約定 {len(fills_by_trader)} 件・問題 {len(bad) + ledger_errors} 件。記録: {rec.dir}")
    return 1 if bad or ledger_errors or blocked else 0


if __name__ == "__main__":
    sys.exit(main())
