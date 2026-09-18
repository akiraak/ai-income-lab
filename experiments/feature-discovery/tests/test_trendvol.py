"""`trendvol` 層（出来高の長い窓。[プラン](../../../docs/plans/archive/volume-trend-input.md) §4）。

⚠ **落としたいのは 4 つ**: 行数と並びが入力と同じ ／ 窓が i で閉じる（先読み無し）／ 出来高 0 が NaN ／
`scale_columns` が窓 W の列を trend 6 ＋ trendvol 4 ＝ 10 本拾い、trendvol の無い表では 6 本のまま。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import registry
from ail.features import trend, trendvol
import ail.bootstrap  # noqa: F401

WINDOWS = (5, 10, 20)      # ⚠ 本物の窓（20/60/200）は合成の足では長すぎるので、形だけ同じ短い窓で通す


def _bars(n=300, seed=0, zero_at=None):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0003, 0.012, n)
    close = 100 * np.exp(np.cumsum(r))
    vol = rng.lognormal(15.0, 0.4, n)
    if zero_at is not None:
        vol[zero_at] = 0.0
    ts = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"ts": ts, "open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": vol})


def test_same_rows_and_order_as_input():
    b = _bars()
    x = trendvol.build_one(b, WINDOWS)
    assert len(x) == len(b) and (x.index == b.index).all()
    assert list(x.columns) == [c for w in WINDOWS for c in trendvol.columns(w)]
    assert all(c.startswith(f"{trend.PREFIX}trend") for c in x.columns)   # ⚠ scale_columns が拾う名前


def test_windows_close_at_i_no_lookahead():
    """⚠ **末尾を切っても前の値が 1 つも変わらない**（先読みが無い。rules.md 7 章）。"""
    b = _bars()
    full = trendvol.build_one(b, WINDOWS)
    cut = trendvol.build_one(b.iloc[:200].reset_index(drop=True), WINDOWS)
    pd.testing.assert_frame_equal(full.iloc[:200].reset_index(drop=True), cut)


def test_zero_volume_day_is_nan_not_inf():
    """⚠ **出来高 0 の日は log で −∞ になる**ので NaN にする（表に inf を入れない）。"""
    b = _bars(zero_at=150)
    x = trendvol.build_one(b, WINDOWS)
    assert not np.isinf(x.to_numpy(dtype=float)).any()
    for w in WINDOWS:
        assert np.isnan(x.loc[150, f"{trend.PREFIX}trend{w}_vratio"])
        assert np.isnan(x.loc[150, f"{trend.PREFIX}trend{w}_vslope"])
    # 窓を抜けた先では値が戻る
    assert np.isfinite(x.loc[250, f"{trend.PREFIX}trend20_vratio"])


def test_scaling_volume_does_not_move_the_ratios():
    """⚠ **分割調整の向き**: 調整済みの表（価格 ÷ k・出来高 × k）を読めば比の列は跳ねない。
    比（vratio・updown・vslope）は出来高の定数倍で不変、dollar は価格 ÷ k × 出来高 × k で不変。"""
    b = _bars()
    k = 10.0
    b2 = b.copy()
    for col in ("open", "high", "low", "close"):
        b2[col] = b[col] / k
    b2["volume"] = b["volume"] * k
    x, x2 = trendvol.build_one(b, WINDOWS), trendvol.build_one(b2, WINDOWS)
    pd.testing.assert_frame_equal(x.dropna(), x2.dropna(), check_exact=False, atol=1e-9)


def test_scale_columns_picks_ten_with_trendvol_and_six_without():
    """`trend.scale_columns` は窓 W の列を、trendvol があれば 10 本、無ければ 6 本拾う（プラン §4）。"""
    b = _bars()
    t = trend.build_one(b, WINDOWS)
    tv = trendvol.build_one(b, WINDOWS)
    both = pd.concat([t, tv], axis=1)
    for w in WINDOWS:
        assert len(trend.scale_columns(list(t.columns), w)) == 6
        assert len(trend.scale_columns(list(both.columns), w)) == 10
    # ⚠ 20 と 200 のように片方が他方の接頭辞になる窓でも混ざらない
    wide = trendvol.build_one(_bars(n=400), (20, 200))
    assert len(trend.scale_columns(list(wide.columns), 20)) == 4


def test_layer_is_registered_and_uses_the_same_windows_as_trend():
    fn = registry.resolve("feature", "trendvol")
    out = fn({"S0": _bars(n=250)}, {"trend_windows": WINDOWS})
    assert set(out) == {"S0"} and out["S0"].shape[1] == 4 * len(WINDOWS)
    assert trendvol.WINDOWS == trend.WINDOWS           # ⚠ 数値は手で置かない（14-9）
