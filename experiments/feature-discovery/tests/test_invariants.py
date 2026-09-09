"""⚠ **不変条件の検査そのものを検査する**（rules.md 5 章）。

検査が「何も見つけない」のか「壊れていて見つけられない」のかを分けるため、
⚠ **わざと壊した表を食わせて、ちゃんと止まることを確かめる。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail.data import check, store


def bars(n=50, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = c * (1 + rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, 0.003, n)))
    l = np.minimum(o, c) * (1 - abs(rng.normal(0, 0.003, n)))
    df = pd.DataFrame({"time_ms": np.arange(n) * 86_400_000, "open": o, "high": h,
                       "low": l, "close": c, "volume": rng.integers(1e6, 1e7, n).astype(float)})
    df["ts"] = pd.to_datetime(df["time_ms"], unit="ms", utc=True)
    return df


def test_clean_bars_pass():
    assert check.fatal_of(check.check_bars(bars())) == {}


@pytest.mark.parametrize("break_it,expect", [
    (lambda d: d.assign(time_ms=d.time_ms[::-1].values), "time_not_monotonic"),
    (lambda d: d.assign(time_ms=d.time_ms.where(d.index != 5, d.time_ms[4])), "time_duplicated"),
    (lambda d: d.assign(close=d.close.where(d.index != 3, -1.0)), "nonpositive_price"),
    (lambda d: d.assign(volume=d.volume.where(d.index != 3, -1.0)), "negative_volume"),
    (lambda d: d.assign(close=d.close.where(d.index != 3, np.nan)), "nan_in_bars"),
])
def test_fatal_violations_are_caught(break_it, expect):
    """⚠ **止めるべき違反が止まらなければ、検査は無いのと同じ。**"""
    rep = check.check_bars(break_it(bars()))
    assert expect in check.fatal_of(rep)


def test_ohlc_is_counted_not_fatal():
    """⚠ **提供元の粗（OHLC の綻び）は数えるだけ**（rules.md 5 章）。止めると誰も回さなくなる。"""
    d = bars()
    d.loc[7, "high"] = d.loc[7, "low"] * 0.5
    rep = check.check_bars(d)
    assert rep["ohlc_inconsistent"] >= 1
    assert check.fatal_of(rep) == {}


def test_symbol_filename_roundtrip():
    """`BRK/B` はファイル名で `BRK-B`（rules.md 4 章）。"""
    assert store.to_filename("BRK/B") == "BRK-B"
    assert store.from_filename("BRK-B") == "BRK/B"
    assert store.from_filename("AAPL") == "AAPL"


def test_fingerprint_changes_with_content():
    """⚠ **指紋が変わらなければ「入力が同じ」と誤って言えてしまう。**"""
    a = bars()
    b = a.copy()
    b.loc[10, "close"] *= 1.0001
    assert store.fingerprint(a)["sha256"] != store.fingerprint(b)["sha256"]
    assert store.fingerprint(a)["sha256"] == store.fingerprint(a.copy())["sha256"]
