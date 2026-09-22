#!/usr/bin/env python3
"""シミュレーションの運転手: 仮の時計を進め、営業日の発注できる時間帯に入ったら、その日の気配をモックに流して執行器（run_day.py）を起こす。

    python simctl.py mode sim sim1          # 先に機械をシミュレーションモードへ（何も動いていないとき・人が行う）
    python simrun.py sim1                   # 続きから（無ければ最初から）。速さ・停止・再開は別の端末から simctl.py で
    python simrun.py sim1 --fresh --speed max --days 20

⚠ **実売買とは排他**（live-trading.md §0-7 (a)）: MODE が sim でその名前のときだけ動き、`run.lock` を終わるまで持つ。
⚠ 接続先はこの運転手が起こすループバックのモックだけ。資格情報の `.env` は読まない（`TT_ENV_FILE` を空のファイルに向ける）。
⚠ 売買のコードは本物と同じ `run_day.py`。再送 60 秒 × 3・取消 600 秒は縮めず、仮の時計の上でそのまま待つ。
⚠ 記録は `sim/<名前>/` の下だけ（全行 sim: true ／ mock: true ／ test: true）。仮データの損益・差は判定に混ぜない。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
sys.path.insert(0, HERE)

import mode as modes  # noqa: E402
import simclock  # noqa: E402
import simdata  # noqa: E402
from _livefs import livefs  # noqa: E402

ET = ZoneInfo("America/New_York")
UA = "simrun/1.0"
MOCK_FAULTS = ("session_offline", "http_5xx", "http_429", "auth_5xx", "auth_401", "reject_funds", "no_fill")   # モックが起こすもの
DRIVER_EVENTS = ("skip_day", "crash_mid", "drawdown", "position_loss")                                                           # 運転手が起こすもの（halt は人が管理画面で）
CRED_KEYS = ("TT_CLIENT_SECRET", "TT_REFRESH_TOKEN", "TT_PROD_CLIENT_SECRET", "TT_PROD_REFRESH_TOKEN")


def window_times(day: date, windows: list[str]) -> list[datetime]:
    out = []
    for w in windows:
        hh, mm, *ss = (int(x) for x in w.split(":"))
        out.append(datetime(day.year, day.month, day.day, hh, mm, ss[0] if ss else 0, tzinfo=ET))
    return sorted(out)


def credentials_present(path: str | None = None) -> list[str]:
    """資格情報の `.env` に入っている鍵の名前（値は読まない）。⚠ シミュレーション専用の機械（Sx360）には置かない決まり（§0-7 (f)）。"""
    path = path or os.path.join(SAMPLE_DIR, ".env")
    found = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                if key in CRED_KEYS and value.strip():
                    found.append(key)
    return found


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Driver:
    def __init__(self, name: str, cfg: simdata.SimConfig, root: str, port: int | None = None, real_sleep=time.sleep):
        self.name, self.cfg, self.root = name, cfg, root
        self.control = modes.control_file(name)
        self.status_path = os.path.join(root, "sim", "status.json")
        self.port = port or free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.real_sleep = real_sleep
        self.mock: subprocess.Popen | None = None
        self.status: dict = {}

    # ---------- 状態（管理画面が読む）

    def write_status(self, **change) -> None:
        self.status.update(change)
        ctl = simclock.read_control(self.control)
        self.status.update(name=self.name, sim=True, pid=os.getpid(), speed=ctl["speed"], paused=ctl["paused"],
                           sim_now=datetime.fromtimestamp(simclock.sim_now(ctl, time.time()), ET).isoformat(timespec="seconds"),
                           updated_at=time.time())
        tmp = self.status_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.status, f, ensure_ascii=False)
        os.replace(tmp, self.status_path)

    # ---------- モック

    def start_mock(self) -> None:
        log = open(os.path.join(self.root, "sim", "mock.log"), "a")
        self.mock = subprocess.Popen(
            [sys.executable, os.path.join(SAMPLE_DIR, "mock_server.py"), "--port", str(self.port), "--ws-port", str(free_port()), "--dxlink-port", str(free_port()),
             "--market-data", "--fill-noise", str(self.cfg.fill_noise), "--seed", str(self.cfg.seed), "--sim-control", self.control],
            stdout=log, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                self.post("/_mock/quote", {})
                return
            except OSError:
                if self.mock.poll() is not None:
                    break
                time.sleep(0.1)
        raise SystemExit(f"エラー: モックサーバが起きない（{os.path.join(self.root, 'sim', 'mock.log')}）")

    def post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"User-Agent": UA, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read() or b"{}")

    def stop_mock(self) -> None:
        if self.mock and self.mock.poll() is None:
            self.mock.terminate()
            try:
                self.mock.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.mock.kill()

    # ---------- 時間

    def wait_until(self, target: datetime) -> bool:
        """仮の時計が target になるまで待つ。stop が来たら False。止まっている間は進まない。発注できる時間帯の外は既定で飛ぶ。"""
        beat = 0.0
        while True:
            ctl = simclock.read_control(self.control)
            if ctl.get("stop"):
                return False
            if time.time() - beat >= 1.0:
                beat = time.time()
                self.write_status(state="停止中" if ctl["paused"] else "発注できる時間帯の外")
            remain = target.timestamp() - simclock.sim_now(ctl, time.time())
            if ctl["paused"]:
                self.real_sleep(0.1)
            elif remain <= 0:
                return True
            elif self.cfg.skip_outside_window or ctl["speed"] == "max":
                simclock.update_control(self.control, lambda c: c.update(sim_epoch=max(c["sim_epoch"], target.timestamp())) if not c["paused"] else None)
            else:
                self.real_sleep(min(remain / ctl["speed"], 0.2))

    # ---------- 1 回の発注できる時間帯

    # ---------- 筋書き（故障の注入。live-trading.md §0-7 (e)）

    def events_on(self, day_index: int) -> list[dict]:
        return [e for e in self.cfg.events if int(e.get("day", 0)) == day_index]

    def quote_factor(self, day_index: int) -> float:
        """drawdown: その日から days 日つづけて pct_per_day ずつ気配を下げ、その後は下がった水準のまま（戻さない）。"""
        factor = 1.0
        for e in self.cfg.events:
            if e.get("kind") == "drawdown" and day_index >= int(e["day"]):
                n = min(day_index - int(e["day"]) + 1, int(e.get("days", 10)))
                factor *= (1.0 + float(e.get("pct_per_day", -3.0)) / 100.0) ** n
        return factor

    def run_window(self, day: str, quotes: dict[str, float], extra_args: list[str], crash: bool = False) -> int:
        self.post("/_mock/quote", {"quotes": quotes, "spread_bp": self.cfg.spread_bp})
        env = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}     # ⚠ 本番の鍵も資格情報も子へ渡さない
        if crash:
            env["LT_SIM_CRASH"] = "after_submit"
        env.update({"TT_ENV_FILE": os.path.join(self.root, "sim", "env.empty"), "TT_REST_BASE": self.base,
                    "TT_CLIENT_SECRET": "MOCK-SECRET", "TT_REFRESH_TOKEN": "MOCK-REFRESH"})
        if modes.overridden():
            env["LT_MODE_DIR"] = modes.base_dir()
        log_path = os.path.join(self.root, "sim", "logs", f"{day}.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log:
            proc = subprocess.Popen([sys.executable, os.path.join(HERE, "run_day.py"), "--traders", ",".join(self.cfg.traders), "--mode", "submit", "--sim-clock", *extra_args],
                                    stdout=log, stderr=subprocess.STDOUT, env=env)
            beat = 0.0
            while proc.poll() is None:
                if time.time() - beat >= 1.0:
                    beat = time.time()
                    self.write_status(state="停止中" if simclock.read_control(self.control)["paused"] else "発注できる時間帯の中")
                self.real_sleep(0.05)
        return proc.returncode

    # ---------- 通し

    def run(self, max_days: int | None = None, extra_args: list[str] | None = None) -> int:
        with open(os.path.join(self.root, "sim", "data.json"), encoding="utf-8") as f:
            data = json.load(f)
        days = data["days"]
        done = 0
        rcs: dict[str, int] = dict(self.status.get("rcs") or {})
        self.start_mock()
        try:
            # モックの口座は起こすたびに空 → 台帳の合計を建玉として置く（続きから起こしたとき「口座が台帳より少ない」で全部止まらないように）
            import recovery
            held: dict[str, float] = {}
            for st in recovery.load_all_states(os.path.join(self.root, "state", "cert")).values():
                for sym, h in st.holdings.items():
                    held[sym] = round(held.get(sym, 0.0) + h.shares, 6)
            self.post("/_mock/positions", {"positions": held})
            for i, day in enumerate(days):
                for target in window_times(date.fromisoformat(day), self.cfg.windows):
                    key = f"{target:%Y-%m-%d %H:%M}"
                    now = simclock.sim_now(simclock.read_control(self.control), time.time())
                    if key in rcs or target.timestamp() < now - 60:   # 続きから: もう流した発注できる時間帯・過ぎた発注できる時間帯は流さない
                        continue
                    if max_days is not None and done >= max_days:
                        self.write_status(state="終了", note=f"--days {max_days} に達した（続きは simrun.py {self.name}）")
                        return 0
                    self.write_status(day_index=i + 1, days_total=len(days), sim_date=day, source_date=data["source"][day])
                    if not self.wait_until(target):
                        self.write_status(state="終了", note="stop")
                        return 0
                    self.write_status(state="発注できる時間帯の中")
                    todays = self.events_on(i + 1)
                    faults = {e["kind"]: int(e.get("times", 1)) for e in todays if e["kind"] in MOCK_FAULTS}
                    if faults:
                        self.post("/_mock/fault", {"faults": faults})
                    for e in todays:
                        if e["kind"] == "position_loss":       # 台帳にあるはずの株が口座から消える（人が口座を直接触った・sandbox のリセットの代役）
                            self.post("/_mock/positions", {"adjust": {e["symbol"]: -abs(float(e.get("shares", 1)))}})
                    factor = self.quote_factor(i + 1)
                    quotes = {s: round(q * factor, 4) for s, q in data["quotes"][day].items()}
                    if any(e["kind"] == "skip_day" for e in todays):
                        rc = "skipped"                          # 起動しない日（timer が動かなかった日の代役）。記録のディレクトリもできない
                    else:
                        rc = self.run_window(day, quotes, extra_args or [], crash=any(e["kind"] == "crash_mid" for e in todays))
                    if todays:
                        livefs.append(os.path.join(self.root, "sim", "scenario.jsonl"),
                                      json.dumps({"sim": True, "day_index": i + 1, "date": day, "events": todays, "quote_factor": round(factor, 6), "rc": rc}, ensure_ascii=False))
                    rcs[key] = rc
                    done += 1
                    print(f"  {day}（出どころ {data['source'][day]}）{i + 1:>3}/{len(days)}  run_day rc={rc}", flush=True)

                    def after_day(c):
                        if c.get("step", 0) > 0:                # step: 1 営業日進めて止まる
                            c["step"] -= 1
                            if c["step"] == 0:
                                c["paused"] = True
                    simclock.update_control(self.control, after_day)
                    self.write_status(state="発注できる時間帯の外", last_rc=rc, rcs=rcs)
            self.write_status(state="終了", note="期間の最終日まで流した")
            return 0
        finally:
            self.stop_mock()
            # ⚠ 運転手がいない間に仮の時計だけが進むと、次に起こしたとき発注できる時間帯を通り過ぎている。終わるときは必ず止める
            simclock.pause(self.control)


def main() -> int:
    ap = argparse.ArgumentParser(description="シミュレーションの運転手（仮データと仮の時計で執行器を通しで動かす）")
    ap.add_argument("name", help="シミュレーションの名前（config/sim/<名前>.toml ／ 記録は sim/<名前>/）")
    ap.add_argument("--fresh", action="store_true", help="記録の木を消して最初から（⚠ 消すのは sim/<名前>/ だけ）")
    ap.add_argument("--speed", default=None, help="速さの初期値（1 ／ 10 ／ 60 ／ 300 ／ 1440 ／ max。既定は設定の値。続きからのときは今の速さ）")
    ap.add_argument("--paused", action="store_true", help="止めた状態で始める（simctl.py resume ／ step で動かす）")
    ap.add_argument("--days", type=int, default=None, help="この回に流す発注できる時間帯の数（既定は最終日まで）")
    ap.add_argument("--port", type=int, default=None, help="モックサーバのポート（既定は空いているもの）")
    ap.add_argument("--traders-dir", default=os.path.join(HERE, "config", "traders"))
    args = ap.parse_args()

    try:
        machine = modes.read_mode()
    except modes.ModeError as exc:
        print(f"拒否: {exc}", file=sys.stderr)
        return 5
    print(modes.banner(machine))
    if not machine.is_sim or machine.name != args.name:
        print(f"拒否: 機械のモードが {machine.mode}{f'（{machine.name}）' if machine.name else ''}。シミュレーション {args.name} を回すには、"
              f"何も動いていないときに simctl.py mode sim {args.name}", file=sys.stderr)
        return 5
    lock = modes.RunLock()
    if not lock.acquire("sim", f"simrun {args.name}"):
        print(f"拒否: 別の執行器 ／ 運転手が動いている（run.lock の持ち主 {lock.holder()}）", file=sys.stderr)
        return 6
    try:
        creds = credentials_present()
        if creds:
            print(f"⚠ 警告: シミュレーションモードなのに資格情報の .env が見つかった（{', '.join(creds)}）。運転手は読まないが、"
                  "シミュレーション専用の機械（Sx360）には置かない決まり（live-trading.md §0-7 (f)）", file=sys.stderr)
        cfg = simdata.load_config(args.name)
        unknown = [e for e in cfg.events if e.get("kind") not in MOCK_FAULTS + DRIVER_EVENTS or not int(e.get("day", 0)) >= 1]
        if unknown:
            print(f"拒否: 筋書きが読めない（kind は {MOCK_FAULTS + DRIVER_EVENTS}・day は 1 以上）: {unknown}", file=sys.stderr)
            return 2
        root = modes.sim_root(args.name)
        if args.fresh and os.path.isdir(root):
            livefs.forget()                            # 木の sim.sqlite を消す前に、このプロセスの接続を閉じる
            shutil.rmtree(root)
        control = modes.control_file(args.name)
        resumed = os.path.exists(control)
        if not resumed:
            data = simdata.write_tree(root, cfg, args.traders_dir)
            # `kind = "experiment"` のトレーダーがいるときだけ: 予測の作り置きを仮の日付に書き換えて木に置く（§0-7 (k)）。いなければ何もしない
            import simpredict
            try:
                placed = simpredict.install(root, cfg, args.traders_dir, data)
            except simpredict.SimPredictError as exc:
                print(f"拒否: {exc}", file=sys.stderr)
                return 2
            if placed:
                print(f"予測を置いた: {placed['days']} 日・{placed['rows']} 行（終値と気配の食い違い {placed['close_mismatch']} 行）", flush=True)
            first = window_times(simdata.sim_days(cfg)[0], cfg.windows)[0]
            simclock.init_control(control, first.replace(hour=9, minute=30, second=0), speed=args.speed or cfg.speed, paused=args.paused)
        else:
            simclock.update_control(control, lambda c: c.update(stop=False, step=0, paused=args.paused,
                                                                 **({"speed": simclock.parse_speed(args.speed)} if args.speed else {})))
        open(os.path.join(root, "sim", "env.empty"), "w").close()
        driver = Driver(args.name, cfg, root, port=args.port)
        if resumed and os.path.exists(driver.status_path):
            with open(driver.status_path, encoding="utf-8") as f:
                driver.status = {k: v for k, v in json.load(f).items() if k in ("rcs",)}
        print(f"{'続きから' if resumed else '最初から'}: {root}  モック {driver.base}  操作は別の端末から simctl.py（speed ／ pause ／ resume ／ step ／ stop ／ status）", flush=True)
        return driver.run(max_days=args.days)
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
