"""レジストリ `20261127-cs-state-analog-knn`: 日ごとの市場の状態が似た過去の日の先 20 日の上がる割合 KN1（近傍法）。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名・表記）／ 訓練分割だけで決める（候補の日・割合）／
   出力の契約（行数・0〜100）／ 状態は過去の足だけ ／ 識別項目の表記 ／ leak が状態に入る ／ 基準線は状態を見ない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import names, registry
from ail.ideas import i20261125_unsup_regime_cluster as rg
from ail.ideas import i20261127_cs_state_analog_knn as kn


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
    assert kn.NAME in registry.available("detector")
    assert kn.BASELINE in registry.available("model")
    for n in (rg.name_of(3), rg.name_of(6), "A1 60日の道筋の型ごとの翌日平均のt値の順位（k-means・8個）",
              "H1 先10日ゲート（全列・学習）", "T3 QUANT（60日窓）"):
        assert n in registry.available("detector")                          # 既存の登録名はそのまま
    for n in ("Ridge", "常に上（ドリフト）", "直前リターンの符号", "形の塊を乱数で割り当てた割合（8個）"):
        assert n in registry.available("model")
    key = dict(鍵=rg.name_of(3), モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own",
               層="adjusted", 期間="2018-01-31", 銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key).split(".")[1] == "kmeans3-fwd20"          # 既存の表記は変わらない
    key.update(鍵=kn.NAME)
    assert names.trial_name(key).split(".")[1:4] == ["analog-knn-fwd20", "ridge", "shared@50"]
    t = names.table()
    assert t["method"][kn.BASELINE] == "rand-days-fwd20" and t["learner"][kn.BASELINE] == "rand-days-fwd20"
    assert t["method"]["形の塊を乱数で割り当てた割合（8個）"] == "rand-path-kmeans8"


def test_output_contract_and_training_only():
    p = _panel()
    tr, te = _split(p)
    buy, doc = registry.resolve("detector", kn.NAME)(tr, te, _feats(p), {"seed": 0})
    buy = np.asarray(buy, dtype=float)
    assert len(buy) == len(te) and np.isfinite(buy).all() and ((buy >= 0) & (buy <= 100)).all()
    n_tr = tr["ts"].nunique()
    assert doc["訓練の日数"] == n_tr and doc["評価の日数"] == te["ts"].nunique()
    assert doc["K"] == int(round(np.sqrt(n_tr))) and doc["使ったK"] == doc["K"]
    # 候補 ＝ 状態がそろい（200 日線）、先 20 日が訓練分割の中で確定している日
    assert doc["候補の日数"] == n_tr - (kn.FWD - 1) - (kn.MA_WINDOW - 1)
    assert doc["columns"] == ["own_ret_1"] and doc["成分"] == list(kn.COMPONENTS)
    te2 = te.assign(y=-te["y"])                                              # 評価の y を変えても買い% は同じ
    buy2 = np.asarray(registry.resolve("detector", kn.NAME)(tr, te2, _feats(p), {"seed": 0})[0])
    assert np.array_equal(buy, buy2)


def test_short_training_falls_back_to_50():
    p = _panel(n_days=120)
    tr, te = _split(p)                                                       # 200 日線がそろう日が無い ＝ 候補 0 日
    buy, doc = kn.analog(tr, te, _feats(p), {"seed": 0})
    assert doc["候補の日数"] == 0 and doc["使ったK"] == 0 and (np.asarray(buy) == 50.0).all()


def test_forward_up_stays_inside_training():
    p = _panel()
    tr, _te = _split(p)
    up = kn.forward_up(tr)
    assert len(up) == len(tr)
    assert int(np.isnan(up).sum()) == tr["symbol"].nunique() * (kn.FWD - 1)  # 訓練の終わりの 19 本はそろわない
    s0 = tr[tr["symbol"] == "S0"].sort_values("ts")
    y = s0["y"].to_numpy(dtype=float)
    assert up[s0.index[0]] == float(y[: kn.FWD].sum() > 0)                  # 次の 20 本の和の符号


def test_nearest_picks_close_days():
    idx = kn.nearest(np.array([[0.0], [1.0], [5.0]]), np.array([[0.9], [4.0]]), 2)
    assert idx.tolist() == [[1, 0], [2, 1]]


def test_state_uses_past_only():
    p = _panel()
    st = kn.day_state(p)
    assert list(st.columns) == list(kn.COMPONENTS)
    assert st.iloc[kn.MA_WINDOW - 1:].notna().all().all() and st.iloc[: kn.MA_WINDOW - 1][kn.COMPONENTS[0]].isna().all()
    assert ((st[kn.COMPONENTS[0]].dropna() >= 0) & (st[kn.COMPONENTS[0]].dropna() <= 1)).all()
    cut = np.sort(p["ts"].unique())[300]
    st_cut = kn.day_state(p[p["ts"] <= cut])                                 # 先の足を消しても過去の状態は同じ
    pd.testing.assert_frame_equal(st.loc[st_cut.index], st_cut)


def test_leak_enters_state():
    p = _panel()
    tr, te = _split(p)
    _b, doc = registry.resolve("detector", kn.NAME)(tr, te, _feats(p), {"seed": 0})
    lk = p.assign(LEAK_y_fwd_1=p["y"])
    tr_l, te_l = _split(lk)
    _b2, doc_l = registry.resolve("detector", kn.NAME)(tr_l, te_l, _feats(lk), {"seed": 0})
    assert "LEAK_y_fwd_1" in doc_l["成分"] and "LEAK_y_fwd_1" not in doc["成分"]
    assert "LEAK_y_fwd_1" in doc_l["columns"]


def test_baseline_ignores_state_and_is_seeded():
    p = _panel()
    tr, te = _split(p)
    fn = registry.resolve("model", kn.BASELINE)
    b = np.asarray(fn(tr, te, _feats(p), {"seed": 0}), dtype=float)
    assert len(b) == len(te) and np.isfinite(b).all() and ((b >= -50) & (b <= 50)).all()
    per_sym = pd.Series(b).groupby(te["symbol"].to_numpy()).nunique()
    assert (per_sym == 1).all()                                              # fold の中では銘柄ごとに 1 つの値（状態を見ない）
    assert np.array_equal(b, np.asarray(fn(tr, te, _feats(p), {"seed": 0}), dtype=float))
    te2 = te.assign(own_ret_1=-te["own_ret_1"])                              # 評価の日の状態を変えても同じ
    assert np.array_equal(b, np.asarray(fn(tr, te2, _feats(p), {"seed": 0}), dtype=float))
