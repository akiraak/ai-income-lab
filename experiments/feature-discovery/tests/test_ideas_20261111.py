"""登録簿 `20261111-meta-label-filter`（2026-11-13 の段 2・人の手）: 一段目の合図の当たりを当てる二段目 M1（メタラベル）。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名・綴り）／ ラベルと当たりの割合の定義 ／ 出力は「一段目の買い% か 50」・
   出口は一段目のまま ／ 先の日を見ない ／ 間引けないときは一段目そのまま ／ leak 対照が一段目に届く。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import names, registry
from ail.ideas import i20261111_meta_label as ml
from ail.models import calibrate


def _panel(n_days=900, n_sym=8, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-01", periods=n_days, freq="B", tz="UTC")
    rows = []
    for j in range(n_sym):
        x = rng.normal(size=n_days)
        r = 0.004 * x + rng.normal(0.0, 0.01, n_days)          # own_x0 が翌日の y を少し当てる
        df = pd.DataFrame({"symbol": f"S{j}", "ts": ts, "close": 50.0, "y": r})
        df["own_x0"] = x
        df["own_x1"] = rng.normal(size=n_days)
        rows.append(df)
    return pd.concat(rows, ignore_index=True).sort_values(["ts", "symbol"]).reset_index(drop=True)


def _split(p, cut_frac=0.8):
    days = np.sort(p["ts"].unique())
    cut = days[int(len(days) * cut_frac)]
    return p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


def _feats(p):
    return [c for c in p.columns if c.startswith(("own_", "LEAK_"))]


def _ctx(model="Ridge"):
    return {"model": registry.resolve("model", model), "seed": 0, "lgbm_estimators": 30}


def test_new_name_registered_spelled_and_existing_untouched():
    assert ml.NAME in registry.available("detector")
    for n in ("H1 先10日ゲート（全列・学習）", "T3 QUANT（60日窓）"):
        assert n in registry.available("detector")
    key = dict(鍵=ml.NAME, モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own", 層="adjusted", 期間="2018-01-31",
               銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key) == "own.meta-t1.ridge.shared@50"               # 登録簿の key_shape
    key.update(モデル="LightGBM", 閾値="60")
    assert names.trial_name(key) == "own.meta-t1.lgbm.shared@60"
    assert ml.LINE == 50.0 and ml.COST == 5.0e-4 and ml.HIT_WINDOW == 20 and ml.STAGE1 == "Ridge"


def test_trade_gross_and_hit_rate_definitions():
    ts = pd.date_range("2024-01-01", periods=6, freq="B")
    sym = ["A"] * 6
    b = [60.0, 55.0, 40.0, 70.0, 50.0, 45.0]
    y = [0.01, 0.02, -0.03, 0.04, 0.05, 0.06]
    # 行 0: 次の日も 50 以上 → 行 1 まで持つ・行 2（40）の前で降りる ＝ 0.01 + 0.02 ／ 行 3: 行 4（50 ちょうど）は持つ・行 5（45）の前で降りる
    # （行 2 は合図ではないので学習には使わないが、定義は同じ: その日に建てたら行 4 まで ＝ −0.03 + 0.04 + 0.05）
    assert np.allclose(ml.trade_gross(sym, ts, b, y), [0.03, 0.02, 0.06, 0.09, 0.05, 0.06])
    # 当たり ＝ 買い% − 50 と y の符号が同じ: [1, 1, 1, 1, 0, 0] → その日より前だけの平均（最初の日は 0.5）
    assert np.allclose(ml.hit_rate(sym, ts, b, y, n=2), [0.5, 1.0, 1.0, 1.0, 1.0, 0.5])
    # 銘柄をまたがない・入力の並びによらない
    sym2 = np.array(sym + ["B"] * 6)
    t2 = np.r_[ts.to_numpy(), ts.to_numpy()]
    perm = np.random.default_rng(0).permutation(12)
    got = np.empty(12)
    got[perm] = ml.trade_gross(sym2[perm], t2[perm], np.r_[b, b][perm], np.r_[y, y][perm])
    assert np.allclose(got[:6], got[6:]) and np.allclose(got[:6], [0.03, 0.02, 0.06, 0.09, 0.05, 0.06])


def test_m1_output_is_stage1_or_50_exit_is_stage1_and_no_lookahead():
    p = _panel()
    tr, te = _split(p)
    feats = _feats(p)
    ridge = registry.resolve("model", "Ridge")
    from sklearn.preprocessing import StandardScaler
    sc = StandardScaler().fit(tr[feats])
    Xtr, Xte = pd.DataFrame(sc.transform(tr[feats]), columns=feats), pd.DataFrame(sc.transform(te[feats]), columns=feats)
    plain = np.asarray(calibrate.fit(ridge, Xtr, tr["y"].to_numpy(), {"seed": 0}).buy_pct(ridge(Xtr, tr["y"].to_numpy(), Xte, {"seed": 0})))
    for model in ("Ridge", "LightGBM"):
        buy, ex, doc = registry.resolve("detector", ml.NAME)(tr, te, feats, _ctx(model))
        assert len(buy) == len(te) == len(ex) and np.isfinite(buy).all() and ((buy >= 0) & (buy <= 100)).all()
        assert np.allclose(ex, 100.0 - plain)                                  # 出口は一段目のまま
        kept = buy != 50.0
        assert np.allclose(buy[kept], plain[kept]) and (plain[kept] > 50).all()  # 通した行は一段目の買い%（合図の行だけ）
        assert doc["間引き"] == "あり" and 0.0 <= doc["通した合図の割合"] <= 1.0 and doc["二段目の学習行"] >= ml.MIN_ROWS
        assert doc["columns"] == ["own_x0", "own_x1"] and 0.0 < doc["土台（二段目の学習行での当たりの割合）"] < 1.0
    # 先の日を見ない: 評価期間の後ろ半分の列と y を壊しても、前半分の出力は 1 ビットも変わらない
    buy, ex, _ = registry.resolve("detector", ml.NAME)(tr, te, feats, _ctx())
    days = np.sort(te["ts"].unique())
    late = (te["ts"] >= days[len(days) // 2]).to_numpy()
    te2 = te.copy()
    te2.loc[late, "own_x0"] = 9.0
    te2.loc[late, "y"] = -1.0
    buy2, ex2, _ = registry.resolve("detector", ml.NAME)(tr, te2, feats, _ctx())
    assert np.array_equal(buy[~late], buy2[~late]) and np.array_equal(ex[~late], ex2[~late])


def test_m1_falls_back_to_stage1_when_it_cannot_filter():
    p = _panel(n_days=120, n_sym=2)                                             # 訓練が薄い ＝ 二段目の学習行が足りない
    tr, te = _split(p)
    buy, ex, doc = registry.resolve("detector", ml.NAME)(tr, te, _feats(p), _ctx())
    assert doc["間引き"].startswith("なし") and doc["通した合図の割合"] == 1.0 and np.allclose(ex, 100.0 - buy)


def test_m1_leak_columns_reach_stage1():
    p = _panel()
    p["LEAK_y"] = p["y"]
    tr, te = _split(p)
    buy, ex, doc = registry.resolve("detector", ml.NAME)(tr, te, _feats(p), _ctx())
    assert "LEAK_y" in doc["columns"]
    assert np.corrcoef(100.0 - ex, te["y"].to_numpy())[0, 1] > 0.5             # 一段目が先を知る
    held = buy > 50
    assert held.any() and (te["y"].to_numpy()[held] > 0).mean() > 0.9          # 通した合図はほぼ全部が上がる


# --- 登録簿 `20261111-sector-pair-revert`: 同じセクターの相手との開きが縮むかを当てて出遅れを買う PR1 ---------------------

from ail.ideas import i20261111_sector_pair as pr  # noqa: E402


def _pair_panel(n_days=700, seed=0):
    """XLK の 3 社（AAPL と MSFT は同じ動き ＋ 戻る開き・NVDA は別）・XLY の 2 社・相手のいない XOM・ETF の SPY。"""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-01", periods=n_days, freq="B", tz="UTC")
    base = rng.normal(0.0003, 0.01, n_days)
    ou = np.zeros(n_days)
    for t in range(1, n_days):
        ou[t] = 0.9 * ou[t - 1] + rng.normal(0, 0.01)             # 戻る開き
    rets = {"AAPL": base + np.r_[0.0, np.diff(ou)], "MSFT": base + rng.normal(0, 0.001, n_days), "NVDA": rng.normal(0.0005, 0.02, n_days),
            "AMZN": rng.normal(0.0003, 0.012, n_days), "TSLA": rng.normal(0.0003, 0.03, n_days),
            "XOM": rng.normal(0.0002, 0.012, n_days), "SPY": base * 0.8}
    rets["TSLA"] = rets["TSLA"] + 0.5 * rets["AMZN"]
    rows = []
    for s, r in rets.items():
        df = pd.DataFrame({"symbol": s, "ts": ts, "close": 100 * np.exp(np.cumsum(r)), "y": np.r_[r[1:], np.nan]})
        df["own_ret_1"] = r
        df["own_x0"] = rng.normal(size=n_days)
        rows.append(df)
    return pd.concat(rows, ignore_index=True).dropna(subset=["y"]).sort_values(["ts", "symbol"]).reset_index(drop=True)


def test_pr1_name_spelling_and_partners_from_training_only():
    assert pr.NAME in registry.available("detector")
    key = dict(鍵=pr.NAME, モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own", 層="adjusted", 期間="2018-01-31",
               銘柄="63", 検証方式="閾値売買", 形式="共通", 較正="std", 閾値="55")
    assert names.trial_name(key) == "own.spread-revert5.ridge.shared@55"        # ⚠ 頭は own.（表の層）
    assert pr.FWD == 5 and pr.WINDOWS == (20, 60)
    sectors = pr.sector_of()
    assert sectors["AAPL"] == sectors["MSFT"] == sectors["NVDA"] == "XLK" and "SPY" not in sectors
    p = _pair_panel()
    tr, te = _split(p)
    pairs = pr.partners(tr, sectors)
    assert pairs["AAPL"] == "MSFT" and pairs["MSFT"] == "AAPL" and pairs["NVDA"] in ("AAPL", "MSFT")
    assert pairs["AMZN"] == "TSLA" and pairs["TSLA"] == "AMZN" and "XOM" not in pairs and "SPY" not in pairs
    te2 = te.assign(own_ret_1=te["own_ret_1"] * -5)                             # 評価分割を壊しても組は同じ（訓練分割だけで決める）
    assert pr.partners(tr, sectors) == pairs and te2 is not te


def test_pr1_buys_only_laggards_exits_when_gap_closes_and_no_lookahead():
    p = _pair_panel()
    tr, te = _split(p)
    feats = _feats(p)
    buy, ex, doc = registry.resolve("detector", pr.NAME)(tr, te, feats, _ctx())
    assert len(buy) == len(te) == len(ex) and np.isfinite(buy).all() and ((buy >= 0) & (buy <= 100)).all()
    assert doc["学習"] == "あり" and doc["組のある会社"] == 5 and doc["columns"] == ["own_ret_1", "own_x0", "__z20", "__z60"]
    sym = te["symbol"].to_numpy()
    out = np.isin(sym, ["XOM", "SPY"])
    assert (buy[out] == 50).all() and (ex[out] == 100).all()                    # 相手のいない会社と ETF は対象外
    lag = ex != 100.0
    assert lag.any() and np.allclose(ex[lag], 100.0 - buy[lag]) and (buy[~lag] == 50).all()
    # 出遅れの印は「その日までの終値」の 60 日の z < 0 と同じ
    both = pd.concat([tr, te])
    close = both.pivot_table(index="ts", columns="symbol", values="close")
    close.index = pd.to_datetime(close.index)
    z60 = pr.spreads(close, doc["組"])["z60"]
    want = pr._lookup(z60, pd.to_datetime(te["ts"]), sym) < 0
    assert np.array_equal(lag, want)
    # 先の日を見ない: 評価期間の後ろ半分の終値と列を壊しても、前半分の出力は 1 ビットも変わらない
    days = np.sort(te["ts"].unique())
    late = (te["ts"] >= days[len(days) // 2]).to_numpy()
    te2 = te.copy()
    te2.loc[late, "close"] = te2.loc[late, "close"] * 3.0
    te2.loc[late, "own_x0"] = 9.0
    buy2, ex2, _ = registry.resolve("detector", pr.NAME)(tr, te2, feats, _ctx())
    assert np.array_equal(buy[~late], buy2[~late]) and np.array_equal(ex[~late], ex2[~late]) and not np.array_equal(ex[late], ex2[late])


def test_pr1_labels_stay_inside_training_and_leak_adds_the_future_gap():
    p = _pair_panel()
    tr, te = _split(p)
    close = tr.pivot_table(index="ts", columns="symbol", values="close")
    close.index = pd.to_datetime(close.index)
    sp = pr.spreads(close, {"AAPL": "MSFT"})
    d, mean = sp["d"]["AAPL"], sp["mean"]["AAPL"]
    t = 300
    assert np.isclose(d.iloc[t], np.log(close["AAPL"].iloc[t] / close["MSFT"].iloc[t]))
    assert np.isclose(mean.iloc[t], d.iloc[t - 59:t + 1].mean())                # その日までの 60 日
    assert np.isclose(sp["z20"]["AAPL"].iloc[t], (d.iloc[t] - mean.iloc[t]) / d.iloc[t - 19:t + 1].std())
    p["LEAK_y"] = p["y"]
    tr, te = _split(p)
    buy, ex, doc = registry.resolve("detector", pr.NAME)(tr, te, _feats(p), _ctx())
    assert doc["columns"][-1] == "__leak_d5" and "LEAK_y" in doc["columns"]
    thin, _te = _split(_pair_panel(n_days=200), 0.5)                            # 訓練が薄い ＝ 学ばない（全部 50 ／ 100）
    b0, e0, d0 = registry.resolve("detector", pr.NAME)(thin, _te, [c for c in thin.columns if c.startswith("own_")], _ctx())
    assert d0["学習"].startswith("なし") and (b0 == 50).all() and (e0 == 100).all()
