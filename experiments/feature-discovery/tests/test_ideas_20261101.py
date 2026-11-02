"""登録簿 2026-11-01 の 2 本: 夜間 ／ 日中の層 `gap_`（`ail/ideas/i20261101_gap_layer.py`）とボラ割りラベル（`prep.train_label`）。

⚠ 見るもの: 無い config は 1 ビットも変わらない ／ 足 i の値は足 i までで決まる ／ 列の中身 ／ ラベル ＝ y ÷ own_vol_60・欠けた σ は中央値・識別項目。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import catalog, names, registry
from ail.ideas import i20261101_gap_layer as gap
from ail.validation import prep
from cli import build


def _df(n=80, seed=0):
    rng = np.random.default_rng(seed)
    c = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = c * np.exp(rng.normal(0, 0.005, n))
    return pd.DataFrame({"ts": pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC"), "open": o, "close": c})


def test_gap_layer_is_opt_in_and_correct():
    assert "gap" in registry.available("feature") and "gap" not in build.ORDER
    assert build.layer_order(["own"]) == ["own"]
    df = _df()
    x = gap.gap_one(df)
    on = np.log(df["open"] / df["close"].shift(1))
    iday = np.log(df["close"] / df["open"])
    assert x["on_1"].iloc[10] == pytest.approx(on.iloc[10]) and x["id_5"].iloc[30] == pytest.approx(iday.iloc[26:31].sum())
    assert x["on_1"].iloc[1] + x["id_1"].iloc[1] == pytest.approx(np.log(df["close"].iloc[1] / df["close"].iloc[0]))   # 夜間 ＋ 日中 ＝ 1 日
    assert ((x["opp_20"].dropna() >= 0) & (x["opp_20"].dropna() <= 1)).all()
    out = registry.resolve("feature", "gap")({"A": df}, {})
    assert list(out["A"].columns) == [f"gap_{c}" for c in gap.COLUMNS]


def test_gap_layer_no_lookahead():
    df = _df()
    full = gap.gap_one(df)
    part = gap.gap_one(df.iloc[:50])
    pd.testing.assert_frame_equal(full.iloc[:50], part)


def test_vol_scaled_label():
    tr = pd.DataFrame({"y": [0.02, -0.01, 0.03, 0.0], "own_vol_60": [0.01, 0.02, np.nan, 0.0], "ts": [1, 1, 2, 2]})
    assert np.array_equal(prep.train_label({}, tr), tr["y"].values)                   # 無い config は y のまま
    got = prep.train_label({"label": "vol_scaled"}, tr)
    med = 0.015                                                                         # 正の σ の中央値
    assert got == pytest.approx([2.0, -0.5, 0.03 / med, 0.0])
    with pytest.raises(SystemExit):
        prep.train_label({"label": "vol_scaled"}, tr.drop(columns="own_vol_60"))
    style = catalog.threshold_style({"trading": {"style": "threshold"}, "label": "vol_scaled"})
    assert style == "閾値売買（ボラ割りラベル）" and catalog.is_threshold(style)
    key = dict(鍵="全部使う（基準）", モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own",
               層="adjusted", 期間="2018-01-31", 銘柄="63", 検証方式=style, 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key) == "own.all.ridge.shared~vol-label@50"


# ---------- 枠の外 2 本（2026-11-01 契約 2 の段 1）: 急落を当てる出口 ／ 持つ量を段階に

from ail.ideas import i20261101_crash_exit as crash          # noqa: E402
from ail.validation import simulate as sim                   # noqa: E402


def test_crash_label_uses_only_the_next_five_rows_inside_training():
    ts = pd.date_range("2020-01-01", periods=12, freq="B", tz="UTC")
    y = np.full(12, 0.001)
    y[6] = -0.2                                               # 7 本目の y が大きく下げる
    tr = pd.DataFrame({"symbol": "A", "ts": ts, "y": y}).iloc[::-1]   # 並びは逆でも行で返す
    lab, use, doc = crash.crash_label(tr)
    got = pd.Series(lab, index=tr.index).sort_index()
    ok = pd.Series(use, index=tr.index).sort_index()
    assert list(got[:7]) == [0, 0, 1, 1, 1, 1, 1]             # 行 t は y_t〜y_{t+4} を見る（行 2〜6 が 6 を含む）
    assert not ok.iloc[-4:].any() and ok.iloc[:8].all()       # 先の 4 本が無い尻は使わない


def test_crash_detector_is_registered_and_named():
    assert crash.NAME in registry.available("detector")
    assert names.method_slug(crash.NAME) == "x5-exit-crash5"


def test_graded_sizing_weights_costs_and_name():
    b = np.array([40.0, 60.0, 80.0, 100.0, 50.0])
    y = np.array([0.01, 0.01, 0.01, 0.01, 0.01])
    r = sim.simulate_graded(b, y, 50.0, cost_bp=5.0)
    w = np.array([0.0, 0.4, 1.0, 1.0, 0.0])
    assert np.allclose(r["pos"], w)
    moved = np.abs(np.diff(np.concatenate([[0.0], w])))
    assert np.allclose(r["net_bp"], w * 100 - 2.5 * moved) and r["trades"] == 1 and r["hold_days"] == [3]
    assert names.method_slug("全部使う（基準）〔量を段階に〕") == "all-graded"
    # 全部 100 なら持ちっぱなし ＝ 閾値売買で持ちっぱなしと同じ純利（費用は入りと末尾の清算）
    full = sim.simulate_graded(np.full(5, 100.0), y, 50.0)
    thr = sim.simulate(np.full(5, 100.0), y, 50.0)
    assert np.allclose(full["net_bp"], thr["net_bp"])
