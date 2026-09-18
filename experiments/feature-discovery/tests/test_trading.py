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


def test_calibration_is_scale_invariant():
    """⚠ **予測を定数倍しても買い% は変わらない**（2026-09-12 の処置。rules.md 13-2 の 6）。

    ⚠ **これが 2026-09-12 まで壊れていた** — `lbfgs` の `tol` は平均対数損失の**勾配**で
    ⚠ **収束を判定し、その勾配は予測のスケールに比例する。** 1 日リターンの尺度
    （σ ≈ 0.0017）では 1 反復で「収束」と判定され、傾きが初期値 0 から動かなかった。
    ⚠ **買い% の幅が 0.00002 点に潰れ、θ を跨げず、手法が違っても売買が 1 ビットも
    ⚠ **違わなくなっていた**（[buy-pct-width-collapse.md](
    ../../../docs/specs/experiments/buy-pct-width-collapse.md)）。
    """
    rng = np.random.default_rng(0)
    n = 4000
    pred = rng.normal(0, 1, n)
    target = np.where(0.4 * pred + rng.normal(0, 1, n) > 0, 1.0, -1.0)
    base = calibrate.fit_from_predictions(pred, target, "holdout")
    for scale in (1e-1, 1e-2, 1e-3, 1e-4, 1e-6):
        cal = calibrate.fit_from_predictions(pred * scale, target, "holdout")
        # ⚠ **同じ標本・同じ目的関数なので、買い% は一致しなければならない**
        assert np.allclose(cal.buy_pct(pred * scale), base.buy_pct(pred), atol=1e-6), scale
        # ⚠ **幅が縮まないこと**（潰れていたときはここが 0 になった）
        assert np.ptp(np.percentile(cal.buy_pct(pred * scale), [5, 95])) > 10.0, scale


def test_calibration_reaches_the_maximum_likelihood():
    """⚠ **止まっていないことを尤度で確かめる**（プラン §2-1 の決定的な検定）。

    ⚠ **`a` の値だけでは「情報が無い」と「解けていない」を区別できない。** 区別できるのは尤度。
    ⚠ **傾きを少し動かして尤度が上がるなら、解が止まっている。**
    """
    rng = np.random.default_rng(1)
    n = 4000
    pred = rng.normal(0, 0.0017, n)                        # ⚠ 1 日リターンの尺度
    target = np.where(0.1 * pred / 0.0017 + rng.normal(0, 1, n) > 0, 1.0, -1.0)
    cal = calibrate.fit_from_predictions(pred, target, "holdout")
    up = target > 0

    def ll(a, b):
        p = 1.0 / (1.0 + np.exp(-np.clip(a * pred + b, -500, 500)))
        return float(np.mean(np.where(up, np.log(p + 1e-12), np.log1p(-p + 1e-12))))

    best = ll(cal.a, cal.b)
    for f in (0.5, 0.8, 1.25, 2.0):
        assert ll(cal.a * f, cal.b) <= best + 1e-9, f      # ⚠ 傾きをずらすと必ず下がる


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


def test_old_rows_are_not_split_by_the_trading_columns():
    """⚠ **13 章が足した 3 列（検証方式・形式・閾値）では旧行が 1 行も割れない**（13-9 の 2）。

    ⚠ **試行数そのものは釘付けしない。** 鍵に列を足すたびに、まとまっていた行が割れて増える:

      - 2026-09-12 に**期間**を足した → 67 → 112（rules.md 14-4）
      - 2026-09-12 に**銘柄**（実行が読んだ本数）を足した → 112 → 121
        ⚠ **48 本の断面の実行と 63 本の実行が同じ鍵にまとまっていた。**
        ⚠ **増えるのは厳しい側であり、数え落としを直したということである**（11 章 規約 4）

    ⚠ **どちらも数字は再計算していない。** この検査の目的は「13 章の 3 列が旧行を割っていないこと」なので、
    ⚠ **そちらを見る**（合計は割れた結果として動いてよい）。
    """
    rows, _leak, _runs = catalog.trials()
    old = [r for r in rows if r["検証方式"] == "毎日往復"]
    assert all(r["形式"] == "共通" and r["閾値"] == "—" for r in old)
    assert len([r for r in old if catalog.is_trial(r)]) == 121


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


# --- 1 取引ごとの保有日数（rules.md 13-4 の 6。プラン holding-days-distribution §4） -------------

def test_hold_days_sum_to_position_days_and_count_trades():
    """`sum(hold_days) == pos.sum()`・`len(hold_days) == trades`。⚠ **既存の鍵の計算には触っていない。**"""
    rng = np.random.default_rng(3)
    b = rng.uniform(0, 100, 400)
    y = rng.normal(0, 0.01, 400)
    for th in (50.0, 55.0, 60.0):
        r = simulate(b, y, th)
        assert sum(r["hold_days"]) == int(r["pos"].sum())
        assert len(r["hold_days"]) == r["trades"] == len(r["entry_idx"])
        assert all(k >= 1 for k in r["hold_days"])
        # 建てた足の添字は pos が 0 → 1 に変わる位置
        assert r["entry_idx"] == [t for t in range(400) if r["pos"][t] == 1 and (t == 0 or r["pos"][t - 1] == 0)]


def test_open_then_close_next_day_is_one_day():
    """足 t で建て t+1 で手仕舞うと **1 日**（pos は t だけ 1）。"""
    r = simulate([60, 40, 50, 50], [0.0, 0.0, 0.0, 0.0], threshold=55.0)   # 日 0 建て、日 1 売り% 60 で手仕舞い
    assert r["hold_days"] == [1] and r["entry_idx"] == [0] and r["forced_close"] is False
    assert list(r["pos"]) == [1, 0, 0, 0]


def test_forced_close_marks_the_last_trade_and_counts_to_the_end():
    """末尾で保有中なら最後の要素が末尾までの日数で、`forced_close` が立つ。"""
    r = simulate([100, 50, 50, 50, 50], [0.0] * 5, threshold=50.0)
    assert r["forced_close"] is True and r["hold_days"] == [5] and r["trades"] == 1
    r2 = simulate([60, 50, 40, 60, 50], [0.0] * 5, threshold=55.0)    # 建て → 手仕舞い → 建て → 末尾
    assert r2["hold_days"] == [2, 2] and r2["forced_close"] is True and r2["entry_idx"] == [0, 3]


def test_hold_days_are_the_same_through_the_exit_pct_path():
    """`exit_pct` を渡す経路（rules.md 16-1）でも同じ保有日数が出る（省くと 100 − 入口% と一致）。"""
    rng = np.random.default_rng(5)
    b = rng.uniform(0, 100, 300)
    y = rng.normal(0, 0.01, 300)
    a = simulate(b, y, 55.0)
    c = simulate(b, y, 55.0, exit_pct=100.0 - b)
    assert a["hold_days"] == c["hold_days"] and a["forced_close"] == c["forced_close"]


def test_shifted_gate_keeps_the_total_hold_days():
    """⚠ **乱択ゲートは保有日数を保つ**（14-6 b）。巡回シフトの継ぎ目で本数だけ ±1 まで。"""
    from ail.validation.simulate import shifted_gate
    rng = np.random.default_rng(7)
    b = rng.uniform(0, 100, 500)
    y = rng.normal(0, 0.01, 500)
    r = simulate(b, y, 55.0)
    g = shifted_gate(r["pos"], y, 5.0, np.random.default_rng(1))
    assert sum(g["hold_days"]) == sum(r["hold_days"])
    assert abs(len(g["hold_days"]) - len(r["hold_days"])) <= 1


# --- 逆売買は補集合（rules.md 14-3 の (3)。プラン reverse-trading-check §2-3） ----------------

def _complement_pair(b, y, th, exit_pct=None):
    e = (100.0 - np.asarray(b)) if exit_pct is None else np.asarray(exit_pct)
    fwd = simulate(b, y, th, 5.0, exit_pct=exit_pct)
    rev = simulate(e, y, th, 5.0, exit_pct=np.asarray(b))
    return fwd, rev


@pytest.mark.parametrize("th", [50.0, 55.0, 60.0])
def test_reverse_is_the_complement_after_the_first_signal(th):
    """⚠ **最初の合図の日以降、逆のポジションは 1 − 元**。売買日も同じで、取引回数は ±1 以内。"""
    rng = np.random.default_rng(11)
    b = rng.uniform(0, 100, 600)
    y = rng.normal(0.0002, 0.01, 600)
    fwd, rev = _complement_pair(b, y, th)
    first = next((t for t in range(600) if fwd["pos"][t] == 1 or rev["pos"][t] == 1), None)
    assert first is not None
    assert (fwd["pos"][:first] == 0).all() and (rev["pos"][:first] == 0).all()
    assert np.array_equal(rev["pos"][first:], 1 - fwd["pos"][first:])
    assert abs(fwd["trades"] - rev["trades"]) <= 1
    # 売買日（コストを払った日）は同じ集合
    days_f = {t for t in range(600) if abs(fwd["net_bp"][t] - fwd["gross_bp"][t]) > 0}
    days_r = {t for t in range(600) if abs(rev["net_bp"][t] - rev["gross_bp"][t]) > 0}
    # ⚠ 最初の建て（片方だけ払う）と末尾の強制清算（Long の側だけ払う）を除けば、売買日は同じ集合
    assert days_f - {first, 599} == days_r - {first, 599}


def test_reverse_identity_gross_and_cost():
    """⚠ **逆の粗利 ＝ B&H 粗利 − 元の粗利**（最初の合図以降）、逆のコスト − 元のコスト ∈ {−5, 0, ＋5}。

    最初の建て（2.5）と強制清算（2.5）が同じ側に付けば 5、別の側なら 0（reverse-trading.md §1-3）。
    """
    rng = np.random.default_rng(13)
    b = rng.uniform(0, 100, 500)
    y = rng.normal(0.0002, 0.01, 500)
    fwd, rev = _complement_pair(b, y, 55.0)
    first = next(t for t in range(500) if fwd["pos"][t] == 1 or rev["pos"][t] == 1)
    bh_gross = float((y[first:] * 1e4).sum())
    assert float(rev["gross_bp"][first:].sum()) == pytest.approx(bh_gross - float(fwd["gross_bp"][first:].sum()))
    assert rev["cost_bp_total"] - fwd["cost_bp_total"] in (-5.0, 0.0, 5.0)


def test_reverse_with_two_outputs_settles_into_the_complement():
    """2 出力の契約（`exit_pct` あり）で **同じ日に入口% と出口% が両方 θ を超える**系列でも、遅れて補集合に落ち着く。"""
    rng = np.random.default_rng(17)
    b = rng.uniform(0, 100, 400)
    e = np.where(rng.uniform(0, 1, 400) < 0.3, 90.0, rng.uniform(0, 100, 400))   # 出口% が独立に立つ
    y = rng.normal(0, 0.01, 400)
    fwd, rev = _complement_pair(b, y, 55.0, exit_pct=e)
    both = fwd["pos"] + rev["pos"]
    # ⚠ 2 つが同時に Long になる日があってもよいが、末尾では補集合に落ち着いている
    assert both[-50:].max() <= 1 and (both[-50:] == 1).mean() > 0.5
