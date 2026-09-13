"""⚠ **上限の見積もりそのものを検査する**（[rules.md 14-2](../../../docs/specs/experiments/feature-discovery/rules.md)）。

⚠ **ここが静かに間違うと「回すか打ち切るか」の判断がそのまま嘘になる。**
特に ⚠ **区間の割り方（(lo, hi] か [lo, hi) か）**と ⚠ **fold の切り方**は、
間違えても数字がそれらしく出るので、恒等式と既存実装との一致で縛る。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail.validation import checks
from cli import ceiling


def _panel(n_up1=100, n_down=60, n_up2=220, up=20.0, down=-60.0):
    """上げ → 下げ → 回復 の 1 エピソードを持つ B&H 日次系列。

    ⚠ **既定は回復し切る長さにする**（下げ 3,600bp を上げ 20bp/日 で埋めるには 180 日要る）。
    """
    ts = pd.date_range("2020-01-01", periods=n_up1 + n_down + n_up2, freq="D", tz="UTC")
    step = np.r_[np.full(n_up1, up), np.full(n_down, down), np.full(n_up2, up)]
    return pd.Series(step, index=ts)


# --- 1. 既存実装との一致 ------------------------------------------------

def test_episode_spans_agrees_with_the_checks_implementation():
    """⚠ **添字で返す版と日付で返す版が同じ区間を指すこと。** 片方だけ直すと診断と上限がずれる。"""
    bh = _panel()
    spans = ceiling.episode_spans(bh, min_drop_bp=1000.0)
    zeros = [pd.Series(np.zeros(len(bh)), index=bh.index)]
    doc = checks._episodes([bh], zeros, zeros, min_drop_bp=1000.0)
    assert len(spans) == doc["回数"] == 1
    lo, hi, rec = spans[0]
    e = doc["episodes"][0]
    assert str(bh.index[lo].date()) == e["山"]
    assert str(bh.index[hi].date()) == e["谷"]
    assert str(bh.index[rec].date()) == e["回復"]
    assert hi - lo == e["日数"]


def test_episode_spans_leaves_the_recovery_open_when_it_never_returns():
    """⚠ **回復しなかったエピソードは `rec=None`**（谷 → 回復の区間が無い）。"""
    bh = _panel(n_up2=5)                      # 戻り切らない
    (lo, hi, rec), = ceiling.episode_spans(bh, min_drop_bp=1000.0)
    assert rec is None and hi > lo


# --- 2. 区間の割り方 ----------------------------------------------------

def test_windows_partition_the_days_exactly():
    """⚠ **区間は (lo, hi] と (hi, rec]。** ⚠ **重なっても余ってもいけない。**"""
    bh = _panel()
    spans = ceiling.episode_spans(bh, min_drop_bp=1000.0)
    w = ceiling.windows(len(bh), spans)
    assert len(w) == len(bh)
    counts = {k: int((w == k).sum()) for k in (ceiling.PEAK_TROUGH, ceiling.TROUGH_RECOVER,
                                               ceiling.OTHER)}
    assert sum(counts.values()) == len(bh)
    (lo, hi, rec), = spans
    assert counts[ceiling.PEAK_TROUGH] == hi - lo
    assert counts[ceiling.TROUGH_RECOVER] == rec - hi


def test_peak_to_trough_window_sums_to_the_decline():
    """⚠ **区間を 1 日ずらすと下げ幅が変わる。** 山 → 谷の合計が累積の差と一致すること。"""
    bh = _panel()
    spans = ceiling.episode_spans(bh, min_drop_bp=1000.0)
    w = ceiling.windows(len(bh), spans)
    (lo, hi, _), = spans
    eq = bh.cumsum().to_numpy()
    assert bh.to_numpy()[w == ceiling.PEAK_TROUGH].sum() == pytest.approx(eq[hi] - eq[lo])


# --- 3. 神託の定義 ------------------------------------------------------

def test_perfect_entry_earns_exactly_zero_over_the_recovery():
    """⚠ **B&H は常に買い持ちなので、完全に持てば上乗せはちょうど 0。**

    ⚠ **ここが 0 にならない実装は「持っている日にも上乗せが出る」ことになり、物差しが壊れている。**
    """
    bh = _panel()
    spans = ceiling.episode_spans(bh, min_drop_bp=1000.0)
    w = ceiling.windows(len(bh), spans)
    rng = np.random.default_rng(0)
    edge = rng.normal(0.0, 10.0, len(bh))
    series, _ = ceiling.oracles(edge, bh.to_numpy(), w, len(spans))
    assert series["(ii) 入口だけ完全"][w == ceiling.TROUGH_RECOVER] == pytest.approx(0.0)
    # ⚠ **それ以外の区間は現行規則のまま**（入口を直しても平常時の空振りは消えない）
    assert series["(ii) 入口だけ完全"][w == ceiling.OTHER] == pytest.approx(edge[w == ceiling.OTHER])


def test_perfect_both_equals_the_whole_decline_minus_cost():
    """⚠ **両方完全の上限は「山 → 谷を丸ごと避けた額」— 現行規則の成績に依らない。**"""
    bh = _panel()
    spans = ceiling.episode_spans(bh, min_drop_bp=1000.0)
    w = ceiling.windows(len(bh), spans)
    y = bh.to_numpy()
    for scale in (0.0, 1.0, -3.0):                       # 現行規則を取り替えても変わらない
        _, totals = ceiling.oracles(np.full(len(bh), scale), y, w, len(spans))
        want = -y[w == ceiling.PEAK_TROUGH].sum() - len(spans) * 2 * ceiling.COST_ONEWAY_BP
        assert totals["(iii) 両方完全"] == pytest.approx(want)


def test_fixing_both_beats_the_sum_of_fixing_each_by_the_false_alarms():
    """⚠ **恒等式: (iii)−(i)−(ii) の取り戻しは「それ以外の区間の取り損ね」にちょうど等しい。**

    ⚠ **片方だけ直しても平常時の空振りは消えない**ことの数式での裏づけ。コストには依らない。
    """
    bh = _panel()
    spans = ceiling.episode_spans(bh, min_drop_bp=1000.0)
    w = ceiling.windows(len(bh), spans)
    rng = np.random.default_rng(1)
    edge = rng.normal(0.0, 10.0, len(bh))
    _, totals = ceiling.oracles(edge, bh.to_numpy(), w, len(spans))
    actual = totals["現行規則"]
    gains = {k: totals[k] - actual for k in totals if k != "現行規則"}
    other = float(edge[w == ceiling.OTHER].sum())
    assert (gains["(iii) 両方完全"] - gains["(i) 出口だけ完全"]
            - gains["(ii) 入口だけ完全"]) == pytest.approx(-other)


# --- 4. 検出限界 --------------------------------------------------------

def test_detection_limit_scales_with_the_spread():
    """⚠ **σ は方策ごとに違う。** 借りずに毎回その系列から出していることを、倍率で縛る。"""
    rng = np.random.default_rng(2)
    d = rng.normal(0.0, 10.0, 1000)
    folds = [200, 200, 200, 200, 200]
    a = ceiling.detection_limit(d, folds)
    b = ceiling.detection_limit(d * 3.0, folds)
    assert b["限界_符号_bp/fold"] == pytest.approx(a["限界_符号_bp/fold"] * 3.0)
    # ⚠ **符号 k/k の壁は t≥2 の壁より高い**（5 fold・確率 0.8 のとき）
    assert a["限界_符号_bp/fold"] > a["限界_t2_bp/fold"] > 0.0


def test_needed_share_flags_the_impossible_case():
    """⚠ **神託の 100% を超えないと壁に届かないなら、その型は回す前に落としてよい。**"""
    # 神託でも壁に届かない: 現行 −100・神託 +100（取り戻し 200）・壁 100/fold × 5 = 500
    assert ceiling.needed_share(100.0, -100.0, 100.0, 5) == pytest.approx(3.0)
    # 神託の半分で届く
    assert ceiling.needed_share(900.0, -100.0, 100.0, 5) == pytest.approx(0.6)
    # 取り戻しが無い型は無限大（＝ 不可能）
    assert ceiling.needed_share(-200.0, -100.0, 100.0, 5) == float("inf")


# --- 5. 銘柄別の神託 ----------------------------------------------------

def _two_symbol_panel():
    """2 銘柄 × 上げ → 下げ → 回復。⚠ **乖離の列は「持つ / 休む」をそのまま決める。**"""
    ts = pd.date_range("2020-01-01", periods=380, freq="D", tz="UTC")
    step = np.r_[np.full(100, 20.0), np.full(60, -60.0), np.full(220, 20.0)] / 1e4
    rows = []
    for sym, shift in (("AAA", 0.0), ("BBB", 0.0)):
        rows.append(pd.DataFrame({"symbol": sym, "ts": ts, "y": step + shift,
                                  "own_trend200_dist": np.r_[np.full(120, 1.0),
                                                             np.full(80, -1.0),
                                                             np.full(180, 1.0)]}))
    return pd.concat(rows, ignore_index=True), ts


def test_portfolio_bp_charges_one_way_cost_on_entry_and_forced_close():
    """⚠ **建てた日と手仕舞った日に片道 2.5bp**（13-4 規約 3）＋ ⚠ **fold の尻は強制清算**（規約 4）。"""
    y = np.full((4, 1), 100.0)
    avail = np.ones((4, 1), dtype=bool)
    got = ceiling._portfolio_bp(np.ones((4, 1)), y, avail, [4], 2.5)
    assert got[0] == pytest.approx(100.0 - 2.5)          # 初日に建てる
    assert got[1] == pytest.approx(100.0)
    assert got[3] == pytest.approx(100.0 - 2.5)          # 尻で強制清算
    assert ceiling._portfolio_bp(np.zeros((4, 1)), y, avail, [4], 2.5) == pytest.approx(0.0)


def test_portfolio_bp_weights_only_the_symbols_that_exist_that_day():
    """⚠ **その日に存在する銘柄の等加重**（13-7）。⚠ **未上場を 0 として混ぜない。**"""
    y = np.array([[100.0, np.nan], [100.0, 300.0]])
    avail = np.array([[True, False], [True, True]])
    got = ceiling._portfolio_bp(np.ones((2, 2)), np.nan_to_num(y), avail, [2], 0.0)
    assert got[0] == pytest.approx(100.0)                # 1 銘柄しか居ない日は割る数も 1
    assert got[1] == pytest.approx(200.0)


def test_per_symbol_rule_is_rebuilt_from_the_distance_column():
    """⚠ **古典フィルタは「乖離が正なら持つ」だけ**（`scale.classic_filter`）。組み直しがそれと合うこと。"""
    panel, ts = _two_symbol_panel()
    out, n_ep = ceiling.per_symbol_policies(panel, ts, [len(ts)], "own_trend200_dist",
                                            min_drop_bp=1000.0, cost_oneway_bp=0.0)
    y = ceiling.wide_returns(panel, ts).to_numpy()
    pos = (ceiling._wide(panel, ts, "own_trend200_dist").to_numpy() > 0).astype(float)
    want = (pos * y).mean(axis=1) - y.mean(axis=1)
    assert out["現行規則（表から組み直し）"] == pytest.approx(want)
    assert n_ep == 2                                     # 2 銘柄 × 1 エピソード


def test_per_symbol_perfect_both_dominates_each_single_side_fix():
    """⚠ **両方完全は片側だけの完全より必ず良い。** 逆転したら建玉の組み方が壊れている。"""
    panel, ts = _two_symbol_panel()
    out, _ = ceiling.per_symbol_policies(panel, ts, [len(ts)], "own_trend200_dist",
                                         min_drop_bp=1000.0)
    tot = {k: float(v.sum()) for k, v in out.items()}
    assert tot["(iii) 両方完全"] > tot["(i) 出口だけ完全"] > tot["現行規則（表から組み直し）"]
    assert tot["(iii) 両方完全"] > tot["(ii) 入口だけ完全"] > tot["現行規則（表から組み直し）"]


def test_wide_returns_aligns_a_tz_aware_table_to_a_naive_index():
    """⚠ **表は UTC 付き・`daily.csv` は素。** 揃えないと全部 NaN になり「エピソード 0 回」になる。"""
    panel, ts = _two_symbol_panel()
    naive = ts.tz_localize(None)
    wide = ceiling.wide_returns(panel, naive)
    assert wide.notna().to_numpy().all()


# --- 6. fold の切り方 ---------------------------------------------------

def test_fold_days_come_from_result_csv_not_from_date_gaps(tmp_path):
    """⚠ **検証 fold は時間順に連続していて日付が空かない。**

    ⚠ **空きで切ると先頭が短くなり、fold の符号の数が変わる**（2026-09-12 に踏んだ）。
    """
    rows = [{"手法": "x", "fold": f, "閾値": th, "検証日数": d}
            for f, d in enumerate([1267, 1267, 1267, 1267, 1268], 1)
            for th in (50.0, 55.0)]
    pd.DataFrame(rows).to_csv(tmp_path / "result.csv", index=False)
    assert ceiling._fold_days(str(tmp_path)) == [1267, 1267, 1267, 1267, 1268]
