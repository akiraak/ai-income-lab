"""シナリオ予測の入力（`ail/scenario/data.py`。[プラン](../../../docs/plans/archive/cgan-scenario-forecast.md) §8 のテスト 1〜3）。

⚠ **落としたいのは 4 つ**: 未来の価格を書き換えても、それ以前の特徴量・窓・標準化が変わらない ／
学習と検証の答えの確定日が次の期間に入らない ／ 予測に未来の答えが要らない ／ X と Y の時点を取り違えていない。

⚠ **足は合成**（配線の検査だけ。⚠ 合成データの数字を成績として読まない。プラン §3 の条件 7）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import config
from ail.scenario import data

WINDOW, HORIZON = 60, 5


def _bars(n: int = 900, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.bdate_range("2015-01-01", periods=n, tz="UTC")
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({"ts": ts, "open": c, "high": c, "low": c, "close": c,
                         "volume": rng.uniform(1e6, 2e6, n)})


def _samples(bars, market=None):
    names = data.BASE_FEATURES + ((data.MARKET_FEATURE,) if market is not None else ())
    feats = data.build_features(bars, market)
    return feats, names, data.build_samples(feats, names, WINDOW, HORIZON)


def test_features_same_rows_and_warmup():
    b = _bars()
    f = data.build_features(b)
    assert len(f) == len(b) and (f.index == b.index).all()
    assert tuple(f.columns) == ("date",) + data.BASE_FEATURES
    # 助走: ret_1 は 1 本、20 本の窓は 20 本ぶん欠ける。⚠ 埋めていないこと
    assert f["ret_1"].isna().sum() == 1
    assert f[["vol_rel20", "rv_20", "cum_20"]].isna().sum().tolist() == [data.WARMUP] * 3
    assert np.isfinite(f.iloc[data.WARMUP:][list(data.BASE_FEATURES)].to_numpy()).all()


def test_future_prices_do_not_change_past_features_windows_or_scaler():
    """プラン §8 のテスト 1。足 cut より後ろを書き換えても、cut までの特徴量・窓・標準化は 1 ビットも変わらない。"""
    b = _bars()
    cut = 600
    b2 = b.copy()
    b2.loc[cut + 1:, ["open", "high", "low", "close"]] *= 3.0
    b2.loc[cut + 1:, "volume"] *= 7.0
    m, m2 = _bars(seed=1), _bars(seed=1)
    m2.loc[cut + 1:, "close"] *= 0.5

    f1, names, s1 = _samples(b, m)
    f2, _, s2 = _samples(b2, m2)
    cols = list(names)
    pd.testing.assert_frame_equal(f1.loc[:cut, cols], f2.loc[:cut, cols], check_exact=True)

    day = f1["date"].to_numpy().astype("datetime64[D]")[cut]
    k1, k2 = s1.origin <= day, s2.origin <= day
    assert k1.sum() == k2.sum() > 0
    assert np.array_equal(s1.X[k1], s2.X[k1])                      # 窓は起点の足までしか見ない
    # 答えが cut までに確定した起点は、答えも同じ
    done = s1.labeled & (s1.label_end <= day)
    assert done.sum() > 0 and np.array_equal(s1.Y[done], s2.Y[done])
    # ⚠ 逆に、答えが cut を跨ぐ起点は変わる（＝ このテストが本当に未来を書き換えている証拠）
    cross = s1.labeled & (s1.origin <= day) & (s1.label_end > day)
    assert cross.sum() == HORIZON and not np.array_equal(s1.Y[cross], s2.Y[cross])

    a, c = data.Scaler.fit(f1, names, day), data.Scaler.fit(f2, names, day)
    assert np.array_equal(a.mean, c.mean) and np.array_equal(a.std, c.std) and a.y_scale == c.y_scale


def test_x_and_y_are_aligned_to_the_origin():
    """窓の最後 ＝ 起点の足・答え ＝ 起点の次の足から 5 本。⚠ 1 本でもずれると先読みか、答えの取り違えになる。"""
    b = _bars()
    f, names, s = _samples(b)
    r = np.log(b["close"]).diff().to_numpy()
    day = f["date"].to_numpy().astype("datetime64[D]")
    j = 123
    i = int(np.flatnonzero(day == s.origin[j])[0])
    ret = names.index("ret_1")
    assert np.allclose(s.X[j, :, ret], r[i - WINDOW + 1:i + 1])     # 古い順・起点の足を含む
    assert np.allclose(s.Y[j], r[i + 1:i + 1 + HORIZON])
    assert s.label_end[j] == day[i + HORIZON]
    assert s.origin[0] == day[data.WARMUP + WINDOW - 1]             # 助走 20 ＋ 窓 60 の最初の起点


def test_inference_needs_no_future_labels():
    """プラン §8 のテスト 3。最後の 5 起点は答えが未確定でも窓は組める（予測できる）。"""
    b = _bars()
    f, names, s = _samples(b)
    day = f["date"].to_numpy().astype("datetime64[D]")
    assert s.origin[-1] == day[-1]
    assert (~s.labeled).sum() == HORIZON and (~s.labeled[-HORIZON:]).all()
    assert np.isfinite(s.X[-1]).all() and np.isnan(s.Y[-HORIZON:]).all()
    # 足を 5 本切り落としても、最後の起点だった日の窓は同じ（未来の足は窓に入っていない）
    _, _, short = _samples(b.iloc[:-HORIZON].reset_index(drop=True))
    assert np.array_equal(short.X[-1], s.X[s.origin == short.origin[-1]][0])


def test_split_cuts_by_label_end_not_by_origin():
    """プラン §8 のテスト 2。学習・検証の答えの確定日は、次の期間の始まりより前。"""
    b = _bars(n=1600)                                   # 2015-01 〜 2021-02
    _, _, s = _samples(b)
    folds = data.split(s, ["2019-01-01", "2020-01-01"], "2021-01-01", val_years=1)
    assert [f.name for f in folds] == ["f1", "f2"]
    for f in folds:
        assert s.label_end[f.train].max() < f.val_start
        assert s.origin[f.val].min() >= f.val_start and s.label_end[f.val].max() < f.test_start
        assert s.origin[f.test].min() >= f.test_start and s.origin[f.test].max() < f.test_stop
        assert s.labeled[np.concatenate([f.train, f.val, f.test])].all()
        assert not (set(f.train) & set(f.val)) and not (set(f.val) & set(f.test))
        # ⚠ 境目の直前 5 起点は、起点は前の期間でも答えが食い込むので、どこにも入らない
        purged = np.flatnonzero((s.origin < f.val_start) & (s.label_end >= f.val_start))
        assert len(purged) == HORIZON and not (set(purged) & set(f.train))
    # 増えていく窓: 2 つ目の学習は 1 つ目を含む
    assert set(folds[0].train) < set(folds[1].train)
    # テストの塊は重ならず、起点の日付で隙間なく続く
    assert not (set(folds[0].test) & set(folds[1].test))


def test_split_refuses_bad_boundaries():
    _, _, s = _samples(_bars(n=1600))
    with pytest.raises(SystemExit):
        data.split(s, ["2020-01-01", "2019-01-01"], "2021-01-01", val_years=1)
    with pytest.raises(SystemExit):                     # 検証が足の始まりより前 ＝ 学習が空
        data.split(s, ["2015-06-01"], "2016-01-01", val_years=2)


def test_scaler_fits_inside_train_and_roundtrips():
    b = _bars()
    f, names, s = _samples(b)
    until = s.origin[300]
    sc = data.Scaler.fit(f, names, until)
    rows = f[f["date"].to_numpy().astype("datetime64[D]") <= until][list(names)].dropna()
    assert np.allclose(sc.mean, rows.mean().to_numpy()) and sc.fit_until == str(until)
    z = sc.x(s.X[:301])
    assert z.dtype == np.float32 and abs(float(z[..., 0].mean())) < 0.2
    assert np.allclose(sc.y_inverse(sc.y(s.Y[:10])), s.Y[:10], atol=1e-7)
    again = data.Scaler.from_dict(sc.to_dict(), names)
    assert np.array_equal(again.x(s.X[:5]), sc.x(s.X[:5]))
    with pytest.raises(SystemExit):                     # ⚠ 特徴量の順序が違う scaler は使わせない
        data.Scaler.from_dict(sc.to_dict(), tuple(reversed(names)))


def test_market_column_only_when_target_is_not_the_market():
    assert data.feature_names("SPY", "SPY") == data.BASE_FEATURES
    assert data.feature_names("AAPL", "SPY") == data.BASE_FEATURES + (data.MARKET_FEATURE,)
    # 市場の足が 1 日欠けたら、その日を含む窓は落ちる（⚠ 前の値で埋めない）
    b, m = _bars(), _bars(seed=1)
    _, _, full = _samples(b, m)
    _, _, holed = _samples(b, m.drop(index=400).reset_index(drop=True))
    assert len(full) - len(holed) == WINDOW + 1          # 欠けた日と翌日のリターンが NaN


def test_quality_flags_count_without_fixing():
    b = _bars()
    q = data.quality_flags(b, today="2018-07-01")
    assert q["rows"] == len(b) and q["duplicated_dates"] == 0 and q["abs_return_over_20pct"] == 0
    bad = pd.concat([b, b.iloc[[10]]]).sort_values("ts").reset_index(drop=True)
    bad.loc[500:, "close"] *= 0.5                       # 分割の取りこぼしに見える段差
    q2 = data.quality_flags(bad)
    assert q2["duplicated_dates"] == 1 and q2["abs_return_over_20pct"] == 1


def test_config_is_fixed_as_registered():
    """⚠ 事前固定した数値（記録 `cgan-scenario.md` §0-2）。変えるなら記録に新しい試行として足してからここを直す。"""
    c = config.scenario("cgan_spy")
    assert (c["symbol"], c["window"], c["horizon"], c["n_scenarios"], c["seeds"]) == ("SPY", 60, 5, 1000, [0, 1, 2])
    assert c["split"]["test_starts"] == ["2016-09-01", "2018-09-01", "2020-09-01", "2022-09-01", "2024-09-01"]
    assert (c["split"]["test_end"], c["split"]["val_years"]) == ("2026-09-01", 2)
    assert c["model"]["aux_loss"] == "none" and c["model"]["n_critic"] == 5 and c["model"]["gp_lambda"] == 10.0
    assert c["eval"] == {"block_len": 10, "n_boot": 2000, "boot_seed": 0, "min_blocks_better": 4,
                         "coverage_80": [0.72, 0.88], "coverage_95": [0.90, 0.99]}
