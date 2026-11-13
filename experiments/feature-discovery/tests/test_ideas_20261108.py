"""登録簿 `20261108-hmm-sticky-regime-hold`（2026-11-13 の段 2・人の手）: 滞在日数に下限のある局面 R4（ガウス HMM・2 ／ 3 状態）。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名・綴り）／ 遷移の対角 ≥ 0.95 ／ 前向きの確率だけ（先の日を見ない）／
   同じ日の全銘柄に同じ買い% ／ 出力の契約（行数・0〜100）／ leak 対照が観測に届く。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import names, registry
from ail.ideas import i20261108_hmm_sticky as hs


def _panel(n_days=900, n_sym=6, seed=0, block=90):
    """90 日ごとに「静かで上がる」「荒れて下がる」が入れ替わる市場。"""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-01", periods=n_days, freq="B", tz="UTC")
    calm = (np.arange(n_days) // block) % 2 == 0
    common = np.where(calm, rng.normal(0.002, 0.004, n_days), rng.normal(-0.002, 0.02, n_days))
    rows = []
    for j in range(n_sym):
        r = common + rng.normal(0.0, 0.003, n_days)
        df = pd.DataFrame({"symbol": f"S{j}", "ts": ts, "close": 50 * np.exp(np.cumsum(r)), "y": np.r_[r[1:], np.nan]})
        df["own_ret_1"] = r
        df["own_x0"] = rng.normal(size=n_days)
        rows.append(df)
    p = pd.concat(rows, ignore_index=True).dropna(subset=["y"]).sort_values(["ts", "symbol"]).reset_index(drop=True)
    return p, calm


def _split(p, cut_frac=0.7):
    days = np.sort(p["ts"].unique())
    cut = days[int(len(days) * cut_frac)]
    return p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


def _feats(p):
    return [c for c in p.columns if c.startswith(("own_", "LEAK_"))]


def test_new_names_registered_spelled_and_existing_untouched():
    for k in hs.KS:
        assert hs.name_of(k) in registry.available("detector")
    for n in ("R1 局面別 Ridge（k-means・2個）", "H1 先10日ゲート（全列・学習）", "T3 QUANT（60日窓）"):
        assert n in registry.available("detector")
    key = dict(鍵=hs.name_of(2), モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own", 層="adjusted", 期間="2018-01-31",
               銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key) == "own.r4-hmm-sticky-k2.ridge.shared@50"      # 登録簿の key_shape
    key.update(鍵=hs.name_of(3), 閾値="60")
    assert names.trial_name(key) == "own.r4-hmm-sticky-k3.ridge.shared@60"
    assert hs.KS == (2, 3) and hs.DIAG_MIN == 0.95 and hs.DWELL_DAYS == 20


def test_floor_diag_raises_diagonal_and_keeps_rows_stochastic():
    A = hs.floor_diag(np.array([[0.5, 0.3, 0.2], [0.0, 0.99, 0.01], [0.0, 0.0, 0.0]]))
    assert np.allclose(A.sum(1), 1.0) and (np.diag(A) >= 0.95 - 1e-12).all()
    assert np.allclose(A[0], [0.95, 0.03, 0.02]) and np.allclose(A[1], [0.0, 0.99, 0.01]) and np.allclose(A[2], [0.025, 0.025, 0.95])


def test_forward_is_filtering_only():
    rng = np.random.default_rng(0)
    X = np.r_[rng.normal(-1, 0.3, (60, 1)), rng.normal(1, 0.3, (60, 1))]
    h = hs.fit_hmm(X, 2)
    assert (np.diag(h["A"]) >= 0.95 - 1e-12).all() and np.allclose(h["A"].sum(1), 1.0)
    lb = hs._logb(X, h["mu"], h["var"])
    a, _c, _b = hs.forward(lb, h["pi"], h["A"])
    lb2 = lb.copy()
    lb2[80:] = lb[:40][::-1]                                  # 後ろの観測を壊しても、前の日の確率は変わらない
    a2, _c, _b = hs.forward(lb2, h["pi"], h["A"])
    assert np.array_equal(a[:80], a2[:80]) and np.allclose(a.sum(1), 1.0)
    assert (a[:55].argmax(1) == a[5].argmax()).all() and (a[65:].argmax(1) == a[100].argmax()).all() and a[5].argmax() != a[100].argmax()
    # 続きの前向き（訓練の最後の日から）は、通しで回した前向きと同じ
    b_, _c, _b = hs.forward(lb[60:], a[59], h["A"], first_is_prior=False)
    assert np.allclose(b_, a[60:])


def test_r4_same_buy_for_all_symbols_and_no_lookahead():
    p, calm = _panel()
    tr, te = _split(p)
    ctx = {"model": registry.resolve("model", "Ridge"), "seed": 0}
    for k in hs.KS:
        buy, doc = registry.resolve("detector", hs.name_of(k))(tr, te, _feats(p), ctx)
        assert len(buy) == len(te) and np.isfinite(buy).all() and ((buy >= 0) & (buy <= 100)).all()
        per_day = pd.Series(buy).groupby(te["ts"].to_numpy()).nunique()
        assert (per_day == 1).all()                                         # 同じ日の全銘柄に同じ買い%
        assert doc["K"] == k and doc["columns"] == ["__o_ret", "__o_vol"] and (np.diag(np.array(doc["遷移行列"])) >= 0.95 - 1e-4).all()
        assert doc["source"] == "train" and doc["a"] > 0                    # Platt は訓練分割の全部の行（向きは素の点のまま）
        if k == 2:
            assert doc["評価の滞在日数の中央値"] >= 20 and doc["評価の状態の切り替わり"] <= 6   # 90 日ごとに入れ替わる市場で、局面は数十日続く
    # 静かで上がる時期の買い% は、荒れて下がる時期より高い（2 状態）
    buy, _ = registry.resolve("detector", hs.name_of(2))(tr, te, _feats(p), ctx)
    days = pd.DatetimeIndex(np.sort(p["ts"].unique()))
    is_calm = pd.Series(calm[:len(days)], index=days).reindex(pd.to_datetime(te["ts"])).to_numpy()
    assert buy[is_calm].mean() > buy[~is_calm].mean() + 5
    # 先の日を見ない: 評価期間の後ろ半分のリターンを壊しても、前半分の買い% は 1 ビットも変わらない
    ed = np.sort(te["ts"].unique())
    late = (te["ts"] >= ed[len(ed) // 2]).to_numpy()
    te2 = te.copy()
    te2.loc[late, "own_ret_1"] = 0.5
    buy2, _ = registry.resolve("detector", hs.name_of(2))(tr, te2, _feats(p), ctx)
    assert np.array_equal(buy[~late], buy2[~late]) and not np.array_equal(buy[late], buy2[late])


def test_r4_leak_reaches_the_observations():
    # ⚠ 滞在に下限のある局面は翌日の符号に付いていけない ＝ leak が跳ねるとは限らない。ここで見るのは「観測に届く」こと
    p, _ = _panel()
    tr, te = _split(p)
    base, _d = registry.resolve("detector", hs.name_of(2))(tr, te, _feats(p), {"seed": 0})
    p["LEAK_y"] = p["y"] * 50.0
    tr, te = _split(p)
    buy, doc = registry.resolve("detector", hs.name_of(2))(tr, te, _feats(p), {"seed": 0})
    assert doc["columns"] == ["__o_ret", "__o_vol", "__o_leak0"] and not np.array_equal(buy, base)
    obs, cols = hs.day_obs(tr, te, _feats(p))
    day_y = pd.concat([tr, te]).groupby("ts")["y"].mean()
    assert np.allclose(obs["__o_leak0"].to_numpy(), 50.0 * day_y.reindex(obs.index).to_numpy())      # 遅らせない
    m = pd.concat([tr, te]).groupby("ts")["own_ret_1"].mean()
    assert np.allclose(obs["__o_ret"].to_numpy()[1:], m.to_numpy()[:-1])                              # 2 列は 1 日遅れ
