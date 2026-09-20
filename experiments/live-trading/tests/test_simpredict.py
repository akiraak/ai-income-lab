"""シミュレーションの `kind = "experiment"` のトレーダーに渡す予測の作り置き（simpredict.py。live-trading.md §0-7 (k)）。

⚠ `cli.predict` は呼ばない（作り物の作り置きを置く）。MODE ／ run.lock ／ 記録 ／ 日足 ／ 作り置きは全部 tmp。
"""
import json
import os

import pytest

import mode as modes
import simdata
import simpredict
from test_simrun import cli, make_bars, rows

SIM_TOML = """name = "simx"
traders = ["sim_x"]
start = "2026-10-01"
end = "2026-10-09"
source_start = "2026-06-01"
"""
TRADER_TOML = """name = "sim_x"
test = true
budget_usd = 300.0
symbols = ["T", "VZ", "BAC"]
sizing = "shares"
combine = "asis"
threshold = 50.0
[[models]]
kind = "experiment"
name = "exp_x"
method = "手法 X"
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path / "lt"
    base.mkdir()
    data_dir = make_bars(tmp_path / "bars")
    conf, traders, cache = tmp_path / "simconf", tmp_path / "traders", tmp_path / "cache"
    for d in (conf, traders, cache):
        d.mkdir()
    (conf / "simx.toml").write_text(SIM_TOML, encoding="utf-8")
    (traders / "sim_x.toml").write_text(TRADER_TOML, encoding="utf-8")
    for k, v in (("LT_MODE_DIR", base), ("LT_SIM_DATA_DIR", data_dir), ("LT_SIM_CONFIG_DIR", conf), ("LT_SIM_PREDICT_DIR", cache)):
        monkeypatch.setenv(k, str(v))
    monkeypatch.setattr(simdata, "DATA_DIR", data_dir)              # ⚠ build() の既定の引数は import の時点で決まるので、テストは data_dir を明示して渡す
    monkeypatch.setattr(simdata, "SIM_CONFIG_DIR", str(conf))
    child = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}
    child.update(LT_MODE_DIR=str(base), LT_SIM_DATA_DIR=data_dir, LT_SIM_CONFIG_DIR=str(conf), LT_SIM_PREDICT_DIR=str(cache))
    return str(traders), str(cache), child


def fill_cache(cache, data, skip=(), shift_close=None):
    """出どころの日付ごとに作り置きを置く。買い% は日ごとに 80 ／ 20 を行き来する（買い → 売りが起きる）。"""
    for k, day in enumerate(data["days"]):
        src = data["source"][day]
        if src in skip:
            continue
        path = simpredict.cache_path(cache, src, "exp_x", "手法 X")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for sym in ("T", "VZ", "BAC", "SPY"):          # SPY はこの人が持たない銘柄（実験は銘柄集合ぜんぶを出す）
                buy = 80.0 if k % 2 == 0 else 20.0
                close = data["quotes"][day].get(sym, 1.0) * (1.01 if shift_close == sym else 1.0)
                f.write(json.dumps({"date": src, "model": "exp_x", "method": "手法 X", "symbol": sym, "buy": buy, "exit": 100 - buy,
                                    "proxy_close": close}, ensure_ascii=False) + "\n")


def built(traders_dir):
    cfg = simdata.load_config("simx", os.environ["LT_SIM_CONFIG_DIR"])
    return cfg, simdata.build(cfg, ["T", "VZ", "BAC"], data_dir=os.environ["LT_SIM_DATA_DIR"])


def test_install_rewrites_the_date_and_marks_rows(env, tmp_path):
    traders_dir, cache, _ = env
    cfg, data = built(traders_dir)
    fill_cache(cache, data)
    root = str(tmp_path / "tree")
    got = simpredict.install(root, cfg, traders_dir, data)
    assert got["days"] == len(data["days"]) == 7 and got["rows"] == 7 * 4 and got["close_mismatch"] == 0 and got["rows_without_quote"] == 7
    for day in data["days"]:
        placed = simpredict.read_rows(os.path.join(root, "out", day, "predict.jsonl"))
        assert {r["date"] for r in placed} == {day} and {r["source_date"] for r in placed} == {data["source"][day]}
        assert all(r["sim"] is True and r["mock"] is True and r["test"] is True and r["method"] == "手法 X" for r in placed)
    assert data["days"][0] == "2026-10-01" and data["source"]["2026-10-01"] == "2026-06-01"


def test_install_stops_when_a_day_is_missing(env, tmp_path):
    traders_dir, cache, _ = env
    cfg, data = built(traders_dir)
    fill_cache(cache, data, skip=("2026-06-03",))
    with pytest.raises(simpredict.SimPredictError, match="1 本足りない.*2026-06-03 exp_x"):
        simpredict.install(str(tmp_path / "tree"), cfg, traders_dir, data)
    assert not os.path.exists(tmp_path / "tree" / "out")            # 半端に置かない
    todo, total = simpredict.plan_jobs("simx", traders_dir, cache, data_dir=os.environ["LT_SIM_DATA_DIR"])
    assert total == 7 and [(j[0], j[1], j[2]) for j in todo] == [("2026-06-03", "exp_x", "手法 X")]   # 再開: 無いものだけ作る


def test_install_counts_close_mismatch(env, tmp_path):
    traders_dir, cache, _ = env
    cfg, data = built(traders_dir)
    fill_cache(cache, data, shift_close="VZ")
    got = simpredict.install(str(tmp_path / "tree"), cfg, traders_dir, data)
    assert got["close_mismatch"] == 7 and {m["symbol"] for m in got["close_mismatch_head"]} == {"VZ"}


def test_no_experiment_model_places_nothing(env, tmp_path):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = simdata.load_config("sim1", os.path.join(here, "config", "sim"))
    data = simdata.build(cfg, ["T", "VZ", "BAC"], data_dir=os.environ["LT_SIM_DATA_DIR"])
    assert simpredict.install(str(tmp_path / "tree"), cfg, os.path.join(here, "config", "traders"), data) is None
    assert not os.path.exists(tmp_path / "tree")


def test_cache_path_separates_methods(tmp_path):
    a, b = simpredict.cache_path("c", "2026-06-01", "e", "手法 A"), simpredict.cache_path("c", "2026-06-01", "e", "手法 B")
    assert a != b and simpredict.cache_path("c", "2026-06-01", "e", None).endswith(os.path.join("2026-06-01", "e.jsonl"))


def test_driver_runs_an_experiment_trader(env):
    traders_dir, cache, child = env
    cfg, data = built(traders_dir)
    assert cli("simctl.py", ["mode", "sim", "simx"], child).returncode == 0
    # 作り置きが無ければ運転手は起動しない
    r = cli("simrun.py", ["simx", "--speed", "max", "--traders-dir", traders_dir], child)
    assert r.returncode == 2 and "予測の作り置きが 7 本足りない" in r.stderr
    fill_cache(cache, data)
    r = cli("simrun.py", ["simx", "--fresh", "--speed", "max", "--days", "3", "--traders-dir", traders_dir], child)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "予測を置いた: 7 日・28 行（終値と気配の食い違い 0 行）" in r.stdout
    root = modes.sim_root("simx")
    orders = rows(root, "orders")
    assert [(d, {o["symbol"] for o in orders if o["date"] == d}) for d in ("2026-10-01", "2026-10-02", "2026-10-05")] == \
        [(d, {"T", "VZ", "BAC"}) for d in ("2026-10-01", "2026-10-02", "2026-10-05")]              # 買い 80 → 売り 20 → 買い 80 が予測どおりに起きる
    assert len({o["side"] for o in orders if o["date"] == "2026-10-02"} | {o["side"] for o in orders if o["date"] == "2026-10-01"}) == 2
    assert all(o["final_status"] == "Filled" for o in orders)
    sig = [s for s in rows(root, "signals") if s["date"] == "2026-10-01"]
    assert sig and all(s["buy"] == 80.0 for s in sig)
    assert cli("simctl.py", ["check"], child).returncode == 0       # 置いた predict.jsonl の行にも印がある
