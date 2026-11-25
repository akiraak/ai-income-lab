"""レジストリ `20261125-unsup-regime-cluster`: 日ごとの断面の指標の k-means のクラスタごとの先 20 日の上がる割合 RG1。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名・表記）／ 訓練分割だけで決める（k-means・割合）／
   出力の契約（行数・0〜100・同じ日は全銘柄で同じ値）／ 識別項目の表記 ／ leak が指標に入る。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import names, registry
from ail.ideas import i20261104_regime_ridge as r1
from ail.ideas import i20261125_unsup_regime_cluster as rg


def _panel(n_days=420, n_sym=8, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-01", periods=n_days, freq="B", tz="UTC")
    common = rng.normal(0, 0.01, n_days)
    rows = []
    for j in range(n_sym):
        r = common + rng.normal(0.0003, 0.008 * (1 + j % 3), n_days)
        df = pd.DataFrame({"symbol": f"S{j}", "ts": ts, "close": 50 * np.exp(np.cumsum(r)), "y": np.r_[r[1:], np.nan]})
        df["own_ret_1"] = r
        df["own_vol_20"] = pd.Series(r).rolling(20, min_periods=1).std().fillna(0.0).to_numpy()
        df["own_x0"] = rng.normal(size=n_days)
        rows.append(df)
    return pd.concat(rows, ignore_index=True).dropna(subset=["y"]).sort_values(["ts", "symbol"]).reset_index(drop=True)


def _split(p, cut_frac=0.75):
    days = np.sort(p["ts"].unique())
    cut = days[int(len(days) * cut_frac)]
    return p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


def _feats(p):
    return [c for c in p.columns if c.startswith(("own_", "LEAK_"))]


def test_registered_spelled_and_existing_untouched():
    for k in rg.KS:
        assert rg.name_of(k) in registry.available("detector")
    for n in (r1.name_of(2), r1.name_of(4), "A1 60日の道筋の型ごとの翌日平均のt値の順位（k-means・8個）",
              "H1 先10日ゲート（全列・学習）", "T3 QUANT（60日窓）"):
        assert n in registry.available("detector")                          # 既存の登録名はそのまま
    for n in ("Ridge", "常に上（ドリフト）", "直前リターンの符号"):
        assert n in registry.available("model")
    key = dict(鍵=r1.name_of(2), モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own",
               層="adjusted", 期間="2018-01-31", 銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key).split(".")[1] == "r1-regime-k2"           # 既存の表記は変わらない
    for k in rg.KS:
        key.update(鍵=rg.name_of(k))
        assert names.trial_name(key).split(".")[1:4] == [f"kmeans{k}-fwd20", "ridge", "shared@50"]


def test_output_contract_and_training_only():
    p = _panel()
    tr, te = _split(p)
    for k in rg.KS:
        buy, doc = registry.resolve("detector", rg.name_of(k))(tr, te, _feats(p), {"seed": 0})
        buy = np.asarray(buy, dtype=float)
        assert len(buy) == len(te) and np.isfinite(buy).all() and ((buy >= 0) & (buy <= 100)).all()
        per_day = pd.Series(buy).groupby(te["ts"].to_numpy()).nunique()
        assert (per_day == 1).all()                                          # 同じ日は全銘柄で同じ値
        pct = np.array(list(doc["クラスタごとの買い%"].values()))
        assert len(pct) == k and (np.abs(buy[:, None] - pct[None, :]).min(1) < 1e-3).all()
        assert sum(doc["訓練の日のクラスタ"].values()) == tr["ts"].nunique()
        assert sum(doc["評価の日のクラスタ"].values()) == te["ts"].nunique()
        te2 = te.assign(y=-te["y"])                                          # 評価の y を変えても買い% は同じ
        buy2 = np.asarray(registry.resolve("detector", rg.name_of(k))(tr, te2, _feats(p), {"seed": 0})[0])
        assert np.array_equal(buy, buy2)


def test_forward_up_stays_inside_training_and_rates():
    p = _panel()
    tr, _te = _split(p)
    up = rg.forward_up(tr)
    assert len(up) == len(tr)
    n_sym = tr["symbol"].nunique()
    assert int(np.isnan(up).sum()) == n_sym * (rg.FWD - 1)                 # 訓練の終わりの 19 本はそろわない
    s0 = tr[tr["symbol"] == "S0"].sort_values("ts")
    y = s0["y"].to_numpy(dtype=float)
    assert up[s0.index[0]] == float(y[: rg.FWD].sum() > 0)                  # 次の 20 本の和の符号
    pct, cnt = rg.rates(np.array([0, 0, 1, 1, 1]), np.array([1.0, 0.0, 1.0, 1.0, np.nan]), 3)
    assert list(pct) == [50.0, 100.0, 50.0] and list(cnt) == [2, 2, 0]      # 数える行が 0 のクラスタは 50


def test_indicators_use_past_only():
    p = _panel()
    ind = rg.day_indicators(p)
    assert list(ind.columns) == list(rg.INDICATORS)
    cut = np.sort(p["ts"].unique())[300]
    ind_cut = rg.day_indicators(p[p["ts"] <= cut])                          # 先の足を消しても過去の指標は同じ
    common = ind_cut.index
    pd.testing.assert_frame_equal(ind.loc[common], ind_cut, check_freq=False)


def test_leak_enters_indicators():
    p = _panel()
    tr, te = _split(p)
    _b, doc = registry.resolve("detector", rg.name_of(3))(tr, te, _feats(p), {"seed": 0})
    lk = p.assign(LEAK_y_fwd_1=p["y"])
    tr_l, te_l = _split(lk)
    _b2, doc_l = registry.resolve("detector", rg.name_of(3))(tr_l, te_l, _feats(lk), {"seed": 0})
    assert "LEAK_y_fwd_1" in doc_l["指標"] and "LEAK_y_fwd_1" not in doc["指標"]
    assert "LEAK_y_fwd_1" in doc_l["columns"]
