"""登録簿の 2 本（2026-11-13 の段 2・人の手）: 自分の直近 252 日の予測値の中の順位 TR1 ／ 本物のリターンへの植え込み（cli.inject_power）。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名・綴り）／ 先の日を見ない（順位は その行と前の日の予測値だけ）／
   尻の予測は尻を外して学んだ 1 本（訓練内の当てはめ値を使わない）／ 出力の契約（行数・0〜100）／ 識別項目の綴り ／
   植え込みは本物の y を残す・種が同じなら列は同じ・水準は事前固定。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import names, registry
from ail.ideas import i20261113_score_trailing_rank as trk
from cli import inject_power as ip
from cli import planted_power as pp


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


def test_new_name_registered_spelled_and_existing_untouched():
    assert trk.NAME in registry.available("detector")
    for n in ("H1 先10日ゲート（全列・学習）", "T3 QUANT（60日窓）", "P1 60日の形の塊ごとの上がる割合（k-means・8個）"):
        assert n in registry.available("detector")
    for n in ("Ridge", "LightGBM", "常に上（ドリフト）", "直前リターンの符号"):
        assert n in registry.available("model")
    key = dict(鍵=trk.NAME, モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own", 層="adjusted", 期間="2018-01-31",
               銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key) == "own.ts-rank252.ridge.shared@50"            # 登録簿の key_shape
    key.update(鍵="全部使う（基準）")
    assert names.trial_name(key) == "own.all.ridge.shared@50"                   # 対の相手の綴りは前のまま


def test_trailing_rank_uses_only_the_row_and_earlier_days():
    ts = pd.date_range("2024-01-01", periods=6, freq="B")
    buy = trk.trailing_rank(["A"] * 6, ts, [1.0, 2.0, 3.0, 0.0, 3.0, 9.0], n=3)
    # 並び: [1] ／ [1,2] ／ [1,2,3] ／ [2,3,0] ／ [3,0,3]（同じ値は平均の順位）／ [0,3,9]
    assert np.allclose(buy, [50.0, 75.0, 100 * 2.5 / 3, 100 * 0.5 / 3, 100 * 2.0 / 3, 100 * 2.5 / 3])
    # 入力の並びを崩しても、銘柄をまたいでも同じ（銘柄ごと・日付の順に並べ直す）
    sym = np.array(["A"] * 6 + ["B"] * 6)
    t2 = np.r_[ts.to_numpy(), ts.to_numpy()]
    p2 = np.r_[[1.0, 2.0, 3.0, 0.0, 3.0, 9.0], [5.0, 4.0, 3.0, 2.0, 1.0, 0.0]]
    perm = np.random.default_rng(0).permutation(12)
    got = np.empty(12)
    got[perm] = trk.trailing_rank(sym[perm], t2[perm], p2[perm], n=3)
    assert np.allclose(got[:6], buy) and np.allclose(got[6:], [50.0, 25.0, 100 * 0.5 / 3, 100 * 0.5 / 3, 100 * 0.5 / 3, 100 * 0.5 / 3])


def test_tr1_contract_tail_is_out_of_sample_and_no_lookahead():
    p = _panel()
    tr, te = _split(p)
    feats = _feats(p)
    buy, doc = registry.resolve("detector", trk.NAME)(tr, te, feats, _ctx())
    assert len(buy) == len(te) and np.isfinite(buy).all() and ((buy > 0) & (buy < 100)).all()
    assert doc["columns"] == ["own_ret_1", "own_x0"] and doc["N"] == 252 and doc["尻の日数"] == 252        # ex_ は見ない
    assert doc["尻の行"] == 252 * 6 and doc["頭の行"] == len(tr) - 252 * 6 and doc["買い%の幅"] > 50
    # 先の日を見ない: 評価期間の後ろ半分の列を壊しても、前半分の買い% は 1 ビットも変わらない
    days = np.sort(te["ts"].unique())
    late = (te["ts"] >= days[len(days) // 2]).to_numpy()
    te2 = te.copy()
    te2.loc[late, "own_x0"] = 99.0
    buy2, _ = registry.resolve("detector", trk.NAME)(tr, te2, feats, _ctx())
    assert np.array_equal(buy[~late], buy2[~late]) and not np.array_equal(buy[late], buy2[late])
    # 尻の予測は尻を外して学んだ 1 本: 尻の y を壊しても、評価の予測値（全部で学ぶ 1 本）が変わるぶんしか動かない ＝ 尻の y だけでは尻の予測は変わらない
    ts_tr = pd.to_datetime(tr["ts"]).to_numpy()
    tail = np.isin(ts_tr, trk.tail_days(ts_tr))
    calls = []
    base = registry.resolve("model", "Ridge")

    def spy(Xtr, ytr, Xte, ctx):
        calls.append((len(Xtr), len(Xte)))
        return base(Xtr, ytr, Xte, ctx)
    registry.resolve("detector", trk.NAME)(tr, te, feats, {"model": spy, "seed": 0})
    assert calls == [(int((~tail).sum()), int(tail.sum())), (len(tr), len(te))]
    # 訓練が 2N 日に満たなければ尻は後ろ半分
    short = np.sort(pd.unique(ts_tr))[:300]
    assert len(trk.tail_days(short)) == 150


def test_tr1_leak_columns_reach_the_learner():
    p = _panel()
    p["LEAK_y"] = p["y"]
    tr, te = _split(p)
    buy, doc = registry.resolve("detector", trk.NAME)(tr, te, _feats(p), _ctx())
    assert "LEAK_y" in doc["columns"]
    assert np.corrcoef(buy, te["y"].to_numpy())[0, 1] > 0.5                    # 先を知る予測値 → 順位も先を知る


def test_inject_keeps_real_returns_and_levels_are_fixed():
    p = _panel(n_days=300)
    a, b, c = ip.inject(p, 0.0, 3), ip.inject(p, 8.0, 3), ip.inject(p, 8.0, 4)
    assert np.array_equal(a["y"].to_numpy(), p["y"].to_numpy())               # k 0 ＝ 本物の y のまま
    x = a[pp.PLANTED].to_numpy()
    assert np.array_equal(x, b[pp.PLANTED].to_numpy()) and not np.array_equal(x, c[pp.PLANTED].to_numpy())   # 種が同じなら列は同じ
    assert np.allclose(b["y"].to_numpy() - p["y"].to_numpy(), 8.0e-4 * np.sign(x))
    assert abs(np.corrcoef(x, p["y"].to_numpy())[0, 1]) < 0.1                 # 列は本物のリターンと相関しない
    assert ip.MULTIPLES == (0.1, 1.0, 10.0) and ip.THETAS == (50.0, 55.0, 60.0) and ip.SEEDS == 5 and ip.LIMIT_BP_PER_FOLD == 300.0
    L, ks = ip.levels([300, 300, 300, 300, 300])
    assert L == 1.0 and ks == [0.1, 1.0, 10.0]
