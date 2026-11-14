"""登録簿 `20261108-hmm-sticky-regime-hold`（2026-11-13 の段 2・人の手）: 滞在日数に下限のある局面 R4（ガウス HMM・2 ／ 3 状態）。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名・綴り）／ 遷移の対角 ≥ 0.95 ／ 前向きの確率だけ（先の日を見ない）／
   同じ日の全銘柄に同じ買い% ／ 出力の契約（行数・0〜100）／ leak 対照が観測に届く。
"""

from __future__ import annotations

import numpy as np
import pytest
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


# --- 登録簿 `20261108-edgar-8k-event-flags`: 8-K の取得元 `edgar_8k` と印の層 `evt_`（⚠ ネットワークに出ない）-----------------

from ail.data.sources import edgar_8k  # noqa: E402
from ail.ideas import i20261108_evt_layer as evt  # noqa: E402


def test_8k_source_parses_forms_items_and_counts_without_network():
    assert "edgar_8k" in registry.available("source") and "evt" in registry.available("feature")
    for n in ("edgar", "ecb", "treasury"):
        assert n in registry.available("source")                                # 既存の取得元は前のまま
    doc = {"filings": {"recent": {"form": ["8-K", "10-Q", "8-K/A", "8-K", "8-K", "4"],
                                  "items": ["2.02,9.01", "", "5.02", "5.02, 9.01", "8.01", ""],
                                  "filingDate": ["2024-02-01", "2024-02-05", "2024-03-01", "2024-03-01", "2017-05-05", "2024-03-02"]}}}
    old = {"form": ["8-K"], "items": ["1.01"], "filingDate": ["2019-07-07"]}    # 古い頁は本体と同じ形（filings の外）
    rows = edgar_8k.filings([doc, old], "2018-01-01")
    assert rows == [("2019-07-07", ["1.01"]), ("2024-02-01", ["2.02", "9.01"]), ("2024-03-01", ["5.02"]), ("2024-03-01", ["5.02", "9.01"])]
    c = edgar_8k.counts(rows)
    assert c["K8"] == {"2019-07-07": 1.0, "2024-02-01": 1.0, "2024-03-01": 2.0}
    assert c["K8I502"] == {"2024-03-01": 2.0} and c["K8I202"] == {"2024-02-01": 1.0}
    assert edgar_8k.SERIES == ("K8", "K8I502", "K8I202")


def _bars(start="2024-02-26", n=12):
    ts = pd.date_range(start, periods=n, freq="B", tz="UTC")
    return pd.DataFrame({"ts": ts, "close": 100.0})


def _series(days: dict[str, float]) -> pd.Series:
    return pd.Series(list(days.values()), index=pd.DatetimeIndex(sorted(days)), dtype=float)


def test_evt_layer_uses_only_filings_before_the_bar_and_fills_missing_with_a_flag():
    bars = _bars()                     # 2/26（月）〜 3/12（火）
    series = {"K8": _series({"2024-02-29": 1.0, "2024-03-01": 2.0}), "K8I502": _series({"2024-03-01": 1.0}),
              "K8I202": _series({"2024-02-29": 1.0})}
    x = evt.build_one(bars, series, lag_days=1)
    day = {str(t.date()): i for i, t in enumerate(bars["ts"])}
    assert list(x.columns) == list(evt.COLUMNS) and len(x) == len(bars) and not x.isna().any().any()
    # 2/29（木）の提出は 3/1（金）の足から・3/1（金）の提出は 3/4（月）の足から（提出の当日には使わない）
    assert x["8k_d1"].tolist() == [0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0]
    assert x["8k_202_d1"].iloc[day["2024-03-01"]] == 1 and x["8k_202_d1"].sum() == 1
    assert x["8k_502_d1"].iloc[day["2024-03-04"]] == 1 and x["8k_502_d1"].sum() == 1
    assert x["8k_cnt20"].tolist() == [0, 0, 0, 0, 1, 3, 3, 3, 3, 3, 3, 3]
    assert x["na_8k"].tolist() == [1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    assert x["days_since_8k"].iloc[day["2024-02-29"]] == evt.MAX_DAYS            # まだ 1 件も無い ＝ 埋め値
    assert x["days_since_8k"].iloc[day["2024-03-01"]] == 0.0                     # 3/1 の足が見るのは 2/29 まで ＝ 当日の提出
    assert x["days_since_8k"].iloc[day["2024-03-04"]] == 2.0                     # 3/4 の足が見るのは 3/3 まで ＝ 3/1 から 2 日
    assert x["days_since_8k"].iloc[day["2024-03-12"]] == 10.0
    # 先の提出を足しても、前の足の列は 1 ビットも変わらない
    later = {k: pd.concat([v, _series({"2024-03-08": 5.0})]) for k, v in series.items()}
    x2 = evt.build_one(bars, later, lag_days=1)
    cut = day["2024-03-08"] + 1
    assert x.iloc[:cut].equals(x2.iloc[:cut]) and not x.iloc[cut:].equals(x2.iloc[cut:])
    # 系列の無い銘柄 ＝ 印 1 で残る（行を落とさない）
    x0 = evt.build_one(bars, {}, lag_days=1)
    assert (x0["na_8k"] == 1).all() and (x0["days_since_8k"] == evt.MAX_DAYS).all() and (x0[["8k_d1", "8k_cnt20"]] == 0).all().all()
    # 件数の窓は直近 20 本
    long = _bars("2024-01-02", 40)
    xl = evt.build_one(long, {"K8": _series({"2024-01-03": 1.0})}, lag_days=1)
    assert xl["8k_cnt20"].iloc[2] == 1 and xl["8k_cnt20"].iloc[21] == 1 and xl["8k_cnt20"].iloc[22] == 0


def test_evt_layer_refuses_same_day_lag_and_keeps_layer_order(monkeypatch):
    from cli import build as build_mod
    assert build_mod.layer_order(["own", "evt"]) == ["own", "evt"] and build_mod.layer_order(["own", "cs"]) == ["own", "cs"]
    assert "evt" not in build_mod.ORDER
    monkeypatch.setattr(evt, "load_symbol_series", lambda s: {"K8": _series({"2024-02-29": 1.0})} if s == "AAA" else {})
    out = evt.layer({"AAA": _bars(), "BBB": _bars()}, {})
    assert list(out["AAA"].columns) == [evt.PREFIX + c for c in evt.COLUMNS] and out["BBB"]["evt_na_8k"].eq(1).all()
    with pytest.raises(ValueError):
        evt.layer({"AAA": _bars()}, {"evt_lag_days": 0})
