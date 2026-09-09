"""`ex_` 層。⚠ **この層で一番危ないのは「発表の遅れ」である。**

⚠ **その日のデータがその日に手に入るとは限らない。** ずらし忘れると、
⚠ **知り得なかった値で過去を当てにいくことになる**（先読みの一種）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail.features import exog


def series(start="2020-01-01", n=40, step=1.0):
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.Series(np.arange(n, dtype=float) * step, index=idx, name="S1")


def bars(dates):
    ts = pd.to_datetime(dates, utc=True)
    return pd.DataFrame({"ts": ts, "close": np.arange(len(ts), dtype=float)})


def panel_of(dates):
    return {"AAA": bars(dates)}


def layer(panel, wide, source="ecb", **ctx):
    """`load_series` を差し替えて層を回す（取得済みのデータに依存させない）。

    ⚠ **返り値は (系列, 系列 → 取得元)。** 取得元はずらし幅を引くのに要る。
    """
    import ail.features.exog as m
    orig = m.load_series
    m.load_series = lambda *a, **k: (wide, {sid: source for sid in wide})
    try:
        return m.layer(panel, ctx)
    finally:
        m.load_series = orig


# --- ずらし（この層の存在理由）-------------------------------------------

def test_the_value_used_is_from_before_the_bar():
    """⚠ **足の日の値を使ってはいけない。** 既定は 1 日前。"""
    s = {"S1": series()}
    p = panel_of(["2020-01-10", "2020-01-11", "2020-01-12"])
    out = layer(p, s, ex_transforms=("lvl",))["AAA"]
    # 系列は 2020-01-01 を 0 とする連番なので、1/10 の 1 日前 = 1/9 = 8.0
    assert list(out["ex_S1_lvl"]) == [8.0, 9.0, 10.0]


def test_a_longer_lag_shifts_further():
    s = {"S1": series()}
    p = panel_of(["2020-01-10"])
    assert layer(p, s, ex_lag_days=3, ex_transforms=("lvl",))["AAA"]["ex_S1_lvl"].iloc[0] == 6.0


@pytest.mark.parametrize("lag", [0, -1])
def test_zero_or_negative_lag_is_refused(lag):
    """⚠ **ずらさない設定を許すと、この層の意味が消える。**"""
    with pytest.raises(ValueError, match="1 以上"):
        layer(panel_of(["2020-01-10"]), {"S1": series()}, ex_lag_days=lag)


def test_future_values_never_leak_in():
    """⚠ **系列の未来を切り落としても、過去の行は 1 つも変わらない**（rules.md 7 章の検査）。"""
    s = series(n=40)
    p = panel_of(["2020-01-10", "2020-01-15", "2020-01-20"])
    full = layer(p, {"S1": s}, ex_transforms=("lvl", "d1"))["AAA"]
    cut = layer(p, {"S1": s[s.index <= "2020-01-21"]}, ex_transforms=("lvl", "d1"))["AAA"]
    pd.testing.assert_frame_equal(full, cut)


# --- 休みの日の埋め方 ---------------------------------------------------

def test_a_gap_carries_the_last_published_value():
    """⚠ **休場で発表が無い日は、前の値が「その時点で公表されている最新の値」である。**

    ⚠ **これは未来を見ていない**（`asof` は指定日以前しか見ない）。
    """
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2020-01-01", "2020-01-06"]), name="S1")
    p = panel_of(["2020-01-04", "2020-01-08"])     # 1/4 の前日 = 1/3（1/1 の値）、1/8 の前日 = 1/7（1/6 の値）
    out = layer(p, {"S1": s}, ex_transforms=("lvl",))["AAA"]
    assert list(out["ex_S1_lvl"]) == [1.0, 2.0]


def test_zero_filled_sources_do_not_carry_the_previous_day(tmp_path, monkeypatch):
    """⚠ **地震は「行が無い日 = 0 件」。** 前の値を引きずると、起きなかった日に前日の件数が入る。"""
    from ail.data import store
    d = tmp_path / "raw" / "usgs" / "series"
    d.mkdir(parents=True)
    # 1/1 に 5 件、1/3 に 2 件。⚠ 1/2 は行が無い（＝ 0 件）
    ms = [int(pd.Timestamp(x, tz="UTC").timestamp() * 1000) for x in ("2020-01-01", "2020-01-03")]
    (d / "EQ_COUNT.csv").write_text(f"time_ms,value\n{ms[0]},5\n{ms[1]},2\n", encoding="utf-8")
    monkeypatch.setattr(store, "series_dir", lambda src: str(d))
    got = exog.load_series(sources=("usgs",), zero_fill=("usgs",))[0]["EQ_COUNT"]
    assert list(got.values) == [5.0, 0.0, 2.0]        # ⚠ 1/2 は 0（5 ではない）


def test_sources_without_zero_fill_keep_their_gaps(tmp_path, monkeypatch):
    """⚠ **為替は休場で行が無いだけ。** 0 で埋めるとレートが 0 になる。"""
    from ail.data import store
    d = tmp_path / "raw" / "ecb" / "series"
    d.mkdir(parents=True)
    ms = [int(pd.Timestamp(x, tz="UTC").timestamp() * 1000) for x in ("2020-01-01", "2020-01-03")]
    (d / "EURTOUSD.csv").write_text(f"time_ms,value\n{ms[0]},1.1\n{ms[1]},1.2\n", encoding="utf-8")
    monkeypatch.setattr(store, "series_dir", lambda src: str(d))
    got = exog.load_series(sources=("ecb",), zero_fill=("usgs",))[0]["EURTOUSD"]
    assert list(got.values) == [1.1, 1.2] and len(got) == 2      # 1/2 の行は作らない


# --- 変換 ---------------------------------------------------------------

def test_level_is_not_a_default_transform():
    """⚠ **水準は非定常。** 既定に入れない（明示したときだけ使う）。"""
    assert "lvl" not in exog.DEFAULT_TRANSFORMS
    cols = exog.transform(series()).columns
    assert "lvl" not in cols and "d1" in cols


def test_rolling_window_closes_on_the_day():
    """⚠ **窓は当日で閉じる。** 先の値を含めると先読みになる。"""
    s = series(n=30)
    z = exog.transform(s, ("z20",))["z20"]
    assert z.iloc[:19].isna().all()          # 20 本たまるまでは出ない
    assert not np.isnan(z.iloc[19])


# --- 全銘柄で同じ値になること -------------------------------------------

def test_every_symbol_gets_the_same_value():
    """⚠ **市場全体の値なので銘柄で差が付かない。** ⚠ 断面では銘柄を区別できない。"""
    s = {"S1": series()}
    dates = ["2020-01-10", "2020-01-11"]
    p = {"AAA": bars(dates), "BBB": bars(dates)}
    out = layer(p, s, ex_transforms=("lvl",))
    assert list(out["AAA"]["ex_S1_lvl"]) == list(out["BBB"]["ex_S1_lvl"])


def test_prefix_is_registered():
    from ail.contracts import FEATURE_PREFIXES, layer_of
    assert "ex_" in FEATURE_PREFIXES
    assert layer_of("ex_EURTOUSD_d1") == "ex_"


def test_a_stopped_series_becomes_missing_rather_than_constant():
    """⚠ **系列が止まったら欠損にする。** ⚠ **古い値を引き継ぎ続けると定数の特徴量になる。**

    ⚠ **2026-09-09 に実データで踏んだ**（気象の取得が 09-01 で止まっていて、
    09-02 以降の行に同じ値が並んだ）。
    """
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2020-01-01", "2020-01-02"]), name="S1")
    p = panel_of(["2020-01-05", "2020-01-20"])     # 前者は 2 日前、後者は 17 日前の値になる
    out = layer(p, {"S1": s}, ex_transforms=("lvl",), ex_max_stale_days=7)["AAA"]
    assert out["ex_S1_lvl"].iloc[0] == 2.0         # 2 日前は許す
    assert np.isnan(out["ex_S1_lvl"].iloc[1])      # ⚠ 17 日前は欠損にする


def test_the_stale_limit_is_configurable():
    s = pd.Series([1.0], index=pd.to_datetime(["2020-01-01"]), name="S1")
    p = panel_of(["2020-01-20"])
    assert np.isnan(layer(p, {"S1": s}, ex_transforms=("lvl",),
                          ex_max_stale_days=7)["AAA"]["ex_S1_lvl"].iloc[0])
    assert layer(p, {"S1": s}, ex_transforms=("lvl",),
                 ex_max_stale_days=60)["AAA"]["ex_S1_lvl"].iloc[0] == 1.0


# --- 取得元ごとのずらし幅 -----------------------------------------------

def test_a_slow_source_is_shifted_further():
    """⚠ **NCEI Storm Events は 101 日遅れて公表される**（2026-09-09 の実測）。

    ⚠ **一律 1 日ずらしにすると、まだ公表されていない値を使うことになる。**
    """
    s = series(n=400, start="2020-01-01")
    p = panel_of(["2021-01-01"])
    fast = layer(p, {"S1": s}, source="ecb", ex_transforms=("lvl",))["AAA"]["ex_S1_lvl"].iloc[0]
    slow = layer(p, {"S1": s}, source="ncei_storm",
                 ex_transforms=("lvl",), ex_max_stale_days=400)["AAA"]["ex_S1_lvl"].iloc[0]
    assert fast - slow == pytest.approx(119.0)      # 既定 1 日 対 120 日


def test_the_source_lag_can_be_overridden():
    s = series(n=400, start="2020-01-01")
    p = panel_of(["2021-01-01"])
    got = layer(p, {"S1": s}, source="ncei_storm", ex_transforms=("lvl",),
                ex_source_lag_days={"ncei_storm": 30}, ex_max_stale_days=400)["AAA"]
    plain = layer(p, {"S1": s}, source="ecb", ex_transforms=("lvl",))["AAA"]
    assert plain["ex_S1_lvl"].iloc[0] - got["ex_S1_lvl"].iloc[0] == pytest.approx(29.0)


def test_sources_with_different_lags_are_both_present():
    """⚠ **ずらし幅が違う系列を混ぜても、列は全部そろう。**"""
    import ail.features.exog as m
    s = {"FAST": series(n=400, start="2020-01-01"), "SLOW": series(n=400, start="2020-01-01")}
    owner = {"FAST": "ecb", "SLOW": "ncei_storm"}
    orig = m.load_series
    m.load_series = lambda *a, **k: (s, owner)
    try:
        out = m.layer(panel_of(["2021-01-01"]), {"ex_transforms": ("lvl",),
                                                 "ex_max_stale_days": 400})["AAA"]
    finally:
        m.load_series = orig
    assert list(out.columns) == ["ex_FAST_lvl", "ex_SLOW_lvl"]
    assert out["ex_FAST_lvl"].iloc[0] > out["ex_SLOW_lvl"].iloc[0]     # 遅い側は古い値
