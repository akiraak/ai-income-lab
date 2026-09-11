"""`cli.run` の閾値つき売買パスを、合成パネルで端から端まで通す。

⚠ **leak の配線検査**（rules.md 13-10）: 未来を知る列を混ぜた表では対 B&H の上乗せが
跳ね上がる。⚠ **跳ねなければ 較正 → 閾値 → 状態機械 のどこかが壊れている。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import runs
from ail.validation import checks
from cli.run import evaluate_trading
import ail.bootstrap  # noqa: F401


def _panel(n_days=800, n_sym=3, seed=0, leak=False):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        y = rng.normal(0.0002, 0.01, n_days)               # わずかな上ドリフト
        d = pd.DataFrame({"symbol": f"S{i}", "ts": ts, "y": y,
                          "own_ret_1": np.r_[0.0, y[:-1]],
                          "noise": rng.normal(0, 1, n_days)})
        if leak:
            d["LEAK_y"] = y                                # ⚠ わざとした先読み
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def _exp(form="shared"):
    return {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": form},
            "validation": {"split": "walk_forward_dates", "folds": 5, "seed": 0},
            "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "selectors": ["全部使う（基準）"], "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))       # ⚠ 本物の runs/ を汚さない
    return runs.Run("test_trading", {}, seed=0)


def _feats(panel):
    return [c for c in panel.columns if c not in ("symbol", "ts", "y")]


@pytest.mark.parametrize("form", ["shared", "per_symbol"])
def test_outputs_have_the_promised_shape(run, form):
    """result = 手法 × fold × 閾値、per_symbol = さらに × 銘柄（プラン §Phase 2 の 5）。"""
    panel = _panel()
    res, per_sym, summary, daily = evaluate_trading(panel, _feats(panel), _exp(form), run)
    methods = set(res["手法"])
    assert methods == {"全部使う（基準）", "基準 常に上（ドリフト）", "基準 直前リターンの符号"}
    assert set(res["閾値"]) == {50.0, 55.0, 60.0}
    assert len(res) == 3 * 5 * 3                           # 手法 3 × fold 5 × 閾値 3
    assert set(per_sym["銘柄"]) == {"S0", "S1", "S2"}
    assert len(per_sym) == 3 * 5 * 3 * 3
    assert {"取引回数", "保有日率", "見送り日数"} <= set(per_sym.columns)
    assert "閾値" in summary.columns


def test_buy_and_hold_costs_exactly_5bp_per_fold(run):
    """B&H のコストは 1 fold ちょうど 5bp（13-5。粗利 − 純利 = 5）。"""
    panel = _panel()
    res, _s, _g, _d = evaluate_trading(panel, _feats(panel), _exp(), run)
    bh = res[res["手法"] == "基準 常に上（ドリフト）"]
    assert np.allclose(bh["粗利bp"] - bh["純利bp"], 5.0)
    assert (bh["取引回数"] == 3).all()                      # 銘柄ごとに建て 1 回（清算は回数に入れない）
    assert (bh["保有日率"] == 1.0).all()


def test_higher_theta_never_trades_more(run):
    """閾値を上げると取引回数は増えない（直前符号は回転が多いのでここで効く）。"""
    panel = _panel()
    res, _s, _g, _d = evaluate_trading(panel, _feats(panel), _exp(), run)
    mom = res[res["手法"] == "基準 直前リターンの符号"]
    by = mom.groupby("閾値")["取引回数"].sum()
    assert by[50.0] >= by[55.0] >= by[60.0]


def test_leak_makes_the_edge_jump_in_both_forms(run, tmp_path, monkeypatch):
    """⚠ **上乗せの跳ねで配線を検査する**（13-10）。(A)(B) 両形式で跳ねること。"""
    for form in ("shared", "per_symbol"):
        r = runs.Run(f"leak_{form}", {}, seed=0)
        panel = _panel(leak=True)
        exp = _exp(form)
        res, per_sym, summary, daily = evaluate_trading(panel, _feats(panel), exp, r)
        doc = checks.compute_trading(res, summary, per_sym, daily,
                                     {**exp, "trading": exp["trading"]}, n_trials=70, leak=True)
        for th, e in doc["by_threshold"].items():
            assert e["edge_vs_bh"]["mean_bp"] > 50.0, (form, th)   # ⚠ 跳ねなければ配線が壊れている
            assert e["edge_vs_bh"]["positive"] == e["edge_vs_bh"]["folds"]


def test_without_leak_the_edge_stays_small(run):
    """先読みの無い雑音の表では上乗せは跳ねない（跳ねたらまず配線を疑う。13-10）。"""
    panel = _panel()
    exp = _exp()
    res, per_sym, summary, daily = evaluate_trading(panel, _feats(panel), exp, run)
    doc = checks.compute_trading(res, summary, per_sym, daily, exp, n_trials=70)
    for e in doc["by_threshold"].values():
        assert abs(e["edge_vs_bh"]["mean_bp"]) < 50.0


def test_calibration_coefficients_are_recorded(run, tmp_path):
    """(a, b) と fit 元が `fitted/` に残る（13-2 の 3。⚠ 次の実行では読み込まない）。"""
    import json
    import os
    panel = _panel()
    evaluate_trading(panel, _feats(panel), _exp(), run)
    d = os.path.join(run.dir, "fitted")
    files = sorted(os.listdir(d))
    assert any(f.startswith("calibration_f") for f in files)
    doc = json.load(open(os.path.join(d, files[0]), encoding="utf-8"))
    cal = doc["全部使う（基準）"]
    assert set(cal) == {"a", "b", "source"} and cal["source"] in ("holdout", "train", "constant")
