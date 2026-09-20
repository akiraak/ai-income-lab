"""通し運転で確かめる 3 つ（プラン: docs/plans/test-automation-sim.md Phase 3）。⚠ 作り物の日足・モックだけ。

1. **予算の上限**: 何日流しても、取得原価が予算を超えない（⚠ 1 日に何回売買しても同じ）
2. **1 日に 2 つの窓**: 同じ日に 2 回起きて、⚠ **二重に買わない**（TODO の C9・D13 の前提）
3. **停止と解除**: 途中で `HALT` を置くとその日から発注が止まり、消すと翌日から再開する（⚠ 日をまたぐ振る舞い）

⚠ `halt` は筋書き（設定）に入れない（live-trading.md §0-7 (i) の決めごと「人が押す」）＝ **テスト自身が `HALT` を置く**。
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import mode as modes
import simclock
import simdata
from tests.test_simrun import make_bars

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")

TRADER = """name = "{name}"
test = true
budget_usd = {budget}
symbols = [{symbols}]
combine = "asis"
threshold = 50.0
sizing = "shares"
[[models]]
kind = "file"
name = "m20"
path = "../signals/sim_m20.csv"
"""

CONFIG = """name = "{name}"
traders = ["{trader}"]
start = "2026-10-01"
end = "2026-10-16"
source_start = "2026-04-01"
speed = "max"
windows = [{windows}]
"""


def setup(tmp, *, name, trader, budget, symbols, windows='"15:45:30"', signals="buy"):
    """仮データの木を作り、合図を差し替える（⚠ 毎日「持っていなければ買う」だけ ＝ 何日目に何が起きるかを固定する）。"""
    base, conf, tdir = tmp / "lt", tmp / "conf", tmp / "traders"
    for d in (base, conf, tdir):
        d.mkdir(exist_ok=True)
    (conf / f"{name}.toml").write_text(CONFIG.format(name=name, trader=trader, windows=windows), encoding="utf-8")
    (tdir / f"{trader}.toml").write_text(
        TRADER.format(name=trader, budget=budget, symbols=", ".join(f'"{s}"' for s in symbols)), encoding="utf-8")
    data_dir = make_bars(tmp / "bars")
    child = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}
    child.update(LT_MODE_DIR=str(base), LT_SIM_DATA_DIR=data_dir, LT_SIM_CONFIG_DIR=str(conf))
    os.environ["LT_MODE_DIR"] = str(base)
    modes.switch("sim", name, by="test")
    root = modes.sim_root(name)
    cfg = simdata.load_config(name, str(conf))
    simdata.write_tree(root, cfg, str(tdir), data_dir=data_dir)
    days = [d.isoformat() for d in simdata.sim_days(cfg)]
    with open(os.path.join(root, "config", "signals", "sim_m20.csv"), "w", encoding="utf-8") as f:
        f.write("date,symbol,buy,exit\n")
        for i, d in enumerate(days):
            # "buy" ＝ 毎日「持っていなければ買う」／ "churn" ＝ 1 日おきに買いと手仕舞い（⚠ 受渡し待ちが効き、予算の空きが動く）
            buy, exit_ = (100, 0) if signals == "buy" or i % 2 == 0 else (0, 100)
            for s in symbols:
                f.write(f"{d},{s},{buy},{exit_}\n")
    simclock.init_control(modes.control_file(name), datetime(2026, 10, 1, 9, 30, tzinfo=ET), speed="max")
    return root, child, days


def drive(name, child, *, days):
    r = subprocess.run([sys.executable, os.path.join(HERE, "simrun.py"), name, "--days", str(days)],
                       capture_output=True, text=True, env=child, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return r.stdout


def rows(root, day, kind):
    path = os.path.join(root, "out", day, f"{kind}.jsonl")
    return [json.loads(line) for line in open(path, encoding="utf-8")] if os.path.exists(path) else []


@pytest.fixture(autouse=True)
def _restore_mode_dir():
    old = os.environ.get("LT_MODE_DIR")
    yield
    if old is None:
        os.environ.pop("LT_MODE_DIR", None)
    else:
        os.environ["LT_MODE_DIR"] = old


# --- 1. 予算の上限 --------------------------------------------------------

@pytest.fixture(scope="module")
def capped(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cap")
    root, child, days = setup(tmp, name="simcap", trader="sim_cap", budget=90.0, symbols=["T", "PFE", "XLU"], signals="churn")
    drive("simcap", child, days=12)
    return root, days


def test_holdings_never_cost_more_than_the_budget(capped):
    """⚠ 何日流しても取得原価が予算（$90）を超えない。⚠ 1 日に何回売買しても同じ（CLAUDE.md の約束）。"""
    root, days = capped
    costs = [r["cost_in_use_usd"] for d in days for r in rows(root, d, "ledger")]
    assert costs and max(costs) <= 90.0 + 1e-9, max(costs)
    per_day = {}
    for d in days:
        per_day[d] = sum(o["value_usd"] for o in rows(root, d, "orders") if o["side"] == "buy")
    assert any(v > 0 for v in per_day.values()), "1 件も買っていないと上限の検査にならない"
    assert max(per_day.values()) <= 90.0 + 1e-9, per_day


def test_the_budget_bites_as_too_small_not_as_over_budget(capped):
    """⚠ **予算は「枠に丸める」ことで守られる**（`target = min(per_symbol, available)`）。

    ⚠ そのため `over_budget` は**いまの経路では出ない**（出たら実装が変わった合図。逆に出なくなったら丸めが壊れた合図）。
    枠で 1 株も買えないときは `too_small` になる。
    """
    root, days = capped
    kinds = [e["kind"] for d in days for e in rows(root, d, "events")]
    assert "too_small" in kinds, set(kinds)
    assert "over_budget" not in kinds, "over_budget が出た ＝ 予算の守り方が変わった（プランと仕様を直す）"


# --- 2. 1 日に 2 つの窓 ---------------------------------------------------

@pytest.fixture(scope="module")
def twice(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("two")
    root, child, days = setup(tmp, name="simtwo", trader="sim_two", budget=300.0, symbols=["T"],
                              windows='"15:46:00", "15:55:00"')
    drive("simtwo", child, days=6)          # 窓の数で数える ＝ 3 営業日
    return root, days[:3]


def test_two_windows_a_day_run_twice(twice):
    root, days = twice
    for d in days:
        starts = [e for e in rows(root, d, "events") if e["kind"] == "start"]
        assert len(starts) == 2, (d, starts)
        assert [e["now_et"][11:16] for e in starts] == ["15:46", "15:55"], (d, starts)


def test_the_second_window_does_not_buy_again(twice):
    """⚠ 同じ日に 2 回起きても二重に買わない（1 回目で持ったら 2 回目は hold）。"""
    root, days = twice
    first = days[0]
    assert len(rows(root, first, "orders")) == 1, rows(root, first, "orders")
    holds = [e for e in rows(root, first, "events") if e["kind"] == "hold" and e["symbol"] == "T"]
    assert holds, rows(root, first, "events")
    state = json.load(open(os.path.join(root, "state", "cert", "sim_two.json"), encoding="utf-8"))
    assert state["holdings"]["T"]["shares"] == int(state["holdings"]["T"]["shares"])
    assert all(len(rows(root, d, "orders")) <= 1 for d in days)


@pytest.fixture(scope="module")
def early(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("early")
    root, child, days = setup(tmp, name="simearly", trader="sim_early", budget=300.0, symbols=["T"], windows='"10:00:00"')
    drive("simearly", child, days=2)
    return root, days[:2]


def test_a_window_outside_the_executor_window_is_refused(early):
    """⚠ **執行器の窓は 15:45〜16:05 ET に固定**（`run_day.WINDOW_START/END`）。

    運転手は何時にでも起こせるが、執行器が `out_of_window` で拒む ＝ ⚠ **1 日に何度も売買する形（TODO の C9・D13）に
    進むときは、まずここを広げる必要がある**。この検査は、広げたときに落ちて気づくための印である。
    """
    root, days = early
    for d in days:
        kinds = [e["kind"] for e in rows(root, d, "events")]
        assert kinds == ["out_of_window"], (d, kinds)
        assert not rows(root, d, "orders"), d


# --- 3. 停止と解除（日をまたぐ）-------------------------------------------

@pytest.fixture(scope="module")
def halted(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("halt")
    root, child, days = setup(tmp, name="simhalt", trader="sim_halt", budget=300.0, symbols=["T", "PFE"], signals="churn")
    drive("simhalt", child, days=2)                       # 1〜2 日目: ふつうに売買する
    open(os.path.join(root, "HALT"), "w", encoding="utf-8").write("test")
    drive("simhalt", child, days=2)                       # 3〜4 日目: 止まっている
    os.remove(os.path.join(root, "HALT"))
    drive("simhalt", child, days=2)                       # 5〜6 日目: 再開する
    return root, days[:6]


def test_halt_stops_the_days_after_it_and_resumes_when_removed(halted):
    root, days = halted
    before, during, after = days[:2], days[2:4], days[4:6]
    assert any(rows(root, d, "orders") for d in before), "止める前に注文が無いと検査にならない"
    for d in during:
        assert [e["kind"] for e in rows(root, d, "events")] == ["halted"], (d, rows(root, d, "events"))
        assert not rows(root, d, "orders"), d                       # ⚠ 発注しない
    for d in after:
        kinds = [e["kind"] for e in rows(root, d, "events")]
        assert "halted" not in kinds and "start" in kinds, (d, kinds)   # ⚠ 解除したら翌日から起動する
    assert any(rows(root, d, "orders") for d in after), "解除しても売買が戻らない"


def test_halt_does_not_touch_the_real_tree(halted):
    """⚠ シミュレーションの `HALT` は `sim/<名前>/HALT` だけ（本物の out/HALT には触らない）。"""
    root, _ = halted
    assert root != os.path.join(HERE, "out")
    assert not os.path.exists(os.path.join(HERE, "out", "HALT"))
