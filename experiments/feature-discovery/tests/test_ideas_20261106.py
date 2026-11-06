"""登録簿の 3 本（2026-11-06 の段 2・人の手）: 外生の局面別 Ridge R2 ／ 集まりの中で遅れた銘柄を買う L1 ／ 検出力の曲線（cli.planted_power）。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名・綴り）／ 訓練分割だけで決める（k-means・集まり・順位）／
   出力の契約（行数・0〜100）／ 識別項目の綴り ／ 合成の表の作り方と判定の式。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import names, registry
from ail.ideas import i20261104_regime_ridge as r1
from ail.ideas import i20261106_cluster_laggard as cl
from ail.ideas import i20261106_regime_ex_ridge as r2
from cli import planted_power as pp


def _panel(n_days=520, n_sym=6, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-01", periods=n_days, freq="B", tz="UTC")
    common = rng.normal(0, 0.01, n_days)
    ex = rng.normal(size=(n_days, 2)).cumsum(0) * 0.1
    rows = []
    for j in range(n_sym):
        r = (1.0 if j < n_sym // 2 else -1.0) * common + rng.normal(0.0003, 0.008, n_days)
        df = pd.DataFrame({"symbol": f"S{j}", "ts": ts, "close": 50 * np.exp(np.cumsum(r)), "y": np.r_[r[1:], np.nan]})
        df["own_ret_1"] = r
        df["own_ret_5"] = pd.Series(r).rolling(5, min_periods=1).sum().to_numpy()
        df["own_vol_20"] = pd.Series(r).rolling(20, min_periods=1).std().fillna(0.01).to_numpy()
        df["own_x0"] = rng.normal(size=n_days)
        df["ex_UST10YR_d1"] = ex[:, 0]
        df["ex_EURTOUSD_z20"] = ex[:, 1]
        df["ex_WX_X_PRCP_d1"] = rng.normal(size=n_days)       # 気象は局面に使わない
        rows.append(df)
    return pd.concat(rows, ignore_index=True).dropna(subset=["y"]).sort_values(["ts", "symbol"]).reset_index(drop=True)


def _split(p, cut_frac=0.75):
    days = np.sort(p["ts"].unique())
    cut = days[int(len(days) * cut_frac)]
    return p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


def _feats(p):
    return [c for c in p.columns if c.startswith(("own_", "ex_", "LEAK_"))]


def test_new_names_registered_spelled_and_existing_untouched():
    for n in (r2.name_of(2), r2.name_of(4), cl.NAME):
        assert n in registry.available("detector")
    assert cl.BASELINE in registry.available("model")
    for n in (r1.name_of(2), r1.name_of(4), "H1 先10日ゲート（全列・学習）"):
        assert n in registry.available("detector")
    for n in ("Ridge", "常に上（ドリフト）", "直前リターンの符号"):
        assert n in registry.available("model")
    key = dict(鍵=r2.name_of(2), モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own ex",
               層="adjusted", 期間="2018-01-31", 銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key).split(".")[1:4] == ["r2-regime-ex-k2", "ridge", "shared@50"]
    key.update(鍵=cl.NAME, 特徴量の層="own")
    assert names.trial_name(key).split(".")[1] == "cluster8-laggard5"


def test_r2_uses_only_rates_and_fx_columns_fit_on_training_days():
    p = _panel()
    tr, te = _split(p)
    assert r2.regime_columns(_feats(p)) == ["ex_UST10YR_d1", "ex_EURTOUSD_z20"]
    ctx = {"model": registry.resolve("model", "Ridge")}
    for k in r2.KS:
        buy, doc = registry.resolve("detector", r2.name_of(k))(tr, te, _feats(p), ctx)
        assert len(buy) == len(te) and np.isfinite(buy).all() and ((buy >= 0) & (buy <= 100)).all()
        assert sum(doc["訓練の日の局面"].values()) == tr["ts"].nunique()
        assert not any(c.startswith("ex_") for c in doc["columns"])       # Ridge の入力に ex は入らない
        assert doc["滞在日数の中央値（訓練）"] >= 1
    cols = r2.regime_columns(_feats(p))
    rtr, rte, _ = r2.regimes(tr, te, 2, cols)
    rtr2, _x, _ = r2.regimes(tr, te.iloc[: len(te) // 2], 2, cols)
    assert np.array_equal(rtr, rtr2)                                      # 評価分割を差し替えても訓練の割り当ては同じ
    assert (pd.Series(rtr).groupby(tr["ts"].to_numpy()).nunique() == 1).all()   # 日ごとに 1 つの局面


def test_r2_needs_ex_columns():
    p = _panel().drop(columns=["ex_UST10YR_d1", "ex_EURTOUSD_z20"])
    tr, te = _split(p)
    with pytest.raises(SystemExit):
        registry.resolve("detector", r2.name_of(2))(tr, te, _feats(p), {"model": registry.resolve("model", "Ridge")})


def test_run_lengths():
    assert r2.run_lengths(np.array([0, 0, 1, 1, 1, 0])) == [2, 3, 1]
    assert r2.run_lengths(np.array([])) == []


def test_clusters_from_training_correlation_and_score_is_minus_deviation():
    p = _panel()
    tr, te = _split(p)
    c = cl.clusters(tr, n=2)
    assert len(set(c.values())) == 2
    assert len({c[f"S{j}"] for j in range(3)}) == 1 and len({c[f"S{j}"] for j in range(3, 6)}) == 1   # 共通の項の向きで 2 つに分かれる
    s = cl.laggard_score(te, c)
    g = te["symbol"].map(c)
    want = -(te["own_ret_5"] - te.groupby([te["ts"], g])["own_ret_5"].transform("mean"))
    assert np.allclose(s, want.to_numpy())
    # 評価分割の値を変えても集まりは変わらない（訓練だけ）
    assert cl.clusters(tr, n=2) == cl.clusters(tr.copy(), n=2)


def test_l1_buy_is_training_rank_and_in_range():
    p = _panel()
    tr, te = _split(p)
    buy, doc = registry.resolve("detector", cl.NAME)(tr, te, _feats(p), {})
    assert len(buy) == len(te) and ((buy >= 0) & (buy <= 100)).all()
    assert doc["集まりの数"] <= cl.N_CLUSTERS
    assert cl.rank_pct(np.array([1.0, 2.0, 3.0, 4.0]), np.array([0.0, 2.0, 9.0])).tolist() == [0.0, 37.5, 100.0]
    # y を変えても買い% は変わらない（規則はラベルを読まない）
    b2, _ = registry.resolve("detector", cl.NAME)(tr.assign(y=tr["y"] * 10), te.assign(y=0.0), _feats(p), {})
    assert np.array_equal(buy, b2)


def test_random_cluster_baseline_keeps_sizes_and_is_seeded():
    p = _panel()
    tr, te = _split(p)
    c = cl.clusters(tr)
    sh = cl.shuffled(c, 0)
    assert sorted(sh.values()) == sorted(c.values()) and sh == cl.shuffled(c, 0)
    out = registry.resolve("model", cl.BASELINE)(tr, te, _feats(p), {"seed": 0})
    assert len(out) == len(te) and ((out >= -50) & (out <= 50)).all()


def test_planted_synth_keeps_rows_and_noise_is_shared_across_strengths():
    p = _panel()
    syms, mu, L = pp.real_moments(p)
    a, b = pp.synth(p, syms, mu, L, 0.0, 3), pp.synth(p, syms, mu, L, 20.0, 3)
    assert len(a) == len(p) and a[["ts", "symbol"]].equals(p[["ts", "symbol"]])
    assert np.array_equal(a[pp.PLANTED], b[pp.PLANTED])
    assert np.allclose(b["y"] - a["y"], 20e-4 * a[pp.PLANTED])
    assert not np.array_equal(a["y"], pp.synth(p, syms, mu, L, 0.0, 4)["y"])
    # 共分散は本物に近い（同じ向きの銘柄は正の相関）
    w = a.pivot(index="ts", columns="symbol", values="y")
    assert w["S0"].corr(w["S1"]) > 0.2 and w["S0"].corr(w["S5"]) < -0.2


def test_planted_verdict_matches_trading_rule():
    assert pp.verdict([1, 2, 3, 4, 5]) == "採る"
    assert pp.verdict([10, -1, 1, 1, 1]) == "保留"
    assert pp.verdict([-10, 1, 1, 1, 1]) == "落とす"
    assert pp.verdict([1, float("nan")]) == "保留"
