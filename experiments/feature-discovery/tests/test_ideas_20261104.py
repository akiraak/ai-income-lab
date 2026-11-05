"""登録簿の 3 本（2026-11-04 の段 2・人の手）: 損切り係 X7（C3 SMA200 を出口だけに）／ 荒れる日を休む V2 ／ 局面別 Ridge R1。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名・綴り・ideas の読み込み）／ 先を見ない ／ 出力の契約（行数・0〜100）／
   訓練分割だけで決める（分位・k-means）／ 識別項目の綴り。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import names, registry
from ail.ideas import i20261101_c3_stop as c3
from ail.ideas import i20261104_calm_day as calm
from ail.ideas import i20261104_regime_ridge as rg

NEW_DETECTORS = [c3.NAME, calm.NAME, rg.name_of(2), rg.name_of(4)]


def _panel(n_days=520, n_sym=4, seed=0, start="2019-01-01"):
    rng = np.random.default_rng(seed)
    ts = pd.date_range(start, periods=n_days, freq="B", tz="UTC")
    rows = []
    for j in range(n_sym):
        r = rng.normal(0.0003, 0.01 + 0.004 * j, n_days)
        c = 50 * np.exp(np.cumsum(r))
        df = pd.DataFrame({"symbol": f"S{j}", "ts": ts, "close": c, "y": np.r_[r[1:], np.nan]})
        df["own_ret_1"] = r
        df["own_vol_20"] = pd.Series(r).rolling(20, min_periods=1).std().fillna(0.01).to_numpy()
        for k in range(3):
            df[f"own_x{k}"] = rng.normal(size=n_days)
        df["ex_A_d1"] = np.tile(rng.normal(size=n_days), 1)[:n_days] if j == 0 else np.nan
        rows.append(df)
    p = pd.concat(rows, ignore_index=True)
    p["ex_A_d1"] = p.groupby("ts")["ex_A_d1"].transform("first")     # 外部系列は全銘柄で同じ値
    return p.dropna(subset=["y"]).sort_values(["ts", "symbol"]).reset_index(drop=True)


def _split(p, cut_frac=0.75):
    days = np.sort(p["ts"].unique())
    cut = days[int(len(days) * cut_frac)]
    return p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


def _feats(p):
    return [c for c in p.columns if c.startswith(("own_", "ex_", "LEAK_"))]


def test_new_names_are_registered_spelled_and_existing_untouched():
    for n in NEW_DETECTORS:
        assert n in registry.available("detector")
    assert calm.LEARNER in registry.available("model")
    # 既存の登録名はそのまま（上書きしていない）
    for n in ("C3 長期 SMA200 フィルタ", "X1 出口だけ 高値20日から−10%で降りる", "H1 先10日ゲート（全列・学習）"):
        assert n in registry.available("detector")
    assert "Ridge" in registry.available("model")
    key = dict(鍵=rg.name_of(2), モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own",
               層="adjusted", 期間="2018-01-31", 銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key) == "own.r1-regime-k2.ridge.shared@50"
    key.update(鍵=calm.NAME, モデル=calm.LEARNER, 特徴量の層="own ex")
    assert names.trial_name(key).split(".")[1:3] == ["v2-calm-day", "logit"]


def test_x7_exit_is_sma200_break_and_does_not_look_ahead():
    p = _panel(n_days=500)
    tr, te = _split(p)
    buy, ex, doc = registry.resolve("detector", c3.NAME)(tr, te, _feats(p), {})
    assert len(buy) == len(te) and (buy == 100).all() and set(np.unique(ex)) <= {0.0, 100.0}
    # 期待値: 銘柄ごとに全期間の close で SMA200 を作り、評価の行で close < SMA
    full = p.sort_values(["symbol", "ts"])
    sma = full.groupby("symbol")["close"].transform(lambda s: s.rolling(200, min_periods=200).mean())
    want = ((full["close"] < sma) & sma.notna()).astype(float) * 100
    want = pd.Series(want.to_numpy(), index=pd.MultiIndex.from_frame(full[["symbol", "ts"]]))
    got = pd.Series(ex, index=pd.MultiIndex.from_frame(te[["symbol", "ts"]]))
    assert np.array_equal(got.to_numpy(), want.loc[got.index].to_numpy())
    # 先を見ない: 評価分割の後ろを切っても前の行の値は同じ
    te2 = te[te["ts"] < np.sort(te["ts"].unique())[30]]
    _b, ex2, _d = registry.resolve("detector", c3.NAME)(tr, te2, _feats(p), {})
    assert np.array_equal(ex2, ex[: len(te2)])


def test_x7_without_enough_history_never_stops():
    p = _panel(n_days=150)
    tr, te = _split(p)
    _b, ex, doc = registry.resolve("detector", c3.NAME)(tr, te, _feats(p), {})
    assert (ex == 0).all() and doc["SMA の無い評価行"] == len(te)


def test_calm_day_label_uses_training_days_only_and_gives_one_value_per_day():
    p = _panel()
    tr, te = _split(p)
    lab, q = calm.rough_label(tr)
    med = tr["y"].abs().groupby(tr["ts"]).median()
    assert q == pytest.approx(med.quantile(0.70)) and lab.mean() == pytest.approx((med > q).mean())
    ctx = {"model": registry.resolve("model", calm.LEARNER)}
    buy, doc = registry.resolve("detector", calm.NAME)(tr, te, _feats(p), ctx)
    assert len(buy) == len(te) and np.isfinite(buy).all() and ((buy >= 0) & (buy <= 100)).all()
    per_day = pd.Series(buy).groupby(te["ts"].to_numpy()).nunique()
    assert (per_day == 1).all()                                        # その日の全銘柄に同じ値
    # 評価分割の y を変えても買い% は変わらない（ラベルは訓練分割だけ）
    te2 = te.assign(y=te["y"] * 10)
    b2, _ = registry.resolve("detector", calm.NAME)(tr, te2, _feats(p), ctx)
    assert np.array_equal(buy, b2)


def test_calm_day_leak_columns_add_abs_median():
    p = _panel()
    p["LEAK_y"] = p["y"]
    D, cols = calm.day_features(p, _feats(p))
    assert "LEAK_y" in cols and "LEAK_y|abs" in cols
    assert D["LEAK_y|abs"].iloc[0] == pytest.approx(p.loc[p["ts"] == D.index[0], "y"].abs().median())


def test_regime_kmeans_is_fit_on_training_days_only_and_buy_in_range():
    p = _panel()
    tr, te = _split(p)
    ctx = {"model": registry.resolve("model", "Ridge")}
    for k in rg.KS:
        buy, doc = registry.resolve("detector", rg.name_of(k))(tr, te, _feats(p), ctx)
        assert len(buy) == len(te) and np.isfinite(buy).all() and ((buy >= 0) & (buy <= 100)).all()
        assert sum(doc["訓練の日の局面"].values()) == tr["ts"].nunique()
    rtr, rte, _ = rg.regimes(tr, te, 2)
    rtr2, _rte2, _ = rg.regimes(tr, te.iloc[: len(te) // 2], 2)       # 評価分割を差し替えても訓練の割り当ては同じ
    assert np.array_equal(rtr, rtr2)
    assert np.array_equal(rg.regimes(tr, te, 2)[1], rte)              # 決定的（種 0）


def test_regime_small_regimes_fall_back_to_the_global_model():
    p = _panel(n_days=300)                                             # 訓練 ≈ 225 日 ＜ 250 ＝ どの局面も全体の 1 本
    tr, te = _split(p)
    ctx = {"model": registry.resolve("model", "Ridge")}
    buy, doc = registry.resolve("detector", rg.name_of(2))(tr, te, _feats(p), ctx)
    assert sorted(doc["全体で埋めた局面"]) == ["0", "1"]
    # 全体の 1 本だけ ＝ 局面の列を除いた素の Ridge と同じ予測（較正も同じ尻）
    from sklearn.preprocessing import StandardScaler

    from ail.models import calibrate
    cols = [c for c in _feats(p) if c.startswith(("own_", "LEAK_"))]
    sc = StandardScaler().fit(tr[cols])
    Xtr = pd.DataFrame(sc.transform(tr[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    m = ctx["model"]
    cal = calibrate.fit(m, Xtr, tr["y"].to_numpy(), ctx)
    assert np.allclose(buy, cal.buy_pct(m(Xtr, tr["y"].to_numpy(), Xte, ctx)))
