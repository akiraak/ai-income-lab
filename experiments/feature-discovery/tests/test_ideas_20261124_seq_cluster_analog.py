"""レジストリ `20261124-seq-cluster-analog`: 60 日の道筋（始点で割った比）の k-means の型ごとの t の順位 A1。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名・表記）／ 訓練分割だけで決める（k-means・t・順位）／
   出力の契約（行数・0〜100）／ 識別項目の表記 ／ leak が塊の入力に入る。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import names, registry
from ail.features import seq
from ail.ideas import i20261107_path_cluster as pc
from ail.ideas import i20261124_seq_cluster_analog as a1


def _panel(n_days=420, n_sym=8, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-01", periods=n_days, freq="B", tz="UTC")
    common = rng.normal(0, 0.01, n_days)
    rows = []
    for j in range(n_sym):
        r = common + rng.normal(0.0003, 0.008 * (1 + j % 3), n_days)
        df = pd.DataFrame({"symbol": f"S{j}", "ts": ts, "close": 50 * np.exp(np.cumsum(r)), "y": np.r_[r[1:], np.nan]})
        df["own_ret_1"] = r
        df["own_x0"] = rng.normal(size=n_days)
        for k in range(seq.WINDOW):
            df[seq.column(k)] = pd.Series(r).shift(k).fillna(0.0).to_numpy()
        rows.append(df)
    return pd.concat(rows, ignore_index=True).dropna(subset=["y"]).sort_values(["ts", "symbol"]).reset_index(drop=True)


def _split(p, cut_frac=0.75):
    days = np.sort(p["ts"].unique())
    cut = days[int(len(days) * cut_frac)]
    return p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


def _feats(p):
    return [c for c in p.columns if c.startswith(("own_", "LEAK_"))]


def test_registered_spelled_and_existing_untouched():
    assert a1.NAME in registry.available("detector")
    for n in (pc.NAME, "H1 先10日ゲート（全列・学習）", "T3 QUANT（60日窓）"):
        assert n in registry.available("detector")                          # 既存の登録名はそのまま
    for n in (pc.BASELINE, "Ridge", "常に上（ドリフト）", "直前リターンの符号"):
        assert n in registry.available("model")
    key = dict(鍵=pc.NAME, モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own seq",
               層="adjusted", 期間="2018-01-31", 銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key).split(".")[1] == "path-kmeans8"           # 既存の表記は変わらない
    key.update(鍵=a1.NAME)
    assert names.trial_name(key).split(".")[1:4] == ["kmeans8-analog", "ridge", "shared@50"]


def test_output_contract_and_training_only():
    p = _panel()
    tr, te = _split(p)
    buy, doc = registry.resolve("detector", a1.NAME)(tr, te, _feats(p), {"seed": 0})
    buy = np.asarray(buy, dtype=float)
    assert len(buy) == len(te) and np.isfinite(buy).all() and ((buy >= 0) & (buy <= 100)).all()
    pct = np.array(list(doc["型ごとの買い%"].values()))
    assert (np.abs(buy[:, None] - pct[None, :]).min(1) < 1e-3).all()       # 買い% ＝ 型の順位そのもの
    assert sum(doc["訓練の行の型"].values()) == len(tr)
    assert sum(doc["評価の行の型"].values()) == len(te)
    te2 = te.assign(y=-te["y"])                                              # 評価の y を変えても買い% は同じ
    assert np.array_equal(buy, np.asarray(registry.resolve("detector", a1.NAME)(tr, te2, _feats(p), {"seed": 0})[0]))
    # t の向きと順位: t が大きい型ほど買い% が高い
    t = np.array(list(doc["型ごとのt"].values()))
    order = np.argsort(t)
    assert np.all(np.diff(pct[order]) >= -1e-9)


def test_paths_divide_by_start_and_rank_helpers():
    p = _panel()
    tr, _te = _split(p)
    z = a1.paths(tr, seq.window_columns(_feats(p)))
    assert z.shape == (len(tr), seq.WINDOW) and (z > 0).all()
    r0 = tr[seq.column(seq.WINDOW - 1)].to_numpy(dtype=float)              # 最も古いリターン
    assert np.allclose(z[:, 0], np.exp(r0))                                 # 始点の価格で割った比
    ctr = np.array([0, 0, 1, 1, 1, 2])
    mean, t = a1.tstats(ctr, np.array([0.01, 0.03, -0.01, -0.02, -0.03, 0.5]))
    assert t[2] == 0.0 and t[0] > 0 > t[1] and mean[2] == 0.5               # 行 1 つの型は t 0
    pct = a1.rank_pct(t, ctr)
    assert pct[0] > pct[2] > pct[1] and (pct >= 0).all() and (pct <= 100).all()


def test_leak_enters_clusters():
    p = _panel()
    tr, te = _split(p)
    _b, doc = registry.resolve("detector", a1.NAME)(tr, te, _feats(p), {"seed": 0})
    lk = p.assign(LEAK_y_fwd_1=p["y"])
    tr_l, te_l = _split(lk)
    _b2, doc_l = registry.resolve("detector", a1.NAME)(tr_l, te_l, _feats(lk), {"seed": 0})
    assert "LEAK_y_fwd_1" in doc_l["columns"] and "LEAK_y_fwd_1" not in doc["columns"]
    t_l = np.abs(np.array(list(doc_l["型ごとのt"].values())))
    t0 = np.abs(np.array(list(doc["型ごとのt"].values())))
    assert t_l.max() > t0.max()                                             # 先の値を混ぜると型が上下を分ける
