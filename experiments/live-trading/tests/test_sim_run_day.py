"""仮の時計で執行器を 1 日通す（モックサーバに繋ぐ。ループバックだけ）。記録の印と、本物の木に 1 バイトも書かないこと。"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import mode as modes
import simclock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample", "mock_server.py"))
ET = ZoneInfo("America/New_York")

TRADER = '''name = "sim_x"
test = true
budget_usd = 300.0
symbols = ["SPY"]
combine = "asis"
threshold = 50.0
sizing = "notional"
[[models]]
kind = "file"
name = "script"
path = "../signals/sim_x.csv"
'''


def free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


@pytest.fixture
def mock_server():
    port, ws, dx = free_ports(3)
    proc = subprocess.Popen([sys.executable, MOCK, "--port", str(port), "--ws-port", str(ws), "--dxlink-port", str(dx), "--market-data", "--fill-noise", "0.003"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/customers/me/accounts", headers={"User-Agent": "test/1.0"}), timeout=1)
                break
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.kill()
        proc.wait()


def post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST", headers={"User-Agent": "test/1.0", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def tree(path):
    return sorted((os.path.join(r, f), os.stat(os.path.join(r, f)).st_mtime_ns) for r, _, fs in os.walk(path) for f in fs)


def run_day(base, args, rest_base, extra_env=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}
    env.update(extra_env or {})
    env.update({"TT_ENV_FILE": "/nonexistent", "LT_MODE_DIR": str(base), "TT_REST_BASE": rest_base, "TT_CLIENT_SECRET": "MOCK-SECRET", "TT_REFRESH_TOKEN": "MOCK-REFRESH"})
    return subprocess.run([sys.executable, os.path.join(HERE, "run_day.py"), *args], capture_output=True, text=True, env=env)


def setup_sim(base, monkeypatch, when_et, speed=60):
    monkeypatch.setenv("LT_MODE_DIR", str(base))
    modes.switch("sim", "sim1", by="test")
    root = modes.sim_root("sim1")
    os.makedirs(os.path.join(root, "config", "traders"))
    os.makedirs(os.path.join(root, "config", "signals"))
    open(os.path.join(root, "config", "traders", "sim_x.toml"), "w").write(TRADER)
    open(os.path.join(root, "config", "signals", "sim_x.csv"), "w").write("date,symbol,buy,exit\n2026-10-01,SPY,100,0\n2026-11-27,SPY,100,0\n")
    simclock.init_control(modes.control_file("sim1"), when_et, speed=speed)
    return root


def rows(root, date, kind):
    path = os.path.join(root, "out", date, f"{kind}.jsonl")
    return [json.loads(line) for line in open(path, encoding="utf-8")] if os.path.exists(path) else []


def test_one_day_under_the_sim_clock(tmp_path, monkeypatch, mock_server):
    # ⚠ 本物の木（記録・状態・機械のモード）が 1 バイトも動かないこと。⚠ **「MODE が無い」を前提にしない**
    #    （この機械はふだんシミュレーションモード ＝ MODE がある。live-trading.md §0-7 (f)）
    real_mode = pathlib.Path(HERE, "MODE").read_bytes() if os.path.exists(os.path.join(HERE, "MODE")) else None
    real_before = tree(os.path.join(HERE, "out")), tree(os.path.join(HERE, "state"))
    root = setup_sim(tmp_path, monkeypatch, datetime(2026, 10, 1, 15, 46, tzinfo=ET))
    r = run_day(tmp_path, ["--traders", "sim_x", "--mode", "submit", "--sim-clock"], mock_server)      # ⚠ --date も --ignore-window も渡さない
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("=== シミュレーション sim1（仮の時計 ×60） — 実売買ではない ===")
    orders = rows(root, "2026-10-01", "orders")                                                       # 日付は仮の今日
    assert len(orders) == 1 and orders[0]["final_status"] == "Filled"
    for kind in ("events", "signals", "quotes", "orders", "ledger", "balances", "positions"):
        got = rows(root, "2026-10-01", kind)
        assert got and all(x.get("sim") is True and x.get("mock") is True and x.get("test") is True for x in got), kind
    start = rows(root, "2026-10-01", "events")[0]
    assert start["now_et"].startswith("2026-10-01T15:4") and start["sim_name"] == "sim1" and start["sim_speed"] == 60
    assert start["halt_file"] == os.path.join(root, "HALT")
    assert not [e for e in rows(root, "2026-10-01", "events") if e["kind"] == "drawdown_warning"]
    state = json.load(open(os.path.join(root, "state", "cert", "sim_x.json")))
    assert state["last_date"] == "2026-10-01" and "SPY" in state["holdings"]
    blob = "".join(open(os.path.join(d, f)).read() for d, _, fs in os.walk(root) for f in fs)
    assert "MOCK-SECRET" not in blob and "MOCK-REFRESH" not in blob
    assert (tree(os.path.join(HERE, "out")), tree(os.path.join(HERE, "state"))) == real_before           # 本物の木は動かない
    assert (pathlib.Path(HERE, "MODE").read_bytes() if os.path.exists(os.path.join(HERE, "MODE")) else None) == real_mode


def test_drawdown_is_a_warning_only(tmp_path, monkeypatch, mock_server):
    """含み損が予算の 20% 以上（§0-2。2026-09-19 の利用者決定「警告だけ・止めるのは人」）: 記録に警告を残すが、執行器は止めない。"""
    root = setup_sim(tmp_path, monkeypatch, datetime(2026, 10, 1, 15, 46, tzinfo=ET))
    open(os.path.join(root, "config", "traders", "sim_x.toml"), "w").write(TRADER.replace('symbols = ["SPY"]', 'symbols = ["SPY", "QQQ"]'))
    open(os.path.join(root, "config", "signals", "sim_x.csv"), "w").write("date,symbol,buy,exit\n2026-10-01,SPY,0,0\n2026-10-01,QQQ,100,0\n")
    os.makedirs(os.path.join(root, "state", "cert"))
    json.dump({"name": "sim_x", "holdings": {"SPY": {"shares": 0.2, "avg_price": 1000.0, "opened": "2026-09-30"}}},      # 原価 $200 → 時価 約 $112 ＝ 予算 $300 の 29% の含み損
              open(os.path.join(root, "state", "cert", "sim_x.json"), "w"))
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 0.2}})                                 # 口座にも同じ株がある（帳尻は合っている）
    r = run_day(tmp_path, ["--traders", "sim_x", "--mode", "submit", "--sim-clock"], mock_server)
    assert r.returncode == 0 and "含み損の警告" in r.stderr, r.stdout + r.stderr                        # ⚠ rc は変えない
    warn = [e for e in rows(root, "2026-10-01", "events") if e["kind"] == "drawdown_warning"]
    assert len(warn) == 1 and warn[0]["trader"] == "sim_x" and warn[0]["drawdown_pct_of_budget"] >= 20 and warn[0]["threshold_pct"] == 20.0
    bought = rows(root, "2026-10-01", "orders")
    assert [(o["symbol"], o["side"], o["final_status"]) for o in bought] == [("QQQ", "buy", "Filled")]   # 警告が出ていても買いは続く
    assert "SPY" in json.load(open(os.path.join(root, "state", "cert", "sim_x.json")))["holdings"]      # 投げ売りしない


def test_window_and_calendar_follow_the_sim_clock(tmp_path, monkeypatch, mock_server):
    root = setup_sim(tmp_path, monkeypatch, datetime(2026, 10, 1, 15, 40, tzinfo=ET), speed=1)
    r = run_day(tmp_path, ["--traders", "sim_x", "--mode", "submit", "--sim-clock"], mock_server)
    assert r.returncode == 4 and "発注できる時間帯" in r.stderr                                                     # 仮の 15:40 は発注できる時間帯の外
    simclock.jump_to(modes.control_file("sim1"), datetime(2026, 11, 27, 15, 50, tzinfo=ET))
    r = run_day(tmp_path, ["--traders", "sim_x", "--mode", "submit", "--sim-clock"], mock_server)
    assert r.returncode == 4 and "半日立会" in r.stderr                                               # 仮の 11-27 は半日立会
    assert [e["kind"] for e in rows(root, "2026-11-27", "events")] == ["out_of_window"]


def test_halt_in_the_sim_tree_stops_the_sim_only(tmp_path, monkeypatch, mock_server):
    root = setup_sim(tmp_path, monkeypatch, datetime(2026, 10, 1, 15, 46, tzinfo=ET))
    open(os.path.join(root, "HALT"), "w").write("x")
    r = run_day(tmp_path, ["--traders", "sim_x", "--mode", "submit", "--sim-clock"], mock_server)
    assert r.returncode == 3 and [e["kind"] for e in rows(root, "2026-10-01", "events")] == ["halted"]


def test_driver_held_lock_lets_its_own_child_through(tmp_path, monkeypatch, mock_server):
    """運転手が run.lock を持ったまま起こした執行器は通る。他人が持っているときは拒否。"""
    root = setup_sim(tmp_path, monkeypatch, datetime(2026, 10, 1, 15, 46, tzinfo=ET))
    lock = modes.RunLock()
    assert lock.acquire("sim", "simrun")                                                               # このテストのプロセス ＝ 親
    r = run_day(tmp_path, ["--traders", "sim_x", "--mode", "dry-run", "--sim-clock"], mock_server)
    assert r.returncode == 0, r.stderr
    lock.release()
    assert lock.acquire("real", "someone else")
    r = run_day(tmp_path, ["--traders", "sim_x", "--mode", "dry-run", "--sim-clock"], mock_server)
    assert r.returncode == 6
    lock.release()


# ---------- 口座の建玉と台帳の帳尻（§0-8。2026-09-19 の利用者決定「提案の 3 段」）

def seed_state(root, holdings):
    os.makedirs(os.path.join(root, "state", "cert"), exist_ok=True)
    json.dump({"name": "sim_x", "holdings": holdings}, open(os.path.join(root, "state", "cert", "sim_x.json"), "w"))


def test_crash_after_submit_is_recovered_on_the_next_start(tmp_path, monkeypatch, mock_server):
    """段 1: 発注の後・台帳の保存の前に落ちる → 次の起動で控えから照会し、約定をその人の台帳に入れる。二重に買わない。"""
    root = setup_sim(tmp_path, monkeypatch, datetime(2026, 10, 1, 15, 46, tzinfo=ET))
    r = run_day(tmp_path, ["--traders", "sim_x", "--mode", "submit", "--sim-clock"], mock_server, extra_env={"LT_SIM_CRASH": "after_submit"})
    assert r.returncode == 137 and not os.path.exists(os.path.join(root, "state", "cert", "sim_x.json"))     # 買えているのに台帳は空
    entry = [json.loads(line) for line in open(os.path.join(root, "state", "cert", "journal.jsonl"))]
    assert [e["op"] for e in entry] == ["intent", "submitted"] and entry[0]["trader"] == "sim_x" and entry[0]["symbol"] == "SPY"
    r = run_day(tmp_path, ["--traders", "sim_x", "--mode", "submit", "--sim-clock"], mock_server)             # 同じ日に起こし直す
    assert r.returncode == 0, r.stdout + r.stderr
    ev = [e for e in rows(root, "2026-10-01", "events") if e["kind"] == "journal_recovered"]
    assert len(ev) == 1 and ev[0]["outcome"] == "recovered_filled" and ev[0]["shares"] > 0 and ev[0]["applied"] is True
    state = json.load(open(os.path.join(root, "state", "cert", "sim_x.json")))
    assert state["holdings"]["SPY"]["shares"] == ev[0]["shares"] and state["history"][0]["note"] == "recovered"
    assert rows(root, "2026-10-01", "orders") == []                                                            # 持っているので買い直さない
    assert [e["op"] for e in (json.loads(line) for line in open(os.path.join(root, "state", "cert", "journal.jsonl")))] == ["intent", "submitted", "done"]


def test_short_position_blocks_only_that_symbol(tmp_path, monkeypatch, mock_server):
    """段 2: 台帳にあるはずの株が口座に無い銘柄だけ、その日は売買しない。ほかの銘柄は通常どおり。"""
    root = setup_sim(tmp_path, monkeypatch, datetime(2026, 10, 1, 15, 46, tzinfo=ET))
    open(os.path.join(root, "config", "traders", "sim_x.toml"), "w").write(TRADER.replace('symbols = ["SPY"]', 'symbols = ["SPY", "QQQ"]'))
    open(os.path.join(root, "config", "signals", "sim_x.csv"), "w").write("date,symbol,buy,exit\n2026-10-01,SPY,0,100\n2026-10-01,QQQ,100,0\n")
    seed_state(root, {"SPY": {"shares": 0.2, "avg_price": 500.0, "opened": "2026-09-30"}})                  # 口座は空 ＝ SPY が 0.2 株足りない
    r = run_day(tmp_path, ["--traders", "sim_x", "--mode", "submit", "--sim-clock"], mock_server)
    assert r.returncode == 1 and "口座の建玉が台帳より少ない" in r.stderr
    ev = rows(root, "2026-10-01", "events")
    short = [e for e in ev if e["kind"] == "position_short"]
    assert len(short) == 1 and short[0]["symbol"] == "SPY" and short[0]["diff"] == -0.2 and short[0]["holders"] == {"sim_x": 0.2}
    assert [(e["symbol"], e["side"]) for e in ev if e["kind"] == "blocked_symbol"] == [("SPY", "sell")]
    assert [(o["symbol"], o["final_status"]) for o in rows(root, "2026-10-01", "orders")] == [("QQQ", "Filled")]
    assert ev[-1]["kind"] == "end" and ev[-1]["blocked_symbols"] == ["SPY"]


def test_extra_shares_in_the_account_do_not_stop_trading(tmp_path, monkeypatch, mock_server):
    """段 2: 口座のほうが多い ＝ 台帳の外の株（利用者の手持ちなど）。記録するだけで売買は続ける。"""
    root = setup_sim(tmp_path, monkeypatch, datetime(2026, 10, 1, 15, 46, tzinfo=ET))
    post(mock_server, "/_mock/positions", {"positions": {"SPY": 7}})
    r = run_day(tmp_path, ["--traders", "sim_x", "--mode", "submit", "--sim-clock"], mock_server)
    assert r.returncode == 0, r.stdout + r.stderr
    ev = [e for e in rows(root, "2026-10-01", "events") if e["kind"] == "positions_outside_ledger"]
    assert len(ev) == 1 and ev[0]["symbols"]["SPY"]["diff"] == 7
    assert [o["final_status"] for o in rows(root, "2026-10-01", "orders")] == ["Filled"]
