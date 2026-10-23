"""⚠ **調整の検算**（rules.md 2 章）。

見るのは 3 つ。
  1. 仕込んだ継ぎ目が消え、⚠ **出来高も逆向きに直っているか**
  2. ⚠ **本物の暴落を消していないか**（一番怖い誤り）
  3. ⚠ **一番新しい足を動かしていないか**（調整の基準）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.data import adjust


def series(n=400, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, 0.004, n)))
    l = np.minimum(o, c) * (1 - abs(rng.normal(0, 0.004, n)))
    df = pd.DataFrame({"time_ms": np.arange(n) * 86_400_000, "open": o, "high": h,
                       "low": l, "close": c, "volume": np.full(n, 5e6)})
    df["ts"] = pd.to_datetime(df["time_ms"], unit="ms", utc=True)
    return df


def rescale(df, lo, hi, ratio):
    """[lo, hi) の値段を ratio で割り、出来高を ratio 倍する（提供元の誤りを再現）。"""
    d = df.copy()
    sl = slice(lo, hi)
    for col in ("open", "high", "low", "close"):
        d.loc[sl, col] = d.loc[sl, col] / ratio
    d.loc[sl, "volume"] = d.loc[sl, "volume"] * ratio
    return d


def test_reverting_block_is_repaired():
    """⚠ **XLK 2022-12-30〜2023-10-23 と同じ形**（塊で目盛りがずれ、あとで戻る）。"""
    base = series()
    broken = rescale(base, 200, 300, 2.0)
    out, breaks, summary = adjust.adjust(broken)
    assert summary["repaired"] == 2
    assert np.allclose(out["close"].values, base["close"].values, rtol=1e-9)
    assert np.allclose(out["volume"].values, base["volume"].values, rtol=1e-9)


def test_permanent_step_is_repaired_and_anchored_at_the_end():
    """⚠ **GOOGL 2014-04-02 と同じ形**（片道の段。基準は一番新しい足）。"""
    base = series()
    broken = base.copy()
    for col in ("open", "high", "low", "close"):
        broken.loc[:249, col] = broken.loc[:249, col] * 10.0
    broken.loc[:249, "volume"] = broken.loc[:249, "volume"] / 10.0
    out, breaks, summary = adjust.adjust(broken)
    assert summary["repaired"] == 1
    assert out["close"].iloc[-1] == broken["close"].iloc[-1]     # ⚠ 新しい側は動かさない
    assert np.allclose(out["close"].values, base["close"].values, rtol=1e-9)


def test_volume_untouched_for_non_split_ratio():
    """⚠ **スピンオフ（ABT 2011-05-23）は株数を変えない。** 出来高を掛けると嘘になる。"""
    base = series()
    broken = base.copy()
    for col in ("open", "high", "low", "close"):
        broken.loc[250:, col] = broken.loc[250:, col] / 2.113
    out, breaks, summary = adjust.adjust(broken)
    assert summary["repaired_other_ratio"] == 1
    assert np.allclose(out["volume"].values, broken["volume"].values)


def test_real_crash_is_not_erased():
    """⚠ **これが一番怖い誤り。** 実在の −52% を分割と誤認すると、損失そのものが消える。"""
    base = series()
    crash = base.copy()
    i = 250
    for col in ("open", "high", "low", "close"):
        crash.loc[i:, col] = crash.loc[i:, col] / 2.0
    # ⚠ **AAPL 2000-09-29 と同じ形**: 寄りから半値に飛び、売買代金が 12 倍に跳ねる
    crash.loc[i, "open"] = crash.loc[i - 1, "close"] / 1.95
    crash.loc[i, "high"] = crash.loc[i, "open"]
    crash.loc[i, "volume"] = crash.loc[i, "volume"] * 12
    out, breaks, summary = adjust.adjust(crash)
    assert summary["repaired"] == 0
    assert (breaks["kind"] == adjust.KIND_REAL).any()
    assert np.allclose(out["close"].values, crash["close"].values)


def test_small_steps_are_flagged_not_repaired():
    """⚠ **20% 前後の段差は自動で直さない**（rules.md 2-4 の段階 2）。"""
    base = series()
    broken = rescale(base, 200, 260, 1.25)
    out, breaks, summary = adjust.adjust(broken)
    assert summary["repaired"] == 0
    assert summary["needs_primary_source"] >= 1
    assert np.allclose(out["close"].values, broken["close"].values)


def test_snapping_keeps_cumulative_ratio_exactly_one():
    """⚠ **実測の段差をそのまま使うと、往復したあと系列全体がずれる**（rules.md 2 章 規約 6）。"""
    base = series(seed=7)
    broken = rescale(rescale(base, 100, 200, 2.0), 130, 160, 5.0)
    out, _, _ = adjust.adjust(broken)
    assert np.allclose(out["close"].values, base["close"].values, rtol=1e-9)


def _walk(n=300, seed=3):
    rng = np.random.default_rng(seed)
    close = 30 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]] * np.exp(rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * 1.005
    low = np.minimum(open_, close) * 0.995
    ts = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    return pd.DataFrame({"ts": ts, "time_ms": ts.astype("int64") // 10**6, "open": open_, "high": high,
                         "low": low, "close": close, "volume": np.full(n, 1_000_000.0)})


def test_fix_split_close_repairs_only_that_close():
    """⚠ **分割の日の終値 1 本だけが旧目盛り**（CSX 2021-06-28 の型。rules.md 30-9）。直すのは終値 1 本で、ほかは 1 ビットも触らない。"""
    df = _walk()
    bad = df.copy()
    k = 150
    bad.loc[k, "close"] = df.loc[k, "close"] * 3        # 3:1 の分割の日の終値だけ未調整
    out, _breaks, summary = adjust.adjust(bad, fix_split_close=True)
    assert summary["fixed_split_close"] == 1 and summary["events"][0]["kind"] == adjust.KIND_CLOSE
    assert abs(out.loc[k, "close"] - df.loc[k, "close"]) < 1e-9
    np.testing.assert_array_equal(out.drop(columns="close").to_numpy(), bad.drop(columns="close").to_numpy())
    assert summary["rows_rescaled"] == 0                 # 分割と読んで前の歴史を割らない


def test_fix_split_close_is_off_by_default():
    """既定では今までどおり（前の歴史を 1/3 にする誤りも含めて 1 ビットも変わらない）。"""
    df = _walk()
    bad = df.copy()
    bad.loc[150, "close"] = df.loc[150, "close"] * 3
    out_default, _b, s_default = adjust.adjust(bad)
    out_plain, _b2, _s2 = adjust.adjust(bad, fix_split_close=False)
    pd.testing.assert_frame_equal(out_default, out_plain)
    assert "fixed_split_close" in s_default and s_default["fixed_split_close"] == 0
    assert s_default["rows_rescaled"] > 0                # 今までの読み方: 翌日を分割と見て前を割る
