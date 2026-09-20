"""1 日の買いの合計の上限（`--max-day-usd`。プラン: docs/plans/day-cap-across-traders.md）。

⚠ **2026-09-20 まで効いていなかった**: 上限をローカル変数で数えていたので、**トレーダーごと・1 起動ごとに 0 から**
始まっていた（3 人なら実質 3 倍）。いまは**その日の `orders.jsonl` から数え直して**全員・全起動で 1 つの上限を分け合う。
"""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import mode as modes
import simclock
from plan import DayCap, size_intents, spent_today
from state import TraderState
from tests.test_sim_run_day import HERE, mock_server, rows, run_day  # noqa: F401  (mock_server は fixture)
from trader import Trader

ET = ZoneInfo("America/New_York")


def order(side="buy", mode="submit", status="Filled", value=100.0, fills=((1.0, 100.0),)):
    return {"side": side, "mode": mode, "final_status": status, "value_usd": value,
            "fills": [{"shares": s, "price": p} for s, p in (fills or [])]}


# --- 数え方（⚠ 記録が正本）-----------------------------------------------

def test_spent_today_counts_filled_buys_only():
    got = spent_today([
        order(fills=[(2.0, 50.0)]),                                   # 約定 $100 ＝ 数える
        order(side="sell", fills=[(1.0, 999.0)]),                     # 売りは数えない
        order(mode="dry-run", fills=[(1.0, 999.0)]),                  # dry-run は口座に出ていない
        order(status="Cancelled", fills=[]),                          # 取消は使っていない
        order(status="error", fills=[]),                              # エラーも使っていない
        order(status="Received", fills=[], value=30.0),               # ⚠ まだ分からない ＝ 注文額で保守側に数える
        "こわれた行",                                                   # ⚠ 壊れていても落ちない
    ])
    assert got == 130.0


def test_spent_today_is_zero_without_records():
    assert spent_today([]) == 0.0


# --- 上限の分け合い（⚠ 全トレーダーで 1 つ）------------------------------

def _trader(name, budget=300.0, symbols=("AAA",)):
    return Trader(name=name, budget_usd=budget, symbols=list(symbols), combine="asis", threshold=50.0,
                  sizing="notional", models=[], test=True)


def test_the_cap_is_shared_between_traders():
    """⚠ 直す前は 2 人目も満額買えていた（1 人ずつ 0 から数えていたため）。"""
    cap = DayCap(150.0)
    raw = [{"symbol": "AAA", "side": "buy", "buy_pct": 60.0, "exit_pct": 0.0}]
    first, ev1 = size_intents(_trader("t1", budget=100.0), TraderState("t1"), raw, {"AAA": 10.0}, "2026-10-01", cap)
    second, ev2 = size_intents(_trader("t2", budget=100.0), TraderState("t2"), raw, {"AAA": 10.0}, "2026-10-01", cap)
    assert [i.usd for i in first] == [100.0] and not ev1
    assert second == [] and [e["kind"] for e in ev2] == ["over_day_cap"]
    assert ev2[0]["spent_today_usd"] == 100.0 and ev2[0]["cap"] == 150.0
    assert cap.spent == 100.0


def test_the_cap_starts_from_what_was_already_bought_today():
    """起動をまたぐぶん ＝ その日の記録から数えた額を初期値にする。"""
    cap = DayCap(150.0, spent_today([order(fills=[(1.0, 120.0)])]))
    raw = [{"symbol": "AAA", "side": "buy", "buy_pct": 60.0, "exit_pct": 0.0}]
    its, ev = size_intents(_trader("t1", budget=100.0), TraderState("t1"), raw, {"AAA": 10.0}, "2026-10-01", cap)
    assert its == [] and ev[0]["kind"] == "over_day_cap" and ev[0]["spent_today_usd"] == 120.0


def test_a_plain_number_still_works():
    """⚠ 既存の呼び方（数値）も残す（その場限りの上限として包む）。"""
    cap_events = size_intents(_trader("t1"), TraderState("t1"),
                              [{"symbol": "AAA", "side": "buy", "buy_pct": 60.0, "exit_pct": 0.0}],
                              {"AAA": 10.0}, "2026-10-01", 50.0)[1]
    assert [e["kind"] for e in cap_events] == ["over_day_cap"]


# --- 通し（同じ日に 2 回起こす）------------------------------------------

TRADERS = {
    "sim_p": '''name = "sim_p"
test = true
budget_usd = 300.0
symbols = ["SPY"]
combine = "asis"
threshold = 50.0
sizing = "notional"
[[models]]
kind = "file"
name = "script"
path = "../signals/sim_p.csv"
''',
    "sim_q": '''name = "sim_q"
test = true
budget_usd = 300.0
symbols = ["AAPL"]
combine = "asis"
threshold = 50.0
sizing = "notional"
[[models]]
kind = "file"
name = "script"
path = "../signals/sim_q.csv"
''',
}


@pytest.fixture
def two_traders(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_MODE_DIR", str(tmp_path))
    modes.switch("sim", "sim1", by="test")
    root = modes.sim_root("sim1")
    os.makedirs(os.path.join(root, "config", "traders"))
    os.makedirs(os.path.join(root, "config", "signals"))
    for name, toml in TRADERS.items():
        open(os.path.join(root, "config", "traders", f"{name}.toml"), "w").write(toml)
        sym = "SPY" if name == "sim_p" else "AAPL"
        open(os.path.join(root, "config", "signals", f"{name}.csv"), "w").write(f"date,symbol,buy,exit\n2026-10-01,{sym},100,0\n")
    simclock.init_control(modes.control_file("sim1"), datetime(2026, 10, 1, 15, 46, tzinfo=ET))
    return root


def test_two_traders_and_two_runs_share_one_day_cap(tmp_path, monkeypatch, mock_server, two_traders):
    """⚠ いちばん大事な回帰: 3 人なら 3 倍・2 回起こせば 2 倍、になっていた（2026-09-20 に直した）。"""
    root = two_traders
    args = ["--traders", "sim_p,sim_q", "--mode", "submit", "--sim-clock", "--max-day-usd", "400"]
    first = run_day(tmp_path, args, mock_server)
    assert first.returncode in (0, 1), first.stdout + first.stderr
    orders = rows(root, "2026-10-01", "orders")
    events = rows(root, "2026-10-01", "events")
    bought = sum(float(o["value_usd"]) for o in orders if o["side"] == "buy")
    assert 0 < bought <= 400.0 + 1e-9, orders                          # ⚠ 買えてはいる・2 人で 600 にはならない
    assert [e["kind"] for e in events].count("over_day_cap") == 1, events
    # 同じ日にもう 1 回起こしても、合計で上限を超えない（⚠ 記録から数え直している）
    second = run_day(tmp_path, args, mock_server)
    assert second.returncode in (0, 1), second.stdout + second.stderr
    again = rows(root, "2026-10-01", "orders")
    assert sum(float(o["value_usd"]) for o in again if o["side"] == "buy") <= 400.0 + 1e-9, again
    end = [e for e in rows(root, "2026-10-01", "events") if e["kind"] == "end"]
    assert end[-1]["day_cap_usd"] == 400.0 and end[-1]["day_spent_usd"] <= 400.0
    assert "今日すでに買った額" in second.stdout                          # 2 回目は記録から数え直したと分かる
