"""⚠ **先読みが入っていないことの検査**（rules.md 7 章）。

⚠ **これが一番見つけにくい壊れ方である。** 破ると「良い数字」が出るので、疑われずに通ってしまう。

やり方は 1 つ。⚠ **足 i より先を消したデータで作った特徴量が、全期間で作ったものと一致するか。**
一致しなければ、⚠ **その特徴量は未来を見ている。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail.features import cross, labels, leadlag, own, relative


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


@pytest.mark.parametrize("layer_name", ["own", "cs", "rel", "ll"])
def test_truncating_the_future_does_not_change_the_past(layer_name):
    """⚠ **未来を切り落としても、過去の特徴量は 1 つも変わってはいけない。**"""
    p = panel()
    cut = 200
    ctx_full = _with_own(p)
    short = {s: df.iloc[:cut].reset_index(drop=True) for s, df in p.items()}
    ctx_short = _with_own(short)

    fn = {"own": own.layer, "cs": cross.layer, "rel": relative.layer, "ll": leadlag.layer}[layer_name]
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
