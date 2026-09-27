"""買う線と売る線を別に置く（rules.md 18 章・[記録](../../../docs/specs/experiments/forward10-target.md) §7）。

⚠ **ここで落としたいのは 4 つ。**
  1. `simulate` の `exit_threshold` を省いた経路が、足す前と 1 ビットも変わらないこと
  2. 買う線 60・売る線 50 の意味（買い% > 60 で買い・買い% < 50 で売る・あいだは持ち続ける）
  3. `threshold_pairs` の無い config では `evaluate_trading` の行が 1 つも変わらず、ある config では既存の行がそのままで
     `〔売り線N〕` の行が後ろに足されること（上位 K と同じ形）
  4. 組の検証（買い線が thresholds に無い ／ 売り線が 50 未満 ／ 買い線と同じ）は止まること・名前の綴り
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import names, runs
from ail.validation import simulate as sim
from cli import run as cli_run
from cli.run import evaluate_trading
from tests.test_fwd import _exp, _feats, _panel
import ail.bootstrap  # noqa: F401


def _series(seed=0, n=400):
    rng = np.random.default_rng(seed)
    return rng.uniform(30, 70, n), rng.normal(0.0002, 0.01, n)


# --- 1. 既定は同一 ---------------------------------------------------------

def test_default_path_is_unchanged():
    b, y = _series()
    for th in (50.0, 55.0, 60.0):
        a = sim.simulate(b, y, th, 5.0)
        c = sim.simulate(b, y, th, 5.0, exit_threshold=None)
        d = sim.simulate(b, y, th, 5.0, exit_threshold=th)          # 同じ線 ＝ 対称の形
        for k in ("net_bp", "pos"):
            assert np.array_equal(a[k], c[k]) and np.array_equal(a[k], d[k])
        assert a["trades"] == c["trades"] == d["trades"]


# --- 2. 意味 ----------------------------------------------------------------

def test_buy_above_entry_line_and_sell_below_exit_line():
    """買う線 60・売る線 50: 買い% > 60 で建て、買い% < 50（＝ 出口% > 50）で手仕舞い、50〜60 は持ち続ける。"""
    b, y = _series(1)
    r = sim.simulate(b, y, 60.0, 5.0, exit_threshold=50.0)
    p, want = 0, []
    for x in b:
        if p == 0 and x > 60.0:
            p = 1
        elif p == 1 and (100.0 - x) > 50.0:
            p = 0
        want.append(p)
    assert np.array_equal(r["pos"], np.array(want))
    # ⚠ 対称の θ=60（売る線 40）より早く降り、θ=50 より遅く乗る ＝ 保有日率はあいだ
    assert sim.simulate(b, y, 60.0, 5.0)["hold_ratio"] >= r["hold_ratio"]
    assert r["trades"] >= sim.simulate(b, y, 60.0, 5.0)["trades"]


def test_exit_line_below_50_is_refused():
    b, y = _series()
    with pytest.raises(ValueError, match="exit_threshold"):
        sim.simulate(b, y, 60.0, 5.0, exit_threshold=40.0)


# --- 3. evaluate_trading ---------------------------------------------------

@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_exit_line", {}, seed=0)


def _with_pairs(exp, pairs):
    return {**exp, "trading": {**exp["trading"], "threshold_pairs": pairs}}


def test_existing_rows_do_not_change_and_pair_rows_are_appended(run):
    p = _panel()
    base_exp = _exp()
    res0, sym0, sum0, daily0, extra0 = evaluate_trading(p, _feats(p), base_exp, run)
    res1, sym1, sum1, daily1, extra1 = evaluate_trading(p, _feats(p), _with_pairs(base_exp, [[60, 50], [55, 50]]), run)
    plain = res1[~res1["手法"].str.contains("〔売り線")].reset_index(drop=True)
    pd.testing.assert_frame_equal(res0, plain)                      # ⚠ 既存の行は 1 ビットも変わらない
    added = res1[res1["手法"].str.contains("〔売り線")]
    assert set(added["手法"]) == {f"{base_exp['detectors'][0]}〔売り線50〕"}
    assert set(added["閾値"]) == {60.0, 55.0}                       # 閾値の列は買う線
    assert not any(n.startswith("基準 ") for n in added["手法"])    # 基準線は通さない
    assert (f"{base_exp['detectors'][0]}〔売り線50〕", 60.0) in daily1
    assert "〔売り線50〕" in " ".join(extra1["holds"]["手法"].unique())
    # 対称の θ=60 より保有日率が低く、取引が多い（売る線が 40 → 50 に上がる）
    sym60 = res1[(res1["手法"] == base_exp["detectors"][0]) & (res1["閾値"] == 60.0)]
    pair60 = added[added["閾値"] == 60.0]
    assert pair60["保有日率"].mean() <= sym60["保有日率"].mean()
    assert pair60["取引回数"].sum() >= sym60["取引回数"].sum()


# --- 4. 組の検証と綴り ------------------------------------------------------

@pytest.mark.parametrize("pairs,msg", [
    ([[65, 50]], "thresholds に無い"),
    ([[60, 40]], "50 以上"),
    ([[60, 60]], "別の値"),
])
def test_bad_pairs_stop(pairs, msg):
    with pytest.raises(SystemExit, match=msg):
        cli_run.exit_line_pairs({"threshold_pairs": pairs}, [50.0, 55.0, 60.0])


def test_no_pairs_is_empty():
    assert cli_run.exit_line_pairs({}, [50.0]) == []
    assert cli_run.exit_line_name("H1 先10日ゲート（全列・学習）", 50.0) == "H1 先10日ゲート（全列・学習）〔売り線50〕"


def test_name_spelling_puts_the_exit_line_in_the_method_and_theta_is_the_entry_line():
    row = {"鍵": "H1 先10日ゲート（全列・学習）〔売り線50〕", "モデル": "Ridge", "粒度": "日足", "地平": "1 本（1 日）",
           "特徴量の層": "own cs rel ex", "層": "adjusted", "期間": "2018-01-31", "銘柄": "48",
           "検証方式": "閾値売買", "形式": "共通", "較正": "std", "閾値": "60"}
    assert names.trial_name(row) == "own-cs-rel-ex.h1-gate10-out50.ridge.shared~n48@60"
