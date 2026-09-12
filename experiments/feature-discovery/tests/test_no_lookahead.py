"""⚠ **先読みが入っていないことの検査**（rules.md 7 章）。

⚠ **これが一番見つけにくい壊れ方である。** 破ると「良い数字」が出るので、疑われずに通ってしまう。

やり方は 1 つ。⚠ **足 i より先を消したデータで作った特徴量が、全期間で作ったものと一致するか。**
一致しなければ、⚠ **その特徴量は未来を見ている。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import contracts
from ail.features import cross, labels, leadlag, own, relative, trend


def bars(n=300, seed=0, start="2024-01-02"):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.011, n)))
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, 0.004, n)))
    l = np.minimum(o, c) * (1 - abs(rng.normal(0, 0.004, n)))
    ts = pd.date_range(start, periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"time_ms": ts.astype("int64") // 10**6, "open": o, "high": h,
                         "low": l, "close": c, "volume": rng.integers(1e6, 1e7, n).astype(float),
                         "ts": ts})


def panel(symbols=("SPY", "XLK", "AAPL", "MSFT", "NVDA", "JPM"), n=300):
    return {s: bars(n, seed=i) for i, s in enumerate(symbols)}


CTX = {"market": "SPY", "sector_of": {"AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK"},
       "etf": ["SPY", "XLK"], "ll_leaders": "etf", "ll_lags": (1, 2)}


def _with_own(p):
    ctx = dict(CTX)
    ctx["own"] = own.layer(p, ctx)
    return ctx


@pytest.mark.parametrize("layer_name", ["own", "trend", "cs", "rel", "ll"])
def test_truncating_the_future_does_not_change_the_past(layer_name):
    """⚠ **未来を切り落としても、過去の特徴量は 1 つも変わってはいけない。**"""
    p = panel(n=600)
    cut = 400
    ctx_full = _with_own(p)
    short = {s: df.iloc[:cut].reset_index(drop=True) for s, df in p.items()}
    ctx_short = _with_own(short)

    fn = {"own": own.layer, "trend": trend.layer, "cs": cross.layer,
          "rel": relative.layer, "ll": leadlag.layer}[layer_name]
    full = fn(p, ctx_full)
    part = fn(short, ctx_short)
    for s in p:
        a = full[s].iloc[:cut].to_numpy(dtype=float)
        b = part[s].to_numpy(dtype=float)
        both = ~(np.isnan(a) | np.isnan(b))
        assert np.allclose(a[both], b[both], rtol=1e-9, atol=1e-12), f"{layer_name} / {s} が未来を見ている"
        assert (np.isnan(a) == np.isnan(b)).all(), f"{layer_name} / {s} の欠損の入り方が違う"


def test_label_is_the_only_thing_that_sees_the_future():
    """⚠ **ラベルだけが未来を見てよい**（rules.md 8 章）。"""
    df = bars()
    y = labels.build_one(df, horizon=3)
    expect = np.log(df["close"].shift(-3)) - np.log(df["close"])
    assert np.allclose(y["y"].dropna(), expect.dropna())
    assert y["y"].iloc[-3:].isna().all()          # ⚠ 末尾 k 本はラベルが取れない


def test_scale_labels_look_forward_and_are_never_features():
    """⚠ **スケールのラベルは未来を見る。だからこそ説明変数に入ってはいけない**（プラン §2-2）。"""
    df = bars(n=400)
    y = labels.build_scales(df, (20, 60, 200))
    lc = np.log(df["close"])
    for w in (20, 60, 200):
        col = f"{labels.SCALE_PREFIX}{w}"
        assert np.allclose(y[col].dropna(), (lc.shift(-w) - lc).dropna())
        assert y[col].iloc[-w:].isna().all()        # ⚠ 末尾 W 本はラベルが取れない
        # ⚠ **説明変数から必ず外れること**（外れないと学習の対象がそのまま入力になる）
        assert contracts.is_meta(col)
        assert col not in contracts.feature_columns(y.assign(**{"x_dummy": 1.0}))


def test_scale_labels_leak_columns_exist_per_scale():
    """⚠ **leak 対照はスケールごとに要る**（1 日先の答えは 200 日先の符号をほとんど教えない）。"""
    df = bars(n=400)
    y = labels.build_scales(df, (20, 200), leak=True)
    for w in (20, 200):
        leak_col = f"{labels.LEAK_SCALE_PREFIX}{w}"
        assert leak_col in y
        assert not contracts.is_meta(leak_col)      # ⚠ leak 列は「わざと入れる説明変数」
        assert leak_col in trend.scale_columns(list(y.columns) + ["own_trend%d_dist" % w], w)


def test_leadlag_is_shifted():
    """⚠ **`ll_` は必ず 1 本以上ずらす**（同じ足は `cs_` `rel_` の担当）。"""
    p = panel()
    ctx = _with_own(p)
    out = leadlag.layer(p, ctx)["AAPL"]
    spy = ctx["own"]["SPY"]["own_ret_1"]
    assert np.allclose(out["ll_SPY_ret1_lag1"].dropna(), spy.shift(1).dropna())
    assert "ll_AAPL_ret1_lag1" not in out         # ⚠ 自分自身は own_ の担当


def test_cross_section_does_not_forward_fill():
    """⚠ **時刻の完全一致だけで揃える**（rules.md 4 章 規約 5・7 章 規約 2）。

    ⚠ **1 銘柄だけ足を間引くと、その銘柄が居ない時刻の断面には自分の値が入らない**（NaN のまま）。
    """
    p = panel()
    p["NVDA"] = p["NVDA"].iloc[::2].reset_index(drop=True)      # 1 日おきに休場させる
    ctx = _with_own(p)
    cs = cross.layer(p, ctx)
    missing = set(p["AAPL"]["ts"]) - set(p["NVDA"]["ts"])
    assert missing
    # NVDA が居ない時刻でも、他の銘柄の断面は作れる（銘柄数は 1 本減る）
    n_at = pd.Series(cs["AAPL"]["cs_n"].values, index=p["AAPL"]["ts"].values)
    assert (n_at.reindex(sorted(missing)).dropna() < len(p)).all()
