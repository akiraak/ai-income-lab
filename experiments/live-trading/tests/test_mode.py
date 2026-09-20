"""モードと排他（live-trading.md §0-7 (a)）: MODE が無ければ real ／ 片方のモードではもう片方が起動を拒む ／ run.lock ／ 切り替え。

⚠ ネットワークは使わない（拒否は全部、認証の前で起きる）。MODE と run.lock は `LT_MODE_DIR` で tmp に向ける（本物を触らない）。
"""
import json
import os
import subprocess
import sys

import pytest

import mode as modes

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERT = {"TT_CLIENT_SECRET": "s", "TT_REFRESH_TOKEN": "r"}


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_MODE_DIR", str(tmp_path))
    return tmp_path


def run_day(base, args, env_extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}
    env.update({"TT_ENV_FILE": "/nonexistent", "LT_MODE_DIR": str(base), "LT_OUT_DIR": str(base / "out"), "LT_STATE_DIR": str(base / "state" / "x"), **env_extra})
    return subprocess.run([sys.executable, os.path.join(HERE, "run_day.py"), *args], capture_output=True, text=True, env=env)


def events(base, sub="out"):
    rows = []
    for root, _, files in os.walk(base / sub):
        for f in files:
            if f == "events.jsonl":
                rows += [json.loads(line) for line in open(os.path.join(root, f), encoding="utf-8")]
    return rows


# ---------- モードの正本

def test_no_mode_file_means_real(base):
    assert modes.read_mode() == modes.Mode("real") and not modes.read_mode().is_sim


def test_broken_mode_file_refuses_both(base):
    (base / "MODE").write_text("{")
    with pytest.raises(modes.ModeError):
        modes.read_mode()
    r = run_day(base, ["--traders", "test_a", "--mode", "plan"], CERT)
    assert r.returncode == 5 and "MODE が読めない" in r.stderr


def test_switch_writes_mode_and_log(base):
    m = modes.switch("sim", "sim1", by="test")
    assert m.is_sim and modes.read_mode().name == "sim1"
    assert modes.switch("real").mode == "real" and modes.read_mode().name is None
    log = [json.loads(line) for line in open(base / "mode.log", encoding="utf-8")]
    assert [(r["from"], r["to"]) for r in log] == [("real", "sim"), ("sim", "real")]
    for bad in ("", "../x", "Sim 1", None):
        with pytest.raises(modes.ModeError):
            modes.switch("sim", bad)


def test_switch_refused_while_something_runs(base):
    lock = modes.RunLock()
    assert lock.acquire("real", "run_day")
    with pytest.raises(modes.ModeError, match="何かが動いている"):
        modes.switch("sim", "sim1")
    assert not modes.RunLock().acquire("real", "again")          # 同じモードの二重起動も取れない
    lock.release()
    assert modes.switch("sim", "sim1").is_sim


def test_switch_with_open_real_positions_needs_confirmation(base):
    os.makedirs(base / "state" / "prod")
    (base / "state" / "prod" / "T1.json").write_text(json.dumps({"name": "T1", "holdings": {"KO": {"shares": 2, "avg_price": 70, "opened": "2026-10-01"}}}))
    with pytest.raises(modes.ModeError, match="手仕舞いも出ない"):
        modes.switch("sim", "sim1")
    assert modes.read_mode().mode == "real"
    assert modes.switch("sim", "sim1", real_trading_will_stop=True).is_sim


# ---------- シミュレーションモードでは本物の執行器が起動しない

def test_sim_mode_refuses_real_run_day_even_with_prod_keys(base):
    modes.switch("sim", "sim1")
    env = {"TT_PROD_CLIENT_SECRET": "s", "TT_PROD_REFRESH_TOKEN": "r", "TT_ALLOW_PROD_ORDERS": "1"}
    r = run_day(base, ["--traders", "test_a", "--env", "prod", "--mode", "submit", "--i-know-this-is-real-money", "--ignore-window"], env)
    assert r.returncode == 5 and "シミュレーションモード" in r.stderr
    assert "シミュレーション sim1" in r.stdout                    # 1 行目にモード
    ev = events(base)
    assert [e["kind"] for e in ev] == ["refused_mode_sim"] and ev[0]["sim_name"] == "sim1" and "sim" not in ev[0]   # 本物の記録に理由が残る
    assert not (base / "sim").exists() and not (base / "state" / "x").exists()


def test_sim_mode_refuses_cert_and_plan_too(base):
    modes.switch("sim", "sim1")
    for mode in ("plan", "dry-run", "submit"):
        r = run_day(base, ["--traders", "test_a", "--mode", mode, "--ignore-window"], CERT)
        assert r.returncode == 5, (mode, r.stderr)


# ---------- 実売買モードでは仮の時計が使えない ／ 仮の時計はモックにしか繋がない

SIM_ARGS = ["--traders", "sim_x", "--mode", "submit", "--sim-clock"]
MOCK = {**CERT, "TT_REST_BASE": "http://127.0.0.1:9"}


def test_real_mode_refuses_sim_clock(base):
    for env in (MOCK, {**MOCK, "LT_SIM_CLOCK": "1"}):
        r = run_day(base, SIM_ARGS if "LT_SIM_CLOCK" not in env else SIM_ARGS[:-1], env)
        assert r.returncode == 2 and "MODE が real" in r.stderr
    assert not (base / "out").exists()


@pytest.mark.parametrize("args,env,why", [
    (SIM_ARGS, CERT, "ループバックのモックではない"),                                        # cert（接続先の差し替えなし）
    (SIM_ARGS, {**CERT, "TT_REST_BASE": "https://api.cert.tastyworks.com"}, "ループバックのモックではない"),
    (SIM_ARGS + ["--env", "prod"], {**MOCK, "TT_PROD_REST_BASE": "http://127.0.0.1:9"}, "prod"),
    (SIM_ARGS, {**MOCK, "TT_ALLOW_PROD_ORDERS": "1"}, "本番の鍵"),
    (SIM_ARGS, {**MOCK, "TT_ALLOW_PROD_DRY_RUN": "1"}, "本番の鍵"),
    (SIM_ARGS + ["--i-know-this-is-real-money"], MOCK, "本番の鍵"),
    (SIM_ARGS + ["--allow-prod-dry-run"], MOCK, "本番の鍵"),
])
def test_sim_clock_only_against_loopback_mock_without_prod_keys(base, args, env, why):
    modes.switch("sim", "sim1")
    r = run_day(base, args, env)
    assert r.returncode == 2 and "仮の時計は使えない" in r.stderr and why in r.stderr, r.stderr
    assert not (base / "out").exists() and not (base / "sim").exists()


def test_trader_names_cannot_cross(base):
    r = run_day(base, ["--traders", "sim_a", "--mode", "plan"], CERT)                         # 本物の時計 × sim_ の名前
    assert r.returncode == 2 and "sim_" in r.stderr
    modes.switch("sim", "sim1")
    import simclock
    from datetime import datetime, timezone
    simclock.init_control(modes.control_file("sim1"), datetime(2026, 10, 1, 19, 46, tzinfo=timezone.utc))
    env = {k: v for k, v in MOCK.items()}
    r = run_day(base, ["--traders", "test_a", "--mode", "submit", "--sim-clock"], {**env, "LT_OUT_DIR": "", "LT_STATE_DIR": ""})
    assert r.returncode == 2 and "sim_" in r.stderr


def test_sim_clock_cannot_write_outside_its_tree(base):
    modes.switch("sim", "sim1")
    import simclock
    from datetime import datetime, timezone
    simclock.init_control(modes.control_file("sim1"), datetime(2026, 10, 1, 19, 46, tzinfo=timezone.utc))
    r = run_day(base, SIM_ARGS, MOCK)                                                          # LT_OUT_DIR ＝ base/out（本物の側）
    assert r.returncode == 2 and "置き場" in r.stderr and not (base / "out").exists()


def test_real_clock_cannot_write_into_sim_tree(base):
    r = run_day(base, ["--traders", "test_a", "--mode", "plan"], {**CERT, "LT_OUT_DIR": str(base / "sim" / "sim1" / "out")})
    assert r.returncode == 2 and "シミュレーションの置き場" in r.stderr


# ---------- run.lock ／ LT_MODE_DIR の歯止め

def test_second_run_day_refused_while_lock_is_held(base):
    lock = modes.RunLock()
    assert lock.acquire("real", "other")
    r = run_day(base, ["--traders", "test_a", "--mode", "dry-run", "--ignore-window"], CERT)
    assert r.returncode == 6 and "run.lock" in r.stderr
    assert [e["kind"] for e in events(base)] == ["refused_lock_busy"]


def test_mode_dir_override_cannot_submit_to_a_real_endpoint(base):
    r = run_day(base, ["--traders", "test_a", "--mode", "submit", "--ignore-window"], CERT)
    assert r.returncode == 2 and "LT_MODE_DIR" in r.stderr
