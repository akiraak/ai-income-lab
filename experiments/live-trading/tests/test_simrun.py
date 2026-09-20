"""仮データ（日足の再生・合図）と、運転手 simrun.py ／ 操作の口 simctl.py（ループバックのモックだけ。資格情報なし）。

⚠ 日足（`feature-discovery/data/`）は git 管理外なので、テストは作り物の日足を `LT_SIM_DATA_DIR` に置く。MODE ／ run.lock ／ 記録は tmp。
"""
import glob
import json
import math
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import mode as modes
import simclock
import simdata
import simrun

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")
SYMBOLS = {"T": 25, "VZ": 48, "BAC": 52, "PFE": 26, "KO": 79, "XLF": 51, "XLE": 57, "XLU": 43, "NVDA": 224, "SPY": 758, "INTC": 109}


def make_bars(data_dir, first=date(2026, 4, 1), last=date(2026, 9, 10), symbols=SYMBOLS):
    os.makedirs(data_dir, exist_ok=True)
    for j, (sym, level) in enumerate(symbols.items()):
        with open(os.path.join(data_dir, f"{sym}.csv"), "w") as f:
            f.write("time_ms,open,high,low,close,volume\n")
            d, k = first, 0
            while d <= last:
                if d.weekday() < 5:
                    close = level * (1 + 0.04 * math.sin(k / (3 + j % 4)) + 0.0005 * k)
                    ms = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
                    f.write(f"{ms},{close},{close},{close},{close:.4f},1000\n")
                    k += 1
                d += timedelta(days=1)
    return str(data_dir)


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path / "lt"
    base.mkdir()
    data_dir = make_bars(tmp_path / "bars")
    monkeypatch.setenv("LT_MODE_DIR", str(base))
    child = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}     # 資格情報が 1 つも無い環境（Sx360 と同じ条件）
    child.update(LT_MODE_DIR=str(base), LT_SIM_DATA_DIR=data_dir)
    return base, data_dir, child


def cli(script, args, child, **kw):
    return subprocess.run([sys.executable, os.path.join(HERE, script), *args], capture_output=True, text=True, env=child, timeout=120, **kw)


def rows(root, kind):
    return [json.loads(line) for f in sorted(glob.glob(os.path.join(root, "out", "*", f"{kind}.jsonl"))) for line in open(f, encoding="utf-8")]


def strip_time(row):
    drop = {"run_id", "now_et", "elapsed_ms", "transitions", "read_at", "updated-at", "server_date", "filled_at", "sim_speed", "sdk"}
    if isinstance(row, dict):
        return {k: strip_time(v) for k, v in row.items() if k not in drop}
    return [strip_time(v) for v in row] if isinstance(row, list) else row


# ---------- 仮データ

def test_replay_maps_kth_sim_day_to_kth_source_bar(env):
    _, data_dir, _ = env
    cfg = simdata.load_config("sim1")
    data = simdata.build(cfg, ["T", "SPY"], data_dir=data_dir)
    assert len(data["days"]) == 64 and data["days"][0] == "2026-10-01" and data["days"][-1] == "2026-12-31"
    assert "2026-11-26" not in data["days"] and "2026-11-27" in data["days"]                 # 休場は入らない・半日立会は入る
    assert data["source"]["2026-10-01"] == "2026-06-01" and data["source"]["2026-10-02"] == "2026-06-02"
    bars = dict(simdata.load_closes("T", data_dir))
    assert data["quotes"]["2026-10-05"]["T"] == bars[date(2026, 6, 3)]
    assert data == simdata.build(cfg, ["T", "SPY"], data_dir=data_dir)                           # 決定的


def test_signal_rule():
    flat = [10.0] * 30
    assert simdata.signal(flat, 20) == 50.0 and simdata.signal([1.0] * 5, 20) == 50.0
    up = [float(i) for i in range(30)]
    assert 50 < simdata.signal(up, 20) <= 100 and simdata.signal(up[::-1], 20) < 50
    assert simdata.signal([1.0] * 19 + [1000.0], 20) <= 100.0


def test_not_enough_bars_is_an_error(env, tmp_path):
    short = make_bars(tmp_path / "short", last=date(2026, 7, 1), symbols={"T": 25})
    with pytest.raises(ValueError, match="足が"):
        simdata.build(simdata.load_config("sim1"), ["T"], data_dir=short)


def test_only_sim_prefixed_traders_enter_the_sim_tree(env, tmp_path):
    _, data_dir, _ = env
    cfg = simdata.load_config("sim1")
    cfg.traders = ["sim_a", "test_a"]
    with pytest.raises(ValueError, match="sim_"):
        simdata.write_tree(str(tmp_path / "tree"), cfg, os.path.join(HERE, "config", "traders"), data_dir=data_dir)
    assert not (tmp_path / "tree").exists()


def test_credentials_warning_reads_names_only(tmp_path):
    p = tmp_path / ".env"
    p.write_text("TT_ENV=cert\nTT_CLIENT_SECRET=abc\nTT_PROD_REFRESH_TOKEN=\n")
    assert simrun.credentials_present(str(p)) == ["TT_CLIENT_SECRET"]
    assert simrun.credentials_present(str(tmp_path / "none")) == []


# ---------- 排他

def test_driver_and_controls_refuse_in_real_mode(env):
    base, _, child = env
    assert cli("simrun.py", ["sim1"], child).returncode == 5
    for cmd in (["speed", "60"], ["pause"], ["resume"], ["step"], ["stop"], ["check"]):
        r = cli("simctl.py", cmd, child)
        assert r.returncode == 5 and "実売買モード" in r.stderr, cmd
    assert cli("simctl.py", ["mode", "sim", "nope"], child).returncode == 2                     # 設定の無い名前
    assert not (base / "sim").exists() and not (base / "MODE").exists()
    assert cli("simctl.py", ["status"], child).stdout.startswith("=== 実売買モード ===")


def test_driver_refuses_another_name_and_a_busy_lock(env):
    base, _, child = env
    modes.switch("sim", "other")
    assert cli("simrun.py", ["sim1"], child).returncode == 5
    modes.switch("sim", "sim1")
    lock = modes.RunLock()
    assert lock.acquire("sim", "someone")
    assert cli("simrun.py", ["sim1"], child).returncode == 6
    assert cli("simctl.py", ["mode", "real"], child).returncode == 5                            # 動作中は切り替えられない
    lock.release()


# ---------- 通し

def test_three_days_at_max_speed(env):
    base, _, child = env
    real_before = sorted(glob.glob(os.path.join(HERE, "out", "*", "*"))), sorted(glob.glob(os.path.join(HERE, "state", "*", "*")))
    assert cli("simctl.py", ["mode", "sim", "sim1"], child).returncode == 0
    r = cli("simrun.py", ["sim1", "--speed", "max", "--days", "3"], child)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("=== シミュレーション sim1 — 実売買ではない ===")
    root = modes.sim_root("sim1")
    assert sorted(os.listdir(os.path.join(root, "out"))) == ["2026-10-01", "2026-10-02", "2026-10-05"]
    for kind in ("events", "signals", "quotes", "ledger"):
        got = rows(root, kind)
        assert got and all(x.get("sim") is True and x.get("mock") is True and x.get("test") is True for x in got), kind
    orders = rows(root, "orders")
    assert orders and all(o["final_status"] == "Filled" for o in orders)
    # 1 注文 1 トレーダー（合算しない）。整数株の人（sim_a・sim_c）の持ち分は整数株のまま ＝ 端数ができない
    assert all(len(o["parts"]) == 1 for o in orders)
    assert len({(o["date"], o["symbol"], o["side"]) for o in orders}) < len(orders)                # 同じ銘柄を同じ日に 2 人が買っている
    for name in ("sim_a", "sim_c"):
        st = json.load(open(os.path.join(root, "state", "cert", f"{name}.json")))
        assert st["holdings"] and all(float(h["shares"]).is_integer() for h in st["holdings"].values()), name
    # 金額の内訳が注文ごとに残り、売りの手数料はその人の台帳に入る
    assert all(set(o["amounts"]) >= {"gross_usd", "fee_usd", "net_usd", "fee_breakdown", "buying_power_effect"} and "total-fees" in o["amounts"]["fee_breakdown"] for o in orders)
    assert {o["quote_at_signal"]["mid"] for o in orders if o["symbol"] == "SPY"} != {o["quote_at_signal"]["mid"] for o in orders if o["symbol"] == "T"}   # 銘柄ごとの気配
    assert all(f["filled_at"].startswith(o["date"]) for o in orders for f in o["fills"])          # モックの時刻も仮の時計
    status = json.load(open(os.path.join(root, "sim", "status.json")))
    assert status["state"] == "終了" and status["day_index"] == 3 and status["days_total"] == 64 and status["sim"] is True
    assert simclock.read_control(modes.control_file("sim1"))["paused"] is True                    # 運転手がいない間は時計を止める
    assert cli("simctl.py", ["check"], child).returncode == 0
    assert sorted(os.listdir(os.path.join(root, "state", "cert"))) == ["journal.jsonl", "sim_a.json", "sim_b.json", "sim_c.json"]
    assert simrun  # 控え（journal.jsonl）は全部閉じている ＝ 未完なし
    import journal as jn
    assert jn.Journal(os.path.join(root, "state", "cert")).unfinished() == []
    assert (sorted(glob.glob(os.path.join(HERE, "out", "*", "*"))), sorted(glob.glob(os.path.join(HERE, "state", "*", "*")))) == real_before
    # 続きから: 流した発注できる時間帯は流し直さない
    r = cli("simrun.py", ["sim1", "--speed", "max", "--days", "1"], child)
    assert r.returncode == 0 and "続きから" in r.stdout and "2026-10-06" in r.stdout and "2026-10-05（" not in r.stdout


def test_records_do_not_depend_on_speed(env):
    base, _, child = env
    cli("simctl.py", ["mode", "sim", "sim1"], child)
    got = {}
    for speed in ("max", "1440"):
        r = cli("simrun.py", ["sim1", "--fresh", "--speed", speed, "--days", "3"], child)
        assert r.returncode == 0, r.stdout + r.stderr
        root = modes.sim_root("sim1")
        got[speed] = [strip_time(x) for kind in ("signals", "orders", "transfers", "ledger") for x in rows(root, kind)]
    for a, b in zip(got["max"], got["1440"]):
        a.pop("external_id", None), b.pop("external_id", None)
    assert got["max"] == got["1440"] and len(got["max"]) > 20


def test_half_day_is_refused_and_recorded(env):
    base, data_dir, child = env
    cli("simctl.py", ["mode", "sim", "sim1"], child)
    root = modes.sim_root("sim1")
    simdata.write_tree(root, simdata.load_config("sim1"), os.path.join(HERE, "config", "traders"), data_dir=data_dir)
    simclock.init_control(modes.control_file("sim1"), datetime(2026, 11, 25, 9, 30, tzinfo=ET), speed="max", paused=True)   # 途中の日から（過ぎた発注できる時間帯は流さない）
    r = cli("simrun.py", ["sim1", "--days", "3"], child)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(os.listdir(os.path.join(root, "out"))) == ["2026-11-25", "2026-11-27", "2026-11-30"]     # 11-26 は休場 ＝ 起こさない
    half = [json.loads(line) for line in open(os.path.join(root, "out", "2026-11-27", "events.jsonl"))]
    assert [e["kind"] for e in half] == ["out_of_window"] and "半日立会" in half[0]["reason"]
    status = json.load(open(os.path.join(root, "sim", "status.json")))
    assert status["rcs"] == {"2026-11-25 15:45": 0, "2026-11-27 15:45": 4, "2026-11-30 15:45": 0}


def test_step_runs_one_day_then_pauses_and_stop_ends_the_driver(env):
    base, _, child = env
    cli("simctl.py", ["mode", "sim", "sim1"], child)
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "simrun.py"), "sim1", "--speed", "max", "--paused"], env=child,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        root = modes.sim_root("sim1")
        control = modes.control_file("sim1")

        def wait_for(cond, what):
            for _ in range(300):
                if cond():
                    return
                time.sleep(0.1)
            raise AssertionError(what)
        wait_for(lambda: os.path.exists(os.path.join(root, "sim", "status.json")), "運転手が起きない")
        time.sleep(0.5)
        assert not os.path.isdir(os.path.join(root, "out"))                                         # 止まっている間は何も起きない
        assert cli("simctl.py", ["step"], child).returncode == 0
        wait_for(lambda: os.path.isdir(os.path.join(root, "out", "2026-10-01")) and simclock.read_control(control)["paused"], "step が 1 日で止まらない")
        time.sleep(0.5)
        assert os.listdir(os.path.join(root, "out")) == ["2026-10-01"]
        assert cli("simctl.py", ["stop"], child).returncode == 0
        assert proc.wait(timeout=30) == 0
        assert json.load(open(os.path.join(root, "sim", "status.json")))["state"] == "終了"
    finally:
        if proc.poll() is None:
            proc.kill()
    assert cli("simctl.py", ["mode", "real"], child).returncode == 0                                 # 運転手が止まれば戻せる
