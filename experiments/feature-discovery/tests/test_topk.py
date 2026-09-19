"""上位 K（rules.md 17 章）のシミュレータ `simulate_topk`。⚠ **純粋関数なので合成データだけで検査する。**"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail.validation import simulate as sim


def _long(n_days=120, n_sym=6, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2020-01-01", periods=n_days, freq="D")
    rows = []
    for s in range(n_sym):
        rows.append(pd.DataFrame({"symbol": f"S{s}", "ts": ts,
                                  "buy": rng.uniform(20, 80, n_days),
                                  "y": rng.normal(0, 0.01, n_days),
                                  "close": 10.0 * (s + 1)}))
    return pd.concat(rows, ignore_index=True).sample(frac=1.0, random_state=1).reset_index(drop=True)


def _run(df, k, **kw):
    return sim.simulate_topk(df["buy"].values, df["y"].values, df["ts"].values, df["symbol"].values,
                             kw.pop("th", 50.0), k, 5.0, **kw)


def test_k_equal_to_symbols_matches_the_per_symbol_machine():
    """⚠ **退化の検算**（17-1 の 7）: K ＝ 銘柄数なら、銘柄ごとのポジションと日次純利が `simulate` と完全に一致する。"""
    df = _long()
    r = _run(df, 6)
    for s, g in df.groupby("symbol"):
        g = g.sort_values("ts")
        one = sim.simulate(g["buy"].values, g["y"].values, 50.0, 5.0)
        assert np.array_equal(r["pos"][g.index.values], one["pos"])
        assert np.array_equal(r["net_unit_bp"][g.index.values], one["net_bp"])
    nets = {s: pd.Series(r["net_unit_bp"][g.sort_values("ts").index.values], index=g.sort_values("ts")["ts"].values)
            for s, g in df.groupby("symbol")}
    assert np.allclose(r["port_net_bp"].values, sim.portfolio_daily(nets).values, atol=1e-9)
    assert r["skipped_full"] == 0


def test_never_holds_more_than_k_and_weights_are_one_over_k():
    df = _long()
    for k in (1, 3):
        r = _run(df, k)
        held = pd.Series(r["pos"], index=df["ts"].values).groupby(level=0).sum()
        assert held.max() <= k
        assert set(np.unique(r["weight"][r["pos"] == 1])) == {1.0 / k}
        assert r["skipped_full"] > 0                      # 6 銘柄で候補は毎日あるので、枠は必ず詰まる


def test_picks_the_highest_buy_pct():
    ts = pd.Timestamp("2020-01-01")
    df = pd.DataFrame({"symbol": ["A", "B", "C"], "ts": ts, "buy": [60.0, 90.0, 70.0], "y": 0.0})
    r = _run(df, 1)
    assert r["pos"].tolist() == [0, 1, 0]
    r = _run(df, 2)
    assert r["pos"].tolist() == [0, 1, 1]


def test_tie_breaks_by_symbol_name():
    ts = pd.Timestamp("2020-01-01")
    df = pd.DataFrame({"symbol": ["B", "A"], "ts": ts, "buy": [70.0, 70.0], "y": 0.0})
    assert _run(df, 1)["pos"].tolist() == [0, 1]


def test_a_slot_freed_today_is_usable_from_the_next_day():
    """⚠ 17-1 の 4: A を売った日に B は買えない（枠は翌営業日から）。"""
    d = pd.date_range("2020-01-01", periods=3, freq="D")
    df = pd.DataFrame({"symbol": ["A"] * 3 + ["B"] * 3, "ts": list(d) * 2,
                       "buy": [90, 10, 10, 10, 80, 80], "y": 0.01})
    r = _run(df, 1)
    a, b = r["pos"][:3].tolist(), r["pos"][3:].tolist()
    assert a == [1, 0, 0]                                  # 2 日目に売る（出口% ＝ 100 − 10 ＝ 90 > 50）
    assert b == [0, 0, 1]                                  # ⚠ 2 日目は候補だが枠が無い → 3 日目に買う
    assert r["skipped_full"] == 1


def test_costs_and_forced_close():
    d = pd.date_range("2020-01-01", periods=2, freq="D")
    df = pd.DataFrame({"symbol": "A", "ts": d, "buy": [90.0, 90.0], "y": [0.01, 0.02]})
    r = _run(df, 1)
    assert r["net_unit_bp"].tolist() == pytest.approx([100.0 - 2.5, 200.0 - 2.5])   # 建てた日と強制清算
    assert r["port_net_bp"].sum() == pytest.approx(295.0)


def test_integer_shares_skip_expensive_symbols_and_move_on():
    """⚠ 17-3 の 1: 1 株が枠を超える銘柄は見送り、枠を使わずに次の順位へ進む。"""
    ts = pd.Timestamp("2020-01-01")
    df = pd.DataFrame({"symbol": ["A", "B", "C"], "ts": ts, "buy": [90.0, 80.0, 70.0], "y": 0.01,
                       "close": [500.0, 40.0, 30.0]})
    r = _run(df, 2, price=df["close"].values, budget_usd=200.0)       # 枠 $100
    assert r["pos"].tolist() == [0, 1, 1]
    assert r["skipped_price"] == 1
    assert r["weight"].tolist() == pytest.approx([0.0, 2 * 40 / 200, 3 * 30 / 200])   # 2 株・3 株
    assert r["invested"].iloc[0] == pytest.approx(0.85)
    assert r["port_net_bp"].iloc[0] == pytest.approx((0.4 + 0.45) * (100.0 - 2.5 - 2.5))


def test_integer_weights_never_exceed_the_slot():
    df = _long()
    r = _run(df, 3, price=df["close"].values, budget_usd=300.0)
    assert r["weight"].max() <= 1.0 / 3 + 1e-12
    assert r["invested"].max() <= 1.0 + 1e-12


def test_random_order_is_reproducible_and_differs_from_ranked():
    df = _long()
    a = _run(df, 2, rng=np.random.default_rng(7))
    b = _run(df, 2, rng=np.random.default_rng(7))
    assert np.array_equal(a["pos"], b["pos"])
    assert not np.array_equal(a["pos"], _run(df, 2)["pos"])


def test_row_order_does_not_matter():
    df = _long()
    a = _run(df, 3)
    s = df.sort_values(["symbol", "ts"])
    b = _run(s.reset_index(drop=True), 3)
    back = pd.Series(b["pos"], index=s.index).sort_index().values
    assert np.array_equal(a["pos"], back)


def test_missing_days_keep_the_slot_occupied():
    d = pd.date_range("2020-01-01", periods=3, freq="D")
    df = pd.DataFrame({"symbol": ["A", "A", "B", "B", "B"], "ts": [d[0], d[2], d[0], d[1], d[2]],
                       "buy": [90, 90, 60, 80, 80], "y": 0.0})
    r = _run(df, 1)
    assert r["pos"].tolist() == [1, 1, 0, 0, 0]            # A が 2 日目に行を持たなくても枠は空かない


def test_rejects_low_threshold_and_bad_k():
    df = _long(5, 2)
    with pytest.raises(ValueError):
        _run(df, 1, th=45.0)
    with pytest.raises(ValueError):
        _run(df, 0)


# --- `cli.run` の経路（合成パネルで端から端まで）-------------------------------------------

from ail import runs                                       # noqa: E402
from ail.validation import checks                          # noqa: E402
from cli.run import evaluate_trading, topk_name, topk_random_name   # noqa: E402
import ail.bootstrap  # noqa: E402,F401


def _panel(n_days=700, n_sym=6, seed=0, leak=False):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        y = rng.normal(0.0002, 0.01, n_days)
        d = pd.DataFrame({"symbol": f"S{i}", "ts": ts, "y": y, "close": 20.0 * (i + 1),
                          "own_ret_1": np.r_[0.0, y[:-1]], "noise": rng.normal(0, 1, n_days)})
        if leak:
            d["LEAK_y"] = y
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def _exp(top_k=None, budgets=None):
    t = {"style": "threshold", "thresholds": [50], "form": "shared"}
    if top_k:
        t["top_k"] = top_k
    if budgets:
        t["top_k_budgets_usd"] = budgets
    return {"trading": t, "validation": {"split": "walk_forward_dates", "folds": 5, "seed": 0},
            "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "selectors": ["全部使う（基準）", "乱択（基準）"], "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_topk", {}, seed=0)


def _feats(panel):
    return [c for c in panel.columns if c not in ("symbol", "ts", "y", "close")]


def test_existing_rows_do_not_change_when_top_k_is_added(run):
    """⚠ `top_k` は行を足すだけ。素の行（既存の鍵にまとまる）は 1 ビットも変わらない。"""
    panel = _panel()
    a, *_ = evaluate_trading(panel, _feats(panel), _exp(), run)
    b, *_ = evaluate_trading(panel, _feats(panel), _exp([1, 3], {"A": 300, "B⚠": 3000}), run)
    plain = b[b["手法"].isin(set(a["手法"]))].reset_index(drop=True)
    # ⚠ 値は 1 ビットも変わらない。型だけ、乱択の平均（小数）が同じ列に入るので 取引回数 が float になる
    pd.testing.assert_frame_equal(a.reset_index(drop=True), plain, check_exact=True, check_dtype=False)


def test_top_k_rows_and_their_random_baselines(run):
    panel = _panel()
    res, _ps, summary, daily, extra = evaluate_trading(
        panel, _feats(panel), _exp([1, 3], {"A": 300, "B⚠": 3000}), run)
    kinds = ("端数", "整数株A", "整数株B⚠")
    want = {topk_name("全部使う（基準）", k, kind) for k in (1, 3) for kind in kinds}
    want |= {topk_random_name("全部使う（基準）", k, kind) for k in (1, 3) for kind in kinds}
    want |= {topk_vol_name("全部使う（基準）", k, kind) for k in (1, 3) for kind in kinds}   # 17-7（表に close がある）
    got = {m for m in res["手法"] if "〔" in m}
    assert got == want                                     # ⚠ 乱択（基準）と「基準 」の行は上位 K に通さない
    assert (res[res["手法"].isin(want)].groupby("手法").size() == 5).all()
    assert len(extra["topk"]) == len(want) * 5
    assert all((m, 50.0) in daily for m in want)
    # ⚠ 台帳の数え方: 基準線は数えず、上位 K の行だけが試行
    from ail import catalog
    counted = [m for m in want if catalog.is_trial({"検証方式": "閾値売買", "手法名": m})]
    assert len(counted) == 6


def test_integer_rows_invest_no_more_than_fractional(run):
    panel = _panel()
    res, *_ = evaluate_trading(panel, _feats(panel), _exp([3], {"A": 300}), run)
    frac = res[res["手法"] == topk_name("全部使う（基準）", 3, "端数")]["保有日率"].mean()
    integer = res[res["手法"] == topk_name("全部使う（基準）", 3, "整数株A")]["保有日率"].mean()
    assert integer <= frac + 1e-12


def test_checks_carry_the_top_k_table(run):
    panel = _panel()
    res, ps, summary, daily, extra = evaluate_trading(panel, _feats(panel), _exp([1, 3]), run)
    doc = checks.compute_trading(res, summary, ps, daily, _exp([1, 3]), n_trials=10, extra=extra)
    assert {(e["K"], e["kind"]) for e in doc["topk"]} == {(1, "端数"), (3, "端数")}
    for e in doc["topk"]:
        assert e["vs_bh"]["folds"] == 5 and e["vs_random"]["folds"] == 5
        assert e["vs_random"]["daily"]["n_days"] > 100


def test_leak_makes_top_k_jump(run):
    """⚠ 13-10 の配線検査を上位 K の行でも: 未来を知る列を混ぜると上乗せが跳ねる。"""
    clean, leak = _panel(), _panel(leak=True)
    net = {}
    for tag, p in (("clean", clean), ("leak", leak)):
        res, *_ = evaluate_trading(p, _feats(p), _exp([1]), run)
        net[tag] = res[res["手法"] == topk_name("全部使う（基準）", 1, "端数")]["純利bp"].mean()
    assert net["leak"] > net["clean"] + 5000


def test_per_symbol_form_is_refused(run):
    panel = _panel()
    exp = _exp([1])
    exp["trading"]["form"] = "per_symbol"
    with pytest.raises(SystemExit):
        evaluate_trading(panel, _feats(panel), exp, run)


# --- 基準線「ボラ上位 K」（rules.md 17-7）---------------------------------------------------

from cli.run import topk_vol, topk_vol_name, TOPK_VOL_COLUMN   # noqa: E402


def test_rank_argument_orders_the_buys_and_nan_goes_last():
    ts = pd.Timestamp("2020-01-01")
    df = pd.DataFrame({"symbol": ["A", "B", "C"], "ts": ts, "buy": [90.0, 80.0, 70.0], "y": 0.0})
    r = _run(df, 1, rank=np.array([1.0, 5.0, np.nan]))
    assert r["pos"].tolist() == [0, 1, 0]                  # 買い% ではなく rank の大きい順
    r = _run(df, 2, rank=np.array([np.nan, 5.0, 1.0]))
    assert r["pos"].tolist() == [0, 1, 1]                  # NaN は最下位
    df.loc[0, "buy"] = 40.0                                # ⚠ 候補の条件（買い% > θ）は変えない
    assert _run(df, 3, rank=np.array([9.0, 5.0, 1.0]))["pos"].tolist() == [0, 1, 1]


def test_rank_none_is_the_buy_pct_order():
    df = _long()
    assert np.array_equal(_run(df, 2)["pos"], _run(df, 2, rank=df["buy"].values)["pos"])


def test_vol_column_does_not_look_ahead():
    """⚠ 足 t より先の `close` を書き換えても、足 t までのボラは変わらない（17-7 の 2）。"""
    panel = _panel(n_days=200, n_sym=2)
    rng = np.random.default_rng(3)
    panel["close"] = 100.0 * np.exp(rng.normal(0, 0.02, len(panel)).cumsum())
    a = topk_vol(panel)
    cut = panel["ts"].sort_values().unique()[120]
    later = panel["ts"] > cut
    changed = panel.copy()
    changed.loc[later, "close"] *= 3.0
    b = topk_vol(changed)
    assert np.allclose(a[~later].values, b[~later].values, equal_nan=True)
    assert a[~later].notna().sum() > 0 and a.iloc[:10].isna().all()      # 窓が足りない日は NaN（最下位）


def test_vol_baseline_rows_appear_and_are_not_counted(run):
    from ail import catalog
    panel = _panel()
    rng = np.random.default_rng(5)
    panel["close"] = 50.0 * np.exp(rng.normal(0, 0.02, len(panel)).cumsum() * 0.1)
    base, *_ = evaluate_trading(panel.drop(columns=["close"]), _feats(panel), _exp([1, 3]), run)
    res, ps, summary, daily, extra = evaluate_trading(panel, _feats(panel), _exp([1, 3]), run)
    vol_rows = {topk_vol_name("全部使う（基準）", k, "端数") for k in (1, 3)}
    assert vol_rows <= set(res["手法"]) and not (vol_rows & set(base["手法"]))   # close が無ければ出さない
    assert not any(catalog.is_trial({"検証方式": "閾値売買", "手法名": m}) for m in vol_rows)
    same = res[res["手法"].isin(set(base["手法"]))].reset_index(drop=True)
    pd.testing.assert_frame_equal(base.reset_index(drop=True), same, check_exact=True)   # ⚠ 既存の行は動かない
    doc = checks.compute_trading(res, summary, ps, daily, _exp([1, 3]), n_trials=10, extra=extra)
    assert all("vs_vol" in e and "vol_vs_random" in e for e in doc["topk"])
    assert TOPK_VOL_COLUMN not in panel                    # 呼び出し側の表は汚さない
