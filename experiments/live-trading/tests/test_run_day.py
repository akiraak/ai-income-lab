"""窓の判定と、鍵なしの本番が dry-run の前で止まること（subprocess。ネットワークは使わない）。"""
import os
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from run_day import in_window  # noqa: E402

ET = ZoneInfo("America/New_York")


def test_window():
    assert in_window(datetime(2026, 9, 17, 15, 45, tzinfo=ET))
    assert in_window(datetime(2026, 9, 17, 16, 4, tzinfo=ET))
    assert not in_window(datetime(2026, 9, 17, 16, 5, tzinfo=ET))
    assert not in_window(datetime(2026, 9, 17, 15, 44, tzinfo=ET))
    assert not in_window(datetime(2026, 9, 19, 15, 50, tzinfo=ET))   # 土曜


def _run(args, env_extra):
    env = {**os.environ, "TT_ENV_FILE": "/nonexistent", **env_extra}
    return subprocess.run([sys.executable, os.path.join(HERE, "run_day.py"), *args], capture_output=True, text=True, env=env)


def test_prod_submit_refused_without_both_keys(tmp_path):
    env = {"TT_PROD_CLIENT_SECRET": "s", "TT_PROD_REFRESH_TOKEN": "r", "LT_OUT_DIR": str(tmp_path), "LT_STATE_DIR": str(tmp_path / "st")}
    r = _run(["--traders", "test_a", "--env", "prod", "--mode", "submit", "--ignore-window"], env)
    assert r.returncode == 2 and "TT_ALLOW_PROD_ORDERS" in r.stderr
    r = _run(["--traders", "test_a", "--env", "prod", "--mode", "submit", "--ignore-window", "--i-know-this-is-real-money"], env)
    assert r.returncode == 2
    r = _run(["--traders", "test_a", "--env", "prod", "--mode", "dry-run", "--ignore-window"], env)
    assert r.returncode == 2 and "allow-prod-dry-run" in r.stderr


def test_halt_refuses_before_network(tmp_path):
    halt = tmp_path / "HALT"
    halt.write_text("x")
    env = {"TT_CLIENT_SECRET": "s", "TT_REFRESH_TOKEN": "r", "TT_HALT_FILE": str(halt), "LT_OUT_DIR": str(tmp_path), "LT_STATE_DIR": str(tmp_path / "st")}
    r = _run(["--traders", "test_a", "--mode", "dry-run", "--ignore-window"], env)
    assert r.returncode == 3 and "停止フラグ" in r.stderr


def test_budget_total_cap(tmp_path):
    env = {"TT_CLIENT_SECRET": "s", "TT_REFRESH_TOKEN": "r", "LT_OUT_DIR": str(tmp_path), "LT_STATE_DIR": str(tmp_path / "st")}
    r = _run(["--traders", "mock_a,mock_b", "--mode", "plan", "--max-total-budget", "500"], env)
    assert r.returncode == 2 and "予算の合計" in r.stderr
