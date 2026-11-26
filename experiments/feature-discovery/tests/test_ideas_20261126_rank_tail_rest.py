"""レジストリ `20261126-rank-tail-rest`（2026-11-26 のステージ 2）: 直近 252 日の予測値の順位が下の端に入ったときだけ休む TR2 ／ TR3。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名・表記・TR1 の出力）／ 識別項目の表記がレジストリの key_shape と同じ ／
   休む・戻るの規則（q で入り 0.5 で戻る・最初は持つ・銘柄をまたがない）／ 先 20 本の和は渡した行の中だけ ／
   出力の契約（行数・0 か 100）／ TR2 の順位は TR1 と 1 ビットも違わない ／ 先の日を見ない ／ 末尾の予測は末尾の y を知らない ／ leak が届く。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import names, registry
from ail.ideas import i20261113_score_trailing_rank as trk
from ail.ideas import i20261126_rank_tail_rest as rest

SPELL = {("ret", 0.1): "ts-rank252-rest10", ("ret", 0.2): "ts-rank252-rest20",
         ("fwd20", 0.1): "h3-gate20-rank252-rest10", ("fwd20", 0.2): "h3-gate20-rank252-rest20"}


def _panel(n_days=700, n_sym=6, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-01", periods=n_days, freq="B", tz="UTC")
    rows = []
    for j in range(n_sym):
        r = rng.normal(0.0003, 0.01, n_days)
        df = pd.DataFrame({"symbol": f"S{j}", "ts": ts, "close": 50 * np.exp(np.cumsum(r)), "y": np.r_[r[1:], np.nan]})
        df["own_ret_1"] = r
        df["own_x0"] = rng.normal(size=n_days)
        df["ex_other"] = rng.normal(size=n_days)
        rows.append(df)
    return pd.concat(rows, ignore_index=True).dropna(subset=["y"]).sort_values(["ts", "symbol"]).reset_index(drop=True)


def _split(p, cut_frac=0.8):
    days = np.sort(p["ts"].unique())
    cut = days[int(len(days) * cut_frac)]
    return p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


def _feats(p):
    return [c for c in p.columns if c.startswith(("own_", "ex_", "LEAK_"))]


def _ctx():
    return {"model": registry.resolve("model", "Ridge"), "seed": 0}


def _det(kind, q):
    return registry.resolve("detector", rest.NAMES[(kind, q)])


def test_new_names_registered_spelled_and_existing_untouched():
    assert set(rest.NAMES) == set(SPELL) and rest.N == 252 and rest.QS == (0.1, 0.2) and rest.BACK == 0.5 and rest.FWD == 20
    key = dict(鍵="", モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own", 層="adjusted", 期間="2018-01-31",
               銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="50")
    for kq, name in rest.NAMES.items():
        assert name in registry.available("detector")
        key.update(鍵=name)
        assert names.trial_name(key) == f"own.{SPELL[kq]}.ridge.shared@50"      # レジストリの key_shape
    # 既存の登録名と表記は前のまま
    for n in (trk.NAME, "H1 先10日ゲート（全列・学習）", "H3 先20日ゲート（全列・学習）", "T3 QUANT（60日窓）"):
        assert n in registry.available("detector")
    for n in ("Ridge", "LightGBM", "常に上（ドリフト）", "直前リターンの符号"):
        assert n in registry.available("model")
    key.update(鍵=trk.NAME)
    assert names.trial_name(key) == "own.ts-rank252.ridge.shared@50"
    key.update(鍵="H3 先20日ゲート（全列・学習）")
    assert names.trial_name(key) == "own.h3-gate20.ridge.shared@50"
    key.update(鍵="全部使う（基準）")
    assert names.trial_name(key) == "own.all.ridge.shared@50"
    # 既存の検知器 TR1 の出力は、このモジュールを読んでも 1 ビットも変わらない（登録を上書きしていない）
    p = _panel()
    tr, te = _split(p)
    a, _ = registry.resolve("detector", trk.NAME)(tr, te, _feats(p), _ctx())
    b, _ = trk.tr1(tr, te, _feats(p), _ctx())
    assert np.array_equal(a, b)


def test_rest_state_enters_below_q_and_returns_above_half():
    ts = pd.date_range("2024-01-01", periods=10, freq="B")
    ra = [50.0, 30.0, 5.0, 30.0, 45.0, 60.0, 15.0, 8.0, 50.0, 51.0]
    assert np.array_equal(rest.rest_state(["A"] * 10, ts, ra, 0.1), [100, 100, 0, 0, 0, 100, 100, 0, 0, 100])
    assert np.array_equal(rest.rest_state(["A"] * 10, ts, ra, 0.2), [100, 100, 0, 0, 0, 100, 0, 0, 0, 100])
    # 銘柄をまたがない（B が休んで終わっても C の最初は「持つ」）・入力の並びを崩しても同じ
    sym = np.array(["A"] * 10 + ["B"] * 3 + ["C"] * 2)
    t2 = np.r_[ts.to_numpy(), ts[:3].to_numpy(), ts[:2].to_numpy()]
    r2 = np.r_[ra, [70.0, 5.0, 40.0], [40.0, 40.0]]
    want = np.r_[[100, 100, 0, 0, 0, 100, 100, 0, 0, 100], [100, 0, 0], [100, 100]].astype(float)
    assert np.array_equal(rest.rest_state(sym, t2, r2, 0.1), want)
    perm = np.random.default_rng(0).permutation(len(sym))
    got = np.empty(len(sym))
    got[perm] = rest.rest_state(sym[perm], t2[perm], r2[perm], 0.1)
    assert np.array_equal(got, want)


def test_forward_sum_uses_only_the_given_rows():
    ts = pd.date_range("2024-01-01", periods=5, freq="B")
    sym = np.array(["A"] * 5 + ["B"])
    t2 = np.r_[ts.to_numpy(), ts[:1].to_numpy()]
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 10.0])
    got = rest.forward_sum(sym, t2, y, h=2)
    assert np.allclose(got[:4], [3.0, 5.0, 7.0, 9.0]) and np.isnan(got[4]) and np.isnan(got[5])   # そろわない行は NaN
    perm = np.random.default_rng(1).permutation(6)
    back = np.empty(6)
    back[perm] = rest.forward_sum(sym[perm], t2[perm], y[perm], h=2)
    assert np.allclose(back[:4], got[:4]) and np.isnan(back[4:]).all()


def test_contract_zero_or_hundred_and_wider_q_rests_more():
    p = _panel()
    tr, te = _split(p)
    feats = _feats(p)
    out = {}
    for kind in rest.KINDS:
        for q in rest.QS:
            buy, doc = _det(kind, q)(tr, te, feats, _ctx())
            assert len(buy) == len(te) and set(np.unique(buy)) <= {0.0, 100.0}
            assert doc["columns"] == ["own_ret_1", "own_x0"] and doc["N"] == 252 and doc["尻の日数"] == 252    # ex_ は見ない
            assert doc["q"] == q and doc["戻る線"] == 0.5 and doc["元の予測値"] == kind and doc["尻の行"] == 252 * 6
            assert doc["休む行の割合"] == round(float((buy == 0).mean()), 4) and doc["切り替えの回数"] >= 0
            out[(kind, q)] = buy
        # q が広いほうが休む日は多い（休む日の集合は包含 ＝ 規則から必ず成り立つ）
        assert (out[(kind, 0.2)] <= out[(kind, 0.1)]).all()
    # 評価の y は読まない（列ごと消しても同じ）
    buy2, _ = _det("fwd20", 0.1)(tr, te.drop(columns=["y"]), feats, _ctx())
    assert np.array_equal(buy2, out[("fwd20", 0.1)])


def test_ret_rank_is_tr1_and_models_are_fit_out_of_sample():
    p = _panel()
    tr, te = _split(p)
    feats = _feats(p)
    ts_tr = pd.to_datetime(tr["ts"]).to_numpy()
    n_days = len(np.unique(ts_tr))
    # TR2 の順位は TR1 の買い% と 1 ビットも違わない（予測値が同じ 2 本）
    tail, p_tail, p_te, _ = rest.predictions("ret", tr, te, feats, _ctx())
    assert np.array_equal(tail, np.isin(ts_tr, trk.tail_days(ts_tr)))
    sym = np.r_[tr["symbol"].to_numpy()[tail], te["symbol"].to_numpy()]
    ts = np.r_[ts_tr[tail], pd.to_datetime(te["ts"]).to_numpy()]
    rank = trk.trailing_rank(sym, ts, np.r_[p_tail, p_te])[int(tail.sum()):]
    tr1_buy, _ = trk.tr1(tr, te, feats, _ctx())
    assert np.array_equal(rank, tr1_buy)
    # 学習器を呼ぶ回数と行数: 頭で学んで末尾を当てる 1 本 ＋ 全部で学んで評価を当てる 1 本
    base = registry.resolve("model", "Ridge")
    for kind, lost in (("ret", 0), ("fwd20", rest.FWD - 1)):
        calls = []

        def spy(Xtr, ytr, Xte, ctx):
            calls.append((len(Xtr), len(Xte)))
            return base(Xtr, ytr, Xte, ctx)
        _det(kind, 0.1)(tr, te, feats, {"model": spy, "seed": 0})
        assert calls == [(6 * (n_days - 252 - lost), 6 * 252), (6 * (n_days - lost), len(te))]
    # 末尾の予測は末尾の y を知らない: 訓練の末尾の y を壊しても、末尾の予測値は 1 ビットも変わらない（評価の予測値は変わる）
    tr2 = tr.copy()
    tr2.loc[tail, "y"] = tr2.loc[tail, "y"] + 1.0
    for kind in rest.KINDS:
        _, a_tail, a_te, _ = rest.predictions(kind, tr, te, feats, _ctx())
        _, b_tail, b_te, _ = rest.predictions(kind, tr2, te, feats, _ctx())
        assert np.array_equal(a_tail, b_tail) and not np.array_equal(a_te, b_te)


def test_no_lookahead_within_evaluation():
    p = _panel()
    tr, te = _split(p)
    feats = _feats(p)
    days = np.sort(te["ts"].unique())
    late = (te["ts"] >= days[len(days) // 2]).to_numpy()
    te2 = te.copy()
    te2.loc[late, "own_x0"] = 99.0
    for kind in rest.KINDS:
        for q in rest.QS:
            a, _ = _det(kind, q)(tr, te, feats, _ctx())
            b, _ = _det(kind, q)(tr, te2, feats, _ctx())
            assert np.array_equal(a[~late], b[~late])        # 後ろ半分の列を壊しても、前半分の買い% は 1 ビットも変わらない
        _, _, pa, _ = rest.predictions(kind, tr, te, feats, _ctx())
        _, _, pb, _ = rest.predictions(kind, tr, te2, feats, _ctx())
        assert np.array_equal(pa[~late], pb[~late]) and not np.array_equal(pa[late], pb[late])


def test_leak_columns_reach_the_learner():
    p = _panel()
    p["LEAK_y"] = p["y"]
    tr, te = _split(p)
    y = te["y"].to_numpy()
    for kind in rest.KINDS:
        buy, doc = _det(kind, 0.2)(tr, te, _feats(p), _ctx())
        assert "LEAK_y" in doc["columns"]
        assert (buy == 0).any() and (buy == 100).any()
        assert y[buy == 0].mean() < y[buy == 100].mean() - 0.002          # 先を知る予測値 → 休む日が先を知る
