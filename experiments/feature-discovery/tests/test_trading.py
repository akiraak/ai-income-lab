"""閾値つき売買（rules.md 13 章）の検査。⚠ **プラン trading-validation §4 のテスト方針そのもの。**

シミュレータ（状態機械）・較正（訓練の内側だけ）・日付基準の fold・
台帳の後方互換（⚠ **既存 67 試行が 1 行も変わらない**）・leak の配線検査を通す。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import catalog
from ail.models import calibrate
from ail.validation import checks, splits
from ail.validation.simulate import portfolio_daily, simulate


# --- シミュレータ（状態機械） -------------------------------------------

def test_no_trade_below_threshold():
    """閾値未満は取引しない（コスト 0・ポジション 0）。"""
    r = simulate([55, 55, 55], [0.01, 0.01, 0.01], threshold=60.0)
    assert r["trades"] == 0 and r["cost_bp_total"] == 0.0
    assert (r["pos"] == 0).all() and (r["net_bp"] == 0).all()
    assert r["skip_days"] == 3


def test_sell_signal_while_flat_does_nothing():
    """⚠ **持っていなければ売れない**（買い専用。13-4 の 2）。売り% 100 でも何も起きない。"""
    r = simulate([0, 0, 0], [0.01, -0.02, 0.01], threshold=50.0)   # 売り% = 100
    assert r["trades"] == 0 and (r["net_bp"] == 0).all()


def test_cost_is_charged_only_on_trade_days():
    """コストは建てた日・手仕舞った日だけ **片道 2.5bp**（13-4 の 3）。"""
    # 日 0 で建て（60 > 55）、日 2 で手仕舞い（売り% 60 > 55）。y = 0 でコストだけ見る
    r = simulate([60, 50, 40, 50], [0.0, 0.0, 0.0, 0.0], threshold=55.0, cost_bp=5.0)
    assert r["net_bp"] == pytest.approx([-2.5, 0.0, -2.5, 0.0])
    assert r["trades"] == 1 and r["cost_bp_total"] == 5.0
    assert list(r["pos"]) == [1, 1, 0, 0]


def test_forced_liquidation_at_fold_end():
    """⚠ **fold 末尾で強制清算**（13-4 の 4）。持ったまま終わると最終日に片道を払う。"""
    r = simulate([100, 50, 50], [0.001, 0.001, 0.001], threshold=50.0, cost_bp=5.0)
    assert (r["pos"] == 1).all()
    assert r["cost_bp_total"] == 5.0                       # 建て 2.5 ＋ 清算 2.5
    assert r["net_bp"][-1] == pytest.approx(10.0 - 2.5)    # 最終日も y は得る


def test_buy_and_hold_pays_exactly_one_round_trip():
    """B&H（買い% = 100 の定数指標）のコストは **1 fold ちょうど 5bp**（13-5）。"""
    y = np.random.default_rng(0).normal(0, 0.01, 250)
    r = simulate(np.full(250, 100.0), y, threshold=50.0, cost_bp=5.0)
    assert (r["pos"] == 1).all() and r["trades"] == 1
    assert r["cost_bp_total"] == pytest.approx(5.0)
    assert r["net_bp"].sum() == pytest.approx(y.sum() * 1e4 - 5.0)


def test_hysteresis_band_holds_position():
    """θ=55 は 45〜55 が何もしない帯（13-3）。買い% 50 では建ても手仕舞いもしない。"""
    r = simulate([60, 50, 50, 44], [0.0] * 4, threshold=55.0)
    assert list(r["pos"]) == [1, 1, 1, 0]                  # 50 では持ち続け、44 で手仕舞う


def test_buy_and_sell_never_fire_on_the_same_day():
    """⚠ **θ ≥ 50 なら買いと売りは排他**（13-3 の 2）。全域の買い% で確かめる。"""
    for th in (50.0, 55.0, 60.0):
        for b in np.linspace(0, 100, 201):
            assert not (b > th and (100 - b) > th)


def test_theta_below_50_is_rejected():
    with pytest.raises(ValueError, match="50"):
        simulate([50, 50], [0.0, 0.0], threshold=30.0)


def test_trades_decrease_monotonically_with_theta():
    """閾値を上げると取引回数は単調に減る（増えたら状態機械が壊れている）。"""
    rng = np.random.default_rng(1)
    b = rng.uniform(0, 100, 500)
    y = rng.normal(0, 0.01, 500)
    trades = [simulate(b, y, th)["trades"] for th in (50.0, 55.0, 60.0)]
    assert trades[0] >= trades[1] >= trades[2] > 0


def test_portfolio_is_the_equal_weight_mean():
    """ポートフォリオは銘柄別系列の等加重平均（13-7）。"""
    ts = pd.date_range("2020-01-01", periods=3, freq="D")
    p = portfolio_daily({"A": pd.Series([1.0, 2.0, 3.0], index=ts),
                         "B": pd.Series([3.0, 4.0, 5.0], index=ts)})
    assert list(p.values) == [2.0, 3.0, 4.0]


# --- 較正（Platt） ------------------------------------------------------

def _ridge(Xtr, ytr, Xte, ctx):
    from sklearn.linear_model import Ridge
    return Ridge(alpha=1.0).fit(Xtr, ytr).predict(Xte)


def _toy(n=2000, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"x": rng.normal(0, 1, n)})
    y = 0.5 * X["x"].values + rng.normal(0, 1, n)
    return X, y


def test_calibration_rejects_validation_rows():
    """⚠ **fit は訓練分割の内側だけ**（13-2 の 2）。検証だと宣言された呼び出しは落ちる。"""
    X, y = _toy()
    with pytest.raises(ValueError, match="訓練分割の内側"):
        calibrate.fit(_ridge, X, y, {}, split="validation")


def test_calibration_is_monotone_and_bounded():
    """pred が大きいほど買い% が大きく、0〜100 に収まる。"""
    X, y = _toy()
    cal = calibrate.fit(_ridge, X, y, {})
    grid = np.linspace(-3, 3, 50)
    pct = cal.buy_pct(grid)
    assert (np.diff(pct) > 0).all()                        # 正の関係を学んだので単調増加
    assert pct.min() >= 0.0 and pct.max() <= 100.0
    assert cal.source == "holdout"


def test_calibration_is_deterministic():
    X, y = _toy()
    a = calibrate.fit(_ridge, X, y, {})
    b = calibrate.fit(_ridge, X, y, {})
    assert a.a == b.a and a.b == b.b                       # ビット一致


def test_thin_training_falls_back_to_train_and_says_so():
    """⚠ **(B) の最初の fold は holdout が切れない。** 訓練予測で代用し、fit 元を残す（13-2 の 2）。"""
    X, y = _toy(n=300)                                     # tail_holdout の MIN_TRAIN 未満
    cal = calibrate.fit(_ridge, X, y, {})
    assert cal.source == "train"
    assert cal.doc["source"] == "train"


def test_one_sided_labels_fall_back_to_a_constant():
    """片側しか無い標本では傾きを学ばず、基準率の定数確率に落とす。"""
    X, _ = _toy(n=300)
    y = np.abs(np.random.default_rng(0).normal(1, 0.1, 300))   # 全部正
    cal = calibrate.fit(_ridge, X, y, {})
    assert cal.source == "constant" and cal.a == 0.0
    assert 50.0 < cal.buy_pct([0.0])[0] < 100.0


# --- 日付基準の fold（13-6 の 1） ----------------------------------------

def _two_symbol_panel(n_days=660):
    ts = pd.date_range("2020-01-01", periods=n_days, freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    rows = []
    for s in ("AAA", "BBB"):
        rows.append(pd.DataFrame({"symbol": s, "ts": ts,
                                  "x": rng.normal(0, 1, n_days),
                                  "y": rng.normal(0, 0.01, n_days)}))
    # ⚠ BBB は途中からしか無い（行 rank で切ると境目が銘柄でずれる形）
    rows[1] = rows[1].iloc[n_days // 3:]
    return pd.concat(rows, ignore_index=True)


def test_date_edges_are_shared_between_forms():
    """⚠ **(A) 63 銘柄のパネルと (B) 1 銘柄のパネルで fold 境界の日付が一致する。**"""
    panel = _two_symbol_panel()
    edges = splits.date_edges(panel["ts"], 5)
    folds_a = {f: (te["ts"].min(), te["ts"].max())
               for f, _tr, te in splits.folds_by_dates(panel, edges, 1440.0)}
    one = panel[panel["symbol"] == "AAA"]
    folds_b = {f: (te["ts"].min(), te["ts"].max())
               for f, _tr, te in splits.folds_by_dates(one, edges, 1440.0)}
    assert set(folds_a) == set(folds_b)
    for f in folds_a:
        assert folds_a[f] == folds_b[f]                    # 検証期間の端の日付が同じ


def test_date_folds_purge_the_training_tail():
    """パージ: 検証の直前 `horizon_min` ぶんの訓練行を落とす（walk_forward と同じ向き）。"""
    panel = _two_symbol_panel()
    edges = splits.date_edges(panel["ts"], 5)
    for _f, tr, te in splits.folds_by_dates(panel, edges, 1440.0):
        gap = pd.to_datetime(te["ts"]).min() - pd.to_datetime(tr["ts"]).max()
        assert gap > pd.Timedelta(minutes=1440.0)


# --- 台帳（13-9） --------------------------------------------------------

def _trading_run(form="shared", net=None):
    """閾値売買の実行 1 本ぶんの run 辞書（catalog._run_trials の入力の形）。"""
    methods = {"全部使う（基準）": net if net is not None else [12.0, 11.0, 10.0, 9.0, 8.0],
               "基準 常に上（ドリフト）": [5.0, 5.0, 5.0, 5.0, 5.0]}
    res, summ = [], []
    for th in (50.0, 55.0, 60.0):
        for m, nets in methods.items():
            for f, n in enumerate(nets, 1):
                res.append({"手法": m, "fold": f, "閾値": th, "純利bp": n, "粗利bp": n + 2.0})
            summ.append({"手法": m, "閾値": th, "本数": 35.0, "的中率": 0.52, "IC": 0.03,
                         "粗利bp": float(np.mean(nets)) + 2.0, "純利bp": float(np.mean(nets)),
                         "取引回数": 60.0, "保有日率": 0.5, "fold数": 5})
    summary = pd.DataFrame(summ).set_index("手法")
    return {"実行": "t", "leak": False, "config": {
                "dataset": "daily", "bar_minutes": 1440.0, "horizon": 1, "cost_bp": 5.0,
                "model": "Ridge", "feature_layers": ["own"],
                "trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": form}},
            "inputs": {"layer": "adjusted", "features_meta": {"layer": "adjusted"}},
            "summary": summary, "result": pd.DataFrame(res)}


def test_trading_rows_carry_the_new_key_columns():
    """新方式の行は 検証方式・形式・閾値 の 3 列を持ち、閾値ごとに割れる（13-9 の 1・3）。"""
    rows = catalog._run_trials(_trading_run(form="per_symbol"))
    ours = [r for r in rows if r["手法名"] == "全部使う（基準）"]
    assert len(ours) == 3                                  # 閾値 3 水準で 3 行
    assert {r["閾値"] for r in ours} == {"50", "55", "60"}
    assert all(r["検証方式"] == "閾値売買" and r["形式"] == "銘柄別" for r in ours)
    assert all(r["上乗せbp"] == pytest.approx(5.0) for r in ours)
    assert all(r["fold"] == "5/5 ＋＋＋＋＋" for r in ours)   # ⚠ fold の符号は上乗せ（13-7）


def test_old_runs_read_as_every_day_round_trip():
    """⚠ **旧実行は（毎日往復・共通・—）として読む**（13-9 の 1）。鍵は割れない。"""
    run = _trading_run()
    del run["config"]["trading"]
    run["summary"] = run["summary"][run["summary"]["閾値"] == 50.0].drop(columns=["閾値", "取引回数", "保有日率"])
    rows = catalog._run_trials(run)
    assert all(r["検証方式"] == "毎日往復" and r["形式"] == "共通" and r["閾値"] == "—"
               for r in rows)


def test_existing_67_trials_are_unchanged():
    """⚠ **既存の台帳 67 試行が 1 行も変わらない**（13-9 の 2。後方互換）。"""
    rows, _leak, _runs = catalog.trials()
    old = [r for r in rows if r["検証方式"] == "毎日往復"]
    assert len([r for r in old if catalog.is_trial(r)]) == 67
    # 旧行は全部（毎日往復・共通・—）なので、鍵の 3 列で 1 行も割れていない
    assert all(r["形式"] == "共通" and r["閾値"] == "—" for r in old)


@pytest.mark.parametrize("name,expect", [
    ("全部使う（基準）", True),        # ⚠ 検証方式が処置なので Ridge でも数える（13-9 の 4）
    ("基準 常に上（ドリフト）", False),
    ("基準 直前リターンの符号", False),
    ("乱択（基準）", False),
])
def test_threshold_rows_count_per_threshold(name, expect):
    row = {"検証方式": "閾値売買", "手法名": name, "鍵": name, "モデル": "Ridge"}
    assert catalog.is_trial(row) is expect


BASES = {"全部使う（基準）", "乱択（基準）", "常に上（ドリフト）", "直前リターンの符号"}


def _t_row(**kw):
    base = {"手法名": "全部使う（基準）", "検証方式": "閾値売買", "形式": "共通", "閾値": "55",
            "粒度": "日足", "層": "adjusted", "純利bp": 10.0, "粗利bp": 12.0,
            "上乗せbp": 1.0, "上乗せfold": "5/5 ＋＋＋＋＋"}
    return {**base, **kw}


@pytest.mark.parametrize("r,verdict,inside", [
    (_t_row(), "採る", "DSR"),
    (_t_row(上乗せfold="3/5 ＋＋−−＋"), "保留", "符号が割れる"),
    (_t_row(上乗せbp=-0.5, 上乗せfold="1/5 ＋−−−−"), "落とす", "何も学んでいない"),
    (_t_row(手法名="基準 常に上（ドリフト）"), "基準", "B&H"),
    (_t_row(手法名="乱択（基準）"), "基準", "採否の対象ではない"),
    (_t_row(上乗せbp=None), "保留", "読めない"),
])
def test_trading_judge_follows_rules_13_7(r, verdict, inside):
    """⚠ **判定は対 B&H の上乗せで機械的に決める**（13-7 の表）。"""
    got, why = catalog.judge(r, BASES)
    assert got == verdict
    assert inside in why


def test_old_judge_table_is_untouched():
    """旧方式の行の判定は今までどおり（純利と fold の符号で決まる）。"""
    r = {"手法名": "F1-1 相関", "検証方式": "毎日往復", "粒度": "日足", "層": "adjusted",
         "純利bp": 1.0, "粗利bp": 6.0, "fold": "5/5 ＋＋＋＋＋"}
    got, why = catalog.judge(r, BASES)
    assert got == "採る" and "デフレーテッド SR" in why


# --- 検査（checks.compute_trading） --------------------------------------

def _fake_outputs():
    run = _trading_run()
    res, summary = run["result"], run["summary"]
    ts = pd.date_range("2021-01-01", periods=50, freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    daily = {(m, th): [pd.Series(rng.normal(0.2, 1.0, 50), index=ts)]
             for m in summary.index.unique() for th in (50.0, 55.0, 60.0)}
    sym = [{"手法": m, "閾値": th, "fold": 1, "銘柄": s, "純利bp": v,
            "取引回数": 3, "保有日率": 0.5, "見送り日数": 10}
           for m in summary.index.unique() for th in (50.0, 55.0, 60.0)
           for s, v in (("AAA", 8.0), ("BBB", -2.0), ("CCC", 4.0))]
    return res, summary, pd.DataFrame(sym), daily, run["config"]


def test_compute_trading_keeps_all_three_thresholds():
    """⚠ **3 閾値とも残す**（13-3 の 3。良かった閾値だけ報告しない）。"""
    res, summary, sym, daily, cfg = _fake_outputs()
    doc = checks.compute_trading(res, summary, sym, daily, cfg, n_trials=70)
    assert doc["style"] == "threshold"
    assert set(doc["by_threshold"]) == {"50", "55", "60"}
    e = doc["by_threshold"]["50"]
    assert e["edge_vs_bh"]["mean_bp"] == pytest.approx(5.0)
    assert e["edge_vs_bh"]["positive"] == 5
    assert e["per_symbol"] == {"銘柄数": 3, "中央値bp": 4.0, "四分位bp": [1.0, 6.0], "勝ち銘柄": 2}
    assert e["dsr"]["n_obs"] == 50                        # ⚠ 検証日数で数える（行数 × 銘柄ではない）
    assert doc["best"]["閾値"] in (50.0, 55.0, 60.0)
    assert doc["folds"]["pattern"] == "＋＋＋＋＋"           # ⚠ fold の符号は上乗せで見る


def test_compute_trading_skew_and_kurtosis_come_from_the_series():
    """⚠ **歪度・尖度も系列から実測して渡す**（13-7。旧実装の 0 / 3 の既定値を使わない）。"""
    res, summary, sym, daily, cfg = _fake_outputs()
    doc = checks.compute_trading(res, summary, sym, daily, cfg, n_trials=70)
    d = doc["by_threshold"]["50"]["dsr"]
    assert "歪度" in d and "尖度" in d
    assert d["尖度"] != 3.0 or d["歪度"] != 0.0


# --- 診断列（rules.md 14-3） ---------------------------------------------

def test_edge_bins_split_inside_folds():
    """⚠ **bin は fold 境界をまたがない**（強制清算の位置は動かない。rules.md 14-3）。"""
    ts1 = pd.date_range("2021-01-01", periods=6, freq="D")
    ts2 = pd.date_range("2021-04-01", periods=4, freq="D")
    m = [pd.Series([1.0, 1.0, 1.0, 2.0, 2.0, 2.0], index=ts1),
         pd.Series([5.0, 5.0, -1.0, -1.0], index=ts2)]
    b = [pd.Series(0.0, index=ts1), pd.Series(0.0, index=ts2)]
    d = checks._edge_bins(m, b, per_fold=2)
    assert d["per_fold"] == 2 and d["bins"] == 4
    assert d["values"] == [3.0, 6.0, 10.0, -2.0]           # fold 内で 2 等分した合計
    assert d["pattern"] == "＋＋＋−" and d["positive"] == 3
    assert sum(d["values"]) == pytest.approx(float(sum(s.sum() for s in m)))
    assert "採否" in d["注記"]                              # ⚠ 診断であって判定ではない


def test_edge_bins_refuses_mismatched_series():
    """⚠ **計算できないものは省く**（0 や null で埋めない）。B&H と形が合わなければ None。"""
    ts = pd.date_range("2021-01-01", periods=5, freq="D")
    m = [pd.Series(1.0, index=ts)]
    assert checks._edge_bins(m, None) is None
    assert checks._edge_bins(m, []) is None
    assert checks._edge_bins(m, [pd.Series(1.0, index=ts[:4])]) is None   # 長さ違い
    assert checks._edge_bins(m, m + m) is None                            # fold 数違い


def test_compute_trading_carries_edge_bins_and_breadth():
    """診断列（rules.md 14-3）: edge_bins は常に、breadth は panel があるときだけ。判定の量は不変。"""
    res, summary, sym, daily, cfg = _fake_outputs()
    doc = checks.compute_trading(res, summary, sym, daily, cfg, n_trials=70)
    e = doc["by_threshold"]["50"]
    assert e["edge_bins"]["per_fold"] == 2 and e["edge_bins"]["bins"] == 2
    assert "edge_bins" in doc                              # 最良閾値の写しにも載る
    assert "breadth" not in doc                            # ⚠ panel 無しでは省く
    rng = np.random.default_rng(1)
    ts = pd.date_range("2021-01-01", periods=40, freq="D", tz="UTC")
    panel = pd.DataFrame({"symbol": np.repeat(["AAA", "BBB", "CCC"], 40),
                          "ts": list(ts) * 3, "y": rng.normal(0, 0.01, 120)})
    doc2 = checks.compute_trading(res, summary, sym, daily, cfg, n_trials=70, panel=panel)
    assert doc2["breadth"]["系列数"] == 3
    assert 1.0 <= doc2["breadth"]["実効系列数"] <= 3.0
    assert doc2["by_threshold"]["50"]["dsr"]["n_obs"] == 50   # ⚠ n_obs は検証日数のまま
