"""登録簿 `20261101-deadzone-label`（サンドボックスの 1 本目・2026-11-13・rules.md 31-7）: 訓練の行のうち小さな値動きの行を学習から外す。

- 無い config は 1 ビットも変わらない（`deadzone_model` は同じオブジェクトを返す・検証方式の名前も変わらない）
- 外すのは学習の行だけ（予測する側の行 ＝ 較正の尻・評価の行は外さない）
- 識別項目 ＝ 検証方式の変種 `閾値売買（小動き除外a）` → 名前 `~deadzone-a`
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import catalog, names
from ail.models import calibrate
from ail.validation import prep


def _tr(n=400, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"y": rng.normal(0, 0.01, n), "own_vol_60": np.full(n, 0.01), "ts": np.arange(n)})


def test_no_option_returns_the_same_model_object():
    def model(Xa, ya, Xb, ctx):
        return np.zeros(len(Xb))
    assert prep.deadzone_model({}, model, _tr()) is model
    assert prep.deadzone_model({"label": "vol_scaled"}, model, _tr()) is model
    assert catalog.threshold_style({"trading": {"style": "threshold"}}) == "閾値売買"


def test_drops_only_small_moves_from_the_training_rows():
    tr = pd.DataFrame({"y": [0.001, -0.002, 0.004, -0.006, 0.02, 0.001, 0.001],
                       "own_vol_60": [0.01, 0.01, 0.01, 0.01, 0.01, np.nan, 0.0], "ts": range(7)})
    seen = {}

    def model(Xa, ya, Xb, ctx):
        seen["idx"], seen["y"], seen["nb"] = list(Xa.index), list(ya), len(Xb)
        return np.zeros(len(Xb))
    X = pd.DataFrame({"f": np.arange(7.0)})
    a = prep.deadzone_model({"label_deadzone": "a"}, model, tr)          # |y| < 0.0025 を外す ＝ 行 0・1
    a(X, tr["y"].values, X, {})
    assert seen["idx"] == [2, 3, 4, 5, 6] and seen["nb"] == 7              # σ が欠けた ／ 0 の行は外さない・予測する側は全部
    assert a.deadzone_dropped == pytest.approx(2 / 7)
    b = prep.deadzone_model({"label_deadzone": "b"}, model, tr)          # |y| < 0.005 を外す ＝ 行 0・1・2
    b(X.iloc[:5], tr["y"].values[:5], X.iloc[5:], {})                      # 頭だけ渡しても同じ行が外れる（較正の holdout）
    assert seen["idx"] == [3, 4] and seen["y"] == [-0.006, 0.02] and seen["nb"] == 2
    with pytest.raises(SystemExit):
        prep.deadzone_model({"label_deadzone": "c"}, model, tr)
    with pytest.raises(SystemExit):
        prep.deadzone_model({"label_deadzone": "a"}, model, tr.drop(columns="own_vol_60"))
    with pytest.raises(SystemExit):                                         # 行の対応が取れない index は止める
        a(X.set_index(X.index + 100), tr["y"].values, X, {})


def test_calibration_tail_is_not_filtered():
    """較正の holdout: 学習（頭）は外した行で・予測（尻）は全部の行で。"""
    tr = _tr(2000)
    X = pd.DataFrame({"f": np.random.default_rng(1).normal(size=2000)})
    calls = []

    def model(Xa, ya, Xb, ctx):
        calls.append((len(Xa), len(Xb)))
        return np.asarray(Xb["f"]) * 0.0
    wrapped = prep.deadzone_model({"label_deadzone": "b"}, model, tr)
    calibrate.fit(wrapped, X, tr["y"].values, {})
    (head_plain, tail_plain), = [(len(h[0]), len(t[0])) for h, t in [calibrate.tail_holdout(X, tr["y"].values)]]
    assert calls[0][1] == tail_plain and calls[0][0] < head_plain            # 尻はそのまま・頭は減る


def test_key_and_name():
    for k in ("a", "b"):
        style = catalog.threshold_style({"trading": {"style": "threshold"}, "label_deadzone": k})
        assert style == f"閾値売買（小動き除外{k}）" and catalog.is_threshold(style)
        key = dict(鍵="全部使う（基準）", モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own",
                   層="adjusted", 期間="2018-01-31", 銘柄="63", 検証方式=style, 形式="共通", 較正="std", 閾値="50")
        assert names.trial_name(key) == f"own.all.ridge.shared~deadzone-{k}@50"
