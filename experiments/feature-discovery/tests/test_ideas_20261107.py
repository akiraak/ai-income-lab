"""登録簿の 4 本（2026-11-07 の段 2・人の手）: 局面を列にした LightGBM R3 ／ 60 日の形の塊 P1 ／ 出来高の集まりの遅れ L2 ／
日付をずらした偽の実行（cli.null_shuffle）。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名・綴り）／ 訓練分割だけで決める（k-means・集まり・順位・割合）／
   出力の契約（行数・0〜100）／ 識別項目の綴り ／ 偽の実行の入れ替え方（断面を保つ・fold をまたがない・y の集合は同じ）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import names, registry
from ail.features import seq
from ail.ideas import i20261106_cluster_laggard as l1
from ail.ideas import i20261106_regime_ex_ridge as r2
from ail.ideas import i20261107_path_cluster as pc
from ail.ideas import i20261107_regime_col_lgbm as r3
from ail.ideas import i20261107_volspike_cluster as vs
from cli import null_shuffle as ns


def _panel(n_days=420, n_sym=8, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-01", periods=n_days, freq="B", tz="UTC")
    common = rng.normal(0, 0.01, n_days)
    ex = rng.normal(size=(n_days, 2)).cumsum(0) * 0.1
    spikes = rng.random((n_days, 2)) < 0.08                 # 2 つの組が同じ日に出来高で跳ねる
    rows = []
    for j in range(n_sym):
        r = common + rng.normal(0.0003, 0.008, n_days)
        df = pd.DataFrame({"symbol": f"S{j}", "ts": ts, "close": 50 * np.exp(np.cumsum(r)), "y": np.r_[r[1:], np.nan]})
        df["own_ret_1"] = r
        df["own_ret_5"] = pd.Series(r).rolling(5, min_periods=1).sum().to_numpy()
        df["own_x0"] = rng.normal(size=n_days)
        for k in range(seq.WINDOW):
            df[seq.column(k)] = pd.Series(r).shift(k).fillna(0.0).to_numpy()
        df["ex_UST10YR_d1"] = ex[:, 0]
        df["ex_EURTOUSD_z20"] = ex[:, 1]
        df[vs.VZ] = np.where(spikes[:, j % 2], 3.0, 0.0) + rng.normal(0, 0.3, n_days)
        rows.append(df)
    return pd.concat(rows, ignore_index=True).dropna(subset=["y"]).sort_values(["ts", "symbol"]).reset_index(drop=True)


def _split(p, cut_frac=0.75):
    days = np.sort(p["ts"].unique())
    cut = days[int(len(days) * cut_frac)]
    return p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


def _feats(p):
    return [c for c in p.columns if c.startswith(("own_", "ex_", "LEAK_"))]


def _ok(buy, te):
    buy = np.asarray(buy, dtype=float)
    assert len(buy) == len(te) and np.isfinite(buy).all() and ((buy >= 0) & (buy <= 100)).all()


def test_new_names_registered_spelled_and_existing_untouched():
    for n in (r3.name_of(2), r3.name_of(4), r3.CONTROL, pc.NAME, vs.NAME):
        assert n in registry.available("detector")
    for n in (pc.BASELINE, vs.BASELINE, l1.BASELINE, "Ridge", "LightGBM", "常に上（ドリフト）", "直前リターンの符号"):
        assert n in registry.available("model")
    for n in (r2.name_of(2), r2.name_of(4), l1.NAME, "H1 先10日ゲート（全列・学習）", "T3 QUANT（60日窓）"):
        assert n in registry.available("detector")
    key = dict(鍵=r3.name_of(2), モデル="LightGBM", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own ex",
               層="adjusted", 期間="2018-01-31", 銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key).split(".")[1:4] == ["regime-ex-col-k2", "lgbm", "shared@50"]
    key.update(鍵=r3.CONTROL)
    assert names.trial_name(key).split(".")[1] == "regime-ex-col-none"
    key.update(鍵=pc.NAME, モデル="Ridge", 特徴量の層="own seq")
    assert names.trial_name(key).split(".")[1] == "path-kmeans8"
    key.update(鍵=vs.NAME, 特徴量の層="own")
    assert names.trial_name(key).split(".")[1] == "volspike-cluster8-lag1"


def test_r3_adds_onehot_regimes_and_control_has_none():
    p = _panel()
    tr, te = _split(p)
    ctx = {"model": registry.resolve("model", "LightGBM"), "seed": 0, "lgbm_estimators": 30}
    for k in r3.KS:
        buy, doc = registry.resolve("detector", r3.name_of(k))(tr, te, _feats(p), ctx)
        _ok(buy, te)
        assert [c for c in doc["columns"] if c.startswith("regime_k")] == [f"regime_k{i}" for i in range(k)]
        assert not any(c.startswith("ex_") for c in doc["columns"])          # ex は局面の割り当てだけ
        assert sum(doc["訓練の日の局面"].values()) == tr["ts"].nunique()
        assert set(doc["局面の列の順位"]) == {f"regime_k{i}" for i in range(k)}
    buy0, doc0 = registry.resolve("detector", r3.CONTROL)(tr, te, _feats(p), ctx)
    _ok(buy0, te)
    assert not any(c.startswith("regime_k") for c in doc0["columns"])
    assert [c for c in doc0["columns"] if not c.startswith("regime_k")] == \
        [c for c in doc["columns"] if not c.startswith("regime_k")]            # 違いは局面の列だけ
    oh = r3.onehot(np.array([0, 1, 1, 0]), 2)
    assert oh.to_numpy().sum(1).tolist() == [1, 1, 1, 1]


def test_p1_rates_from_training_only_and_leak_enters_clusters():
    p = _panel()
    tr, te = _split(p)
    buy, doc = registry.resolve("detector", pc.NAME)(tr, te, _feats(p), {"seed": 0})
    _ok(buy, te)
    rates = np.array(list(doc["塊ごとの上がる割合"].values()))
    assert (np.abs(buy[:, None] / 100.0 - rates[None, :]).min(1) < 1e-5).all()   # 買い% ＝ 塊の割合そのもの
    assert sum(doc["訓練の行の塊"].values()) == len(tr)
    te2 = te.assign(y=-te["y"])                                               # 評価の y を変えても買い% は同じ
    assert np.array_equal(buy, registry.resolve("detector", pc.NAME)(tr, te2, _feats(p), {"seed": 0})[0])
    z = pc.paths(tr, seq.window_columns(_feats(p)))
    assert z.shape == (len(tr), seq.WINDOW) and np.allclose(z.mean(1), 0.0, atol=1e-9)
    lk = p.assign(LEAK_y_fwd_1=p["y"])
    tr_l, te_l = _split(lk)
    _b, doc_l = registry.resolve("detector", pc.NAME)(tr_l, te_l, _feats(lk), {"seed": 0})
    assert "LEAK_y_fwd_1" in doc_l["columns"]
    assert doc_l["買い%の幅"] > doc["買い%の幅"]                                 # 先の値を混ぜると塊が上下を分ける
    base = registry.resolve("model", pc.BASELINE)(tr, te, _feats(p), {"seed": 0})
    assert len(base) == len(te) and np.isfinite(base).all()


def test_l2_clusters_by_shared_spike_days_and_signal_rule():
    p = _panel()
    tr, te = _split(p)
    cl = vs.clusters(tr, vs.attach_z(tr), n=2)
    even = {cl[f"S{j}"] for j in range(0, 8, 2)}
    odd = {cl[f"S{j}"] for j in range(1, 8, 2)}
    assert len(even) == 1 and len(odd) == 1 and even != odd                  # 同じ日に跳ねる組が 1 つの集まりになる
    buy, doc = registry.resolve("detector", vs.NAME)(tr, te, _feats(p), {"seed": 0})
    _ok(buy, te)
    assert doc["集まりの数"] <= vs.N_CLUSTERS
    # 合図: 仲間 3 人のうち 2 人が跳ね かつ上げ・自分は跳ねていない
    d = pd.DataFrame({"symbol": ["A", "B", "C", "D"], "ts": pd.Timestamp("2020-01-02", tz="UTC"),
                      "own_ret_1": [0.01, 0.02, -0.01, 0.0]})
    s, sig = vs.score(d, np.array([3.0, 3.0, 3.0, 0.0]), {"A": 1, "B": 1, "C": 1, "D": 1})
    assert sig.tolist() == [False, False, False, True] and s[3] == pytest.approx(2 / 3)
    z = vs.volume_z(pd.Series([1.0] * 60 + [1.0, 10.0]).add(np.r_[np.tile([0.0, 0.1], 30), 0.0, 0.0]))
    assert np.isnan(z.iloc[59]) and z.iloc[61] > 2                            # その日を含まない前の 60 日
    base = registry.resolve("model", vs.BASELINE)(tr, te, _feats(p), {"seed": 0})
    assert len(base) == len(te) and np.isfinite(base).all()


def test_null_shuffle_keeps_cross_section_and_label_multiset():
    ts = pd.date_range("2020-01-01", periods=45, freq="B", tz="UTC")
    df = pd.concat([pd.DataFrame({"symbol": s, "ts": ts, "y": np.arange(45.0) + 100 * i, "own_x": np.arange(45.0)})
                    for i, s in enumerate("AB")]).sort_values(["ts", "symbol"]).reset_index(drop=True)
    o = ns.shuffle_y(df, 20, np.random.default_rng(0))
    a, b = o[o.symbol == "A"]["y"].to_numpy(), o[o.symbol == "B"]["y"].to_numpy()
    assert np.array_equal(b - a, np.full(45, 100.0))                          # 同じ日の全銘柄が同じ日から借りる
    assert sorted(a) == list(range(45)) and not np.array_equal(a, np.arange(45.0))
    assert np.array_equal(o["own_x"].to_numpy(), df["own_x"].to_numpy())      # 特徴量は動かさない
    perm = ns.date_permutation(np.arange(45), 20, np.random.default_rng(0))
    assert sorted(perm) == list(range(45))
    assert int((np.diff(perm) != 1).sum()) <= 2                               # 塊 3 つの中の並びは保つ（切れ目は 2 か所まで）
    assert (ns.THETAS, ns.PERMS, ns.BLOCK) == ((50.0, 55.0, 60.0), 200, 20)  # ⚠ 事前固定（登録簿）
