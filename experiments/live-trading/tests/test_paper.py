"""紙上の対照（paper.py）: 状態機械がバックテストと同じ・差 3 の恒等式・B&H・差 1 の向き。ネットワークもファイルの外も使わない。"""
import json
import math
import os

import numpy as np

import livefs
import paper

SIMULATE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "feature-discovery", "ail", "validation", "simulate.py")


def _backtest_simulator():
    """バックテストの状態機械をファイルから直に読む。⚠ `sys.path` に feature-discovery を足さない（あちらの `tests` パッケージに当たって他のテストが壊れる）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_ail_simulate", SIMULATE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

DATES = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"]
CLOSE = {"AAA": [100.0, 101.0, 99.0, 102.0, 103.0, 101.0], "BBB": [50.0, 50.5, 51.0, 50.0, 49.0, 50.0]}
BUY = {"AAA": [60, 40, 40, 70, 55, 30], "BBB": [40, 45, 80, 52, 20, 90]}


def _write(path, rows):
    # ⚠ 記録は DB（livefs。道はそのまま）
    livefs.append_many(path, [json.dumps(r) for r in rows])


def _closes():
    return {s: dict(zip(DATES, v)) for s, v in CLOSE.items()}


def _out(tmp_path, ledger=None, orders=None, skip_ledger_on=()):
    out = tmp_path / "out"
    (tmp_path / "config" / "traders").mkdir(parents=True)
    (tmp_path / "config" / "traders" / "P1.toml").write_text('name = "P1"\nthreshold = 55.0\n', encoding="utf-8")
    for i, d in enumerate(DATES):
        _write(str(out / d / "signals.jsonl"), [{"date": d, "trader": "P1", "symbol": s, "buy": BUY[s][i], "exit": 100 - BUY[s][i]} for s in CLOSE])
        if ledger and d not in skip_ledger_on:
            _write(str(out / d / "ledger.jsonl"), [ledger(i, d)])
        if orders:
            _write(str(out / d / "orders.jsonl"), orders(i, d))
    return str(out)


def test_paper_state_machine_matches_the_backtest_simulator(tmp_path):
    sim = _backtest_simulator()
    rows = paper.build_rows(_out(tmp_path), _closes(), "bars")
    assert [r["date"] for r in rows] == DATES and all(r["trader"] == "P1" and r["n_symbols"] == 2 for r in rows)
    # バックテスト: 足 t の y ＝ t → t+1 の対数リターン。紙上の d の行 ＝ 前の日の持ち高 × (d−1 → d) − d の売買のコスト
    want = np.zeros(len(DATES))
    for s in CLOSE:
        c = np.array(CLOSE[s])
        y = np.append(np.log(c[1:] / c[:-1]), 0.0)
        r = sim.simulate(np.array(BUY[s], float), y, 55.0, 5.0)
        net = r["net_bp"].copy()
        if r["forced_close"]:
            net[-1] += 2.5                                  # ⚠ 紙上は強制清算しない（実売買は終わらない）
        gross, cost = r["pos"] * y * 1e4, r["pos"] * y * 1e4 - net
        want[1:] += gross[:-1] / 2                          # 値動きは次の日の行に付く
        want -= cost / 2                                    # コストは売買した日の行に付く
    assert [r["paper_bp"] for r in rows] == [round(float(x), 2) for x in want]
    assert rows[-1]["paper_cum_bp"] == round(float(sum(round(float(x), 10) for x in want)), 2)


def test_buy_and_hold_pays_one_way_cost_once(tmp_path):
    rows = paper.build_rows(_out(tmp_path), _closes(), "bars")
    assert rows[0]["bh_bp"] == -2.5
    total = sum(math.log(v[-1] / v[0]) * 1e4 for v in CLOSE.values()) / 2 - 2.5
    assert abs(rows[-1]["bh_cum_bp"] - total) < 0.05


def test_diff3_cum_is_always_paper_minus_real_even_when_a_day_has_no_ledger(tmp_path):
    def ledger(i, d):
        # AAA を 1 株 100 で持ち続ける台帳（予算 1000）
        return {"date": d, "trader": "P1", "budget_usd": 1000.0, "realized_usd": 0.0, "fees_usd": 0.0,
                "holdings": {"AAA": {"shares": 1.0, "avg_price": 100.0}}}
    rows = paper.build_rows(_out(tmp_path, ledger=ledger, skip_ledger_on={"2026-09-23"}), _closes(), "bars")
    by = {r["date"]: r for r in rows}
    assert by["2026-09-23"]["real_bp"] is None and by["2026-09-23"]["diff3_bp"] is None
    for r in rows:
        if r["real_bp"] is not None:
            assert abs(r["diff3_cum_bp"] - (r["paper_cum_bp"] - r["real_cum_bp"])) < 0.02
    # 実物の累計 ＝ 最終日の含み益 ÷ 予算（AAA 100 → 101 ＝ $1 ＝ 10bp）
    assert rows[-1]["real_cum_bp"] == 10.0 and rows[-1]["real_usd"] == 1.0


def test_diff1_is_positive_when_the_fill_is_worse_and_fees_are_summed(tmp_path):
    def orders(i, d):
        if i != 0:
            return []
        q = {"bid": 99.9, "ask": 100.1, "mid": 100.0}
        return [{"symbol": "AAA", "side": "buy", "mode": "submit", "parts": [{"trader": "P1"}], "quote_at_signal": q,
                 "fills": [{"shares": 1.0, "price": 100.2}], "amounts": {"fee_usd": 0.01}},
                {"symbol": "BBB", "side": "sell", "mode": "submit", "parts": [{"trader": "P1"}], "quote_at_signal": {"bid": 49.9, "ask": 50.1, "mid": 50.0},
                 "fills": [{"shares": 1.0, "price": 49.9}], "amounts": {"fee_usd": 0.02}},
                {"symbol": "BBB", "side": "buy", "mode": "submit", "parts": [{"trader": "P1"}], "quote_at_signal": None, "fills": [], "amounts": {}},
                {"symbol": "AAA", "side": "buy", "mode": "submit", "parts": [{"trader": "OTHER"}], "fills": [{"shares": 1.0, "price": 1.0}]}]
    r = paper.build_rows(_out(tmp_path, orders=orders), _closes(), "bars")[0]
    assert (r["orders"], r["filled"], r["not_filled"], r["unexecuted"]) == (3, 2, 1, 1)
    assert r["diff1_quote_to_fill_bp"] == 20.0                  # 買い ＋20bp・売り ＋20bp（どちらも不利）の中央値
    assert r["diff1_fill_to_close_bp"] == round((20.0 + (-(49.9 - 50.0) / 50.0 * 1e4)) / 2, 2)
    assert r["diff2_fees_usd"] == 0.03 and r["diff2_half_spread_bp"] == 15.0   # 10bp と 20bp の中央値


def test_last_date_drops_days_without_an_official_close(tmp_path):
    rows = paper.build_rows(_out(tmp_path), _closes(), "bars", last_date="2026-09-23")
    assert rows[-1]["date"] == "2026-09-23"
