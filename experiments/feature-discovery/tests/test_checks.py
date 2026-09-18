"""⚠ **検査そのものを検査する。**

⚠ **画面のスコアはここが出す数字である。** ここが静かに間違うと、
⚠ **管理画面の順位がそのまま嘘になる。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail.validation import checks


def result_of(rows: dict[str, list[float]], gross: dict[str, list[float]] | None = None):
    """手法ごとに 5 fold ぶんの純利（と粗利）を持つ result.csv の形を作る。"""
    out = []
    for name, nets in rows.items():
        g = (gross or {}).get(name, [x + 5.0 for x in nets])
        for f, (n, gg) in enumerate(zip(nets, g), 1):
            out.append({"手法": name, "fold": f, "選んだ本数": 8, "的中率": 0.5,
                        "IC": 0.01, "粗利bp": gg, "純利bp": n})
    return pd.DataFrame(out)


def summary_of(res: pd.DataFrame) -> pd.DataFrame:
    return (res.groupby("手法")
               .agg(本数=("選んだ本数", "mean"), 的中率=("的中率", "mean"), IC=("IC", "mean"),
                    粗利bp=("粗利bp", "mean"), 純利bp=("純利bp", "mean"), fold数=("fold", "size")))


CONFIG = {"cost_bp": 5.0, "validation": {"folds": 5}}


# --- 基準線をスコアにしない -------------------------------------------

@pytest.mark.parametrize("name,expect", [
    ("基準 常に上（ドリフト）", True),
    ("基準 直前リターンの符号", True),
    ("全部使う（基準）", True),
    ("乱択（基準）", True),
    ("F1-2 相互情報量", False),
    ("F3-5 クラスタ化MDA", False),
])
def test_baselines_are_recognised(name, expect):
    assert checks.is_baseline(name) is expect


def test_score_never_comes_from_a_baseline():
    """⚠ **「常に上」が最良になってはいけない。** 比較の意味が消える。"""
    res = result_of({"基準 常に上（ドリフト）": [9, 9, 9, 9, 9],
                     "全部使う（基準）": [8, 8, 8, 8, 8],
                     "F1-2 相互情報量": [1, 1, 1, 1, 1]})
    assert checks.best_method(summary_of(res)) == "F1-2 相互情報量"


def test_best_is_the_highest_net_not_the_highest_gross():
    res = result_of({"F1-1 相関": [1, 1, 1, 1, 1], "F1-2 相互情報量": [2, 2, 2, 2, 2]})
    assert checks.best_method(summary_of(res)) == "F1-2 相互情報量"


# --- fold の符号 --------------------------------------------------------

def test_fold_signs_are_reported_even_when_the_mean_is_positive():
    """⚠ **平均が正でも 2/5 なら実力ではない**（rules.md 11 章 規約 5）。"""
    res = result_of({"F1-2 相互情報量": [19.52, -5.0, -1.49, 0.71, -2.25],
                     "基準 常に上（ドリフト）": [0, 0, 0, 0, 0]})
    doc = checks.compute(res, summary_of(res), CONFIG)
    assert doc["best"]["純利bp"] > 0
    assert doc["folds"]["positive"] == 2 and doc["folds"]["folds"] == 5
    assert doc["folds"]["pattern"] == "＋−−＋−"


def test_edge_over_drift_matches_a_hand_calculation():
    net = [10.0, 8.0, -2.0, -4.0, 3.0]
    drift = [5.0, 5.0, 5.0, 5.0, 5.0]
    res = result_of({"F1-1 相関": net, "基準 常に上（ドリフト）": drift})
    doc = checks.compute(res, summary_of(res), CONFIG)
    d = np.array([n - x for n, x in zip(net, drift)])          # 粗利は純利 + 5 で共通
    want = float(d.mean() / (d.std(ddof=1) / np.sqrt(5)))
    assert doc["edge_vs_drift"]["mean_bp"] == pytest.approx(d.mean(), abs=1e-3)
    assert doc["edge_vs_drift"]["t"] == pytest.approx(want, abs=1e-3)


def test_t_is_absent_when_every_fold_is_identical():
    """⚠ **散らばりが 0 のときに無限大の t を書かない。**"""
    res = result_of({"F1-1 相関": [1, 1, 1, 1, 1], "基準 常に上（ドリフト）": [0, 0, 0, 0, 0]})
    doc = checks.compute(res, summary_of(res), CONFIG)
    assert doc["edge_vs_drift"]["t"] is None


# --- パネルが要る検査 ---------------------------------------------------

def test_panel_checks_are_omitted_without_a_panel():
    """⚠ **埋められない値を 0 や null で埋めない。** 鍵ごと省く。"""
    res = result_of({"F1-1 相関": [1, 1, 1, 1, 1], "基準 常に上（ドリフト）": [0, 0, 0, 0, 0]})
    doc = checks.compute(res, summary_of(res), CONFIG, panel=None, n_trials=36)
    assert "breadth" not in doc and "dsr" not in doc


def _panel(n_ts=600, n_sym=10, seed=0):
    rng = np.random.default_rng(seed)
    common = rng.normal(0, 0.01, n_ts)          # 共通因子（系列を独立でなくする）
    rows = []
    ts = pd.date_range("2020-01-01", periods=n_ts, freq="D", tz="UTC")
    for i in range(n_sym):
        y = common + rng.normal(0, 0.005, n_ts)
        rows.append(pd.DataFrame({"symbol": f"S{i}", "ts": ts, "y": y}))
    return pd.concat(rows, ignore_index=True)


def test_effective_breadth_is_smaller_than_the_series_count():
    """⚠ **同じ市場の銘柄は独立ではない。** 系列数をそのまま標本数にしない。"""
    res = result_of({"F1-1 相関": [1, 1, 1, 1, 1], "基準 常に上（ドリフト）": [0, 0, 0, 0, 0]})
    doc = checks.compute(res, summary_of(res), {**CONFIG, "horizon_min": 1440.0,
                                                "bar_minutes": 1440.0},
                         panel=_panel(), n_trials=36)
    b = doc["breadth"]
    assert b["系列数"] == 10
    assert 1.0 < b["実効系列数"] < 10.0
    assert b["実効観測数"] < b["検証の行"]        # ⚠ 行数より必ず小さい


def test_validated_rows_are_fewer_than_the_panel():
    """⚠ **成績を測ったのは検証に回った行だけ。** パネル全体で数えると標本を水増しする。"""
    panel = _panel()
    n = checks.test_rows(panel, {**CONFIG, "horizon_min": 1440.0, "bar_minutes": 1440.0})
    assert 0 < n < len(panel)
    assert n == pytest.approx(len(panel) * 5 / 6, rel=0.02)


def test_dsr_records_the_trial_count_it_used():
    """⚠ **試行数は増えていく。** どの試行数で出した DSR かを残さないと後から読めない。"""
    res = result_of({"F1-1 相関": [1, 1, 1, 1, 1], "基準 常に上（ドリフト）": [0, 0, 0, 0, 0]})
    doc = checks.compute(res, summary_of(res), {**CONFIG, "horizon_min": 1440.0,
                                                "bar_minutes": 1440.0},
                         panel=_panel(), n_trials=36)
    assert doc["dsr"]["n_trials"] == 36
    assert 0.0 <= doc["dsr"]["DSR"] <= 1.0
    assert "基準線を超えたかの検定ではない" in doc["dsr"]["注記"]


def test_leak_runs_are_marked():
    """⚠ **先読みの実行が「ただ成績の良い検証」に見えてはいけない。**"""
    res = result_of({"F1-1 相関": [116.0] * 5, "基準 常に上（ドリフト）": [0] * 5})
    doc = checks.compute(res, summary_of(res), CONFIG, leak=True)
    assert doc["leak"] is True and doc["best"]["純利bp"] > 100


# --- 試行数の数え方（2026-09-12。⚠ 二重計上を直した） ----------------------

def test_the_ledger_already_counts_the_run_that_just_wrote_its_summary(tmp_path, monkeypatch):
    """⚠ **`summary.csv` を書いた時点で、台帳はもうこの実行を数えている。**

    だから `n_trials_now` に「この実行ぶん」を足してはいけない（足すと二重になる。
    2026-09-12 までそうなっていた。validation-power.md §8-2-5）。
    """
    import json

    from ail import catalog, runs

    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    count = lambda: len([r for r in catalog.trials()[0] if catalog.is_trial(r)])  # noqa: E731
    before = count()

    d = tmp_path / "2026-01-01T00-00-00_x"
    d.mkdir()
    (d / "config.json").write_text(json.dumps(
        {"model": "Ridge", "bar_minutes": 1440.0, "horizon": 1, "feature_layers": ["own"],
         "trading": {"style": "threshold", "thresholds": [50]}}), encoding="utf-8")
    (d / "summary.csv").write_text("手法,閾値,純利bp\n全部使う（基準）,50.0,1.0\n", encoding="utf-8")

    assert count() == before + 1                      # ⚠ 置いただけで台帳が数える
    assert checks.n_trials_now() == before + 1        # ⚠ 足さないのが正しい


def test_n_trials_now_matches_the_ledger():
    """⚠ **正本は台帳。** `checks.json` に残るのは「そのときの台帳の数」そのものである。"""
    from ail import catalog

    assert checks.n_trials_now() == len([r for r in catalog.trials()[0] if catalog.is_trial(r)])


def test_n_trials_now_refuses_an_extra():
    """⚠ **同じ間違いを書けなくする。** 「この実行ぶん」を渡す引数はもう無い。"""
    with pytest.raises(TypeError):
        checks.n_trials_now(3)


# --- 保有日数・逆売買・選日の上乗せ（2026-09-17。⚠ どれも診断で、採否には使わない） ---------------

def test_holding_summary_matches_a_hand_calculation():
    holds = pd.DataFrame({"手法": ["A"] * 6 + ["B"], "閾値": [50.0] * 7,
                          "fold": 1, "銘柄": "X", "建てた日": "2021-01-01",
                          "保有日数": [1, 2, 2, 5, 10, 40, 3],
                          "強制清算": [False, False, False, False, False, True, False]})
    h = checks._holding(holds, "A", 50.0)
    assert h["取引数"] == 6 and h["中央値"] == 3.5 and h["p25"] == 2.0 and h["p75"] == 8.75
    assert h["保有1日の割合"] == pytest.approx(1 / 6, abs=1e-4) and h["保有2日以下の割合"] == pytest.approx(0.5)
    assert h["強制清算の割合"] == pytest.approx(1 / 6, abs=1e-4) and h["中央値_強制清算除く"] == 2.0
    assert "採否" in h["注記"]
    assert checks._holding(holds, "C", 50.0) is None            # 取引 0 回は鍵を省く
    assert checks._holding(None, "A", 50.0) is None


def test_selection_edge_is_gross_minus_exposure_times_bh():
    res = pd.DataFrame([
        {"手法": "M", "fold": f, "閾値": 50.0, "粗利bp": g, "純利bp": g - 10.0, "保有日率": 0.5}
        for f, g in enumerate([60.0, 40.0, 50.0, 70.0, 30.0], 1)] + [
        {"手法": checks.DRIFT, "fold": f, "閾値": 50.0, "粗利bp": 100.0, "純利bp": 95.0, "保有日率": 1.0}
        for f in range(1, 6)])
    s = checks._selection_edge(res, "M", 50.0)
    assert s["values"] == [10.0, -10.0, 0.0, 20.0, -20.0]      # 粗利 − 0.5 × 100
    assert s["mean_bp"] == 0.0 and s["positive"] == 2 and s["保有日率"] == 0.5
    assert checks._selection_edge(res, "Z", 50.0) is None


def test_reverse_summary_reports_the_identity_gap():
    rows = []
    for f in range(1, 6):
        rows.append({"手法": "M", "fold": f, "閾値": 50.0, "粗利bp": 30.0, "純利bp": 20.0,
                     "逆売買純利bp": 60.0, "逆売買取引回数": 4})
        rows.append({"手法": checks.DRIFT, "fold": f, "閾値": 50.0, "粗利bp": 100.0, "純利bp": 95.0,
                     "逆売買純利bp": -5.0, "逆売買取引回数": 0})
    res = pd.DataFrame(rows)
    r = checks._reverse(res, pd.DataFrame(), "M", 50.0, 5.0)
    assert r["純利bp"] == 60.0 and r["取引回数"] == 4.0
    assert r["edge_vs_bh"]["mean_bp"] == -35.0                    # 60 − 95
    assert r["恒等式の上乗せbp"] == -35.0                          # −20 − 2 × 10 ＋ 5
    assert r["恒等式との差bp"] == 0.0
    assert checks._reverse(res.drop(columns=["逆売買純利bp"]), pd.DataFrame(), "M", 50.0, 5.0) is None
