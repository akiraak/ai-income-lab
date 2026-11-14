"""レジストリ `20261109-decide-every-5d`: 売買の判断を N 本に 1 回だけにするオプション `[trading] decide_every = N`。

⚠ 見るもの: 無い config（と N ＝ 1）は 1 ビットも変わらない ／ 判断日（系列の頭から N 本ごと）だけ買い% と出口% を読み、
間の足は前の判断を持ち越す ／ 持ち続けるだけの行（B&H）は間引いても同じ ／ 検証方式の変種 `閾値売買（5日ごと）` ＝ 名前 `~decide5`。
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import catalog, names, runs
from ail.contracts import is_meta
from ail.validation import simulate as sim
from cli.run import decide_every_of, evaluate_trading
from tests import _fingerprint as fp

BH = "基準 常に上（ドリフト）"
LAST = "基準 直前リターンの符号"
ALL = "全部使う（基準）"


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_decide_every", {}, seed=0)


def _exp(decide=None):
    t = {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"}
    if decide is not None:
        t["decide_every"] = decide
    return {"trading": t, "validation": {"split": "walk_forward_dates", "folds": 5, "seed": 0},
            "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "selectors": ["全部使う（基準）", "乱択（基準）"], "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


def _feats(p):
    return [c for c in p.columns if not is_meta(c)]


def _same(a: dict, b: dict) -> None:
    assert a.keys() == b.keys()
    for k in a:
        if isinstance(a[k], np.ndarray):
            assert np.array_equal(a[k], b[k]), k
        else:
            assert a[k] == b[k], k


# ---------- ① オプションの無い config（と N ＝ 1）は同じ値

def test_simulate_default_is_unchanged():
    assert inspect.signature(sim.simulate).parameters["decide_every"].default is None
    rng = np.random.default_rng(0)
    b = rng.uniform(30.0, 70.0, 200)
    e = rng.uniform(30.0, 70.0, 200)
    y = rng.normal(0.0, 0.01, 200)
    for th in (50.0, 55.0, 60.0):
        base = sim.simulate(b, y, th)
        _same(base, sim.simulate(b, y, th, decide_every=None))
        _same(base, sim.simulate(b, y, th, decide_every=1))          # 毎日判断 ＝ 省いたときと同じ
        ex = sim.simulate(b, y, th, exit_pct=e)
        _same(ex, sim.simulate(b, y, th, exit_pct=e, decide_every=1))


def test_config_without_the_option_is_unchanged(run):
    assert decide_every_of({}) is None and decide_every_of({"decide_every": 1}) is None
    assert decide_every_of({"decide_every": 5}) == 5
    plain = {"trading": {"style": "threshold"}, "cost_bp": 5.0}
    assert catalog.threshold_style(plain) == "閾値売買"
    assert catalog.threshold_style({"trading": {"style": "threshold", "decide_every": 1}}) == "閾値売買"
    p = fp.panel()
    a, sa, *_ = evaluate_trading(p, _feats(p), _exp(), run)
    b, sb, *_ = evaluate_trading(p, _feats(p), _exp(1), run)
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(sa, sb)


# ---------- ② 新しい処置の中身（小さな表で、どの足がどうなるか）

def test_only_decision_bars_are_read():
    y = np.full(12, 0.01)
    b = np.array([60.0, 40, 40, 40, 40, 40, 60, 60, 60, 60, 40, 40])
    r = sim.simulate(b, y, 50.0, cost_bp=5.0, decide_every=5)
    # 判断日は t ＝ 0 ／ 5 ／ 10。t0 で建てる → t1〜4 は出口% が線を超えていても持ち越す → t5 で降りる →
    # t6〜9 は買い% が線を超えていても建てない → t10 は買い% が線の下
    assert list(r["pos"]) == [1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
    assert r["trades"] == 1 and r["hold_days"] == [5] and r["entry_idx"] == [0] and r["forced_close"] is False
    want = np.array([97.5, 100, 100, 100, 100, -2.5, 0, 0, 0, 0, 0, 0])   # 費用は建てた足と降りた足だけ（片道 2.5bp）
    assert np.allclose(r["net_bp"], want) and r["cost_bp_total"] == pytest.approx(5.0)
    daily = sim.simulate(b, y, 50.0, cost_bp=5.0)
    assert list(daily["pos"]) == [1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 0, 0] and daily["trades"] == 2   # 毎日判断なら 2 回

    # 判断日でない足で線を超えても建てない → 次の判断日（t5）で建てる → t10 は持ち続ける → 末尾で強制清算
    b2 = np.array([40.0] * 5 + [70.0] * 7)
    r2 = sim.simulate(b2, y, 50.0, cost_bp=5.0, decide_every=5)
    assert list(r2["pos"]) == [0] * 5 + [1] * 7
    assert r2["trades"] == 1 and r2["hold_days"] == [7] and r2["entry_idx"] == [5] and r2["forced_close"] is True
    assert r2["net_bp"][5] == pytest.approx(97.5) and r2["net_bp"][-1] == pytest.approx(97.5)

    # 出口% を別に持つ手法も同じ: 判断日でない足の出口% は読まない
    b3 = np.array([60.0, 0, 0, 0, 0, 0])
    e3 = np.array([0.0, 90, 90, 90, 90, 0])
    r3 = sim.simulate(b3, y[:6], 50.0, exit_pct=e3, decide_every=5)
    assert list(r3["pos"]) == [1] * 6 and r3["trades"] == 1 and r3["forced_close"] is True
    assert list(sim.simulate(b3, y[:6], 50.0, exit_pct=e3)["pos"]) == [1, 0, 0, 0, 0, 0]


def test_buy_and_hold_is_the_same_and_bad_values_stop():
    rng = np.random.default_rng(1)
    y = rng.normal(0.0, 0.01, 53)
    full = np.full(53, 100.0)
    _same(sim.simulate(full, y, 50.0), sim.simulate(full, y, 50.0, decide_every=5))   # t ＝ 0 は必ず判断日 ＝ B&H は同じ
    for bad in (0, -1, 2.5, True):
        with pytest.raises(ValueError):
            sim.simulate(full, y, 50.0, decide_every=bad)
    for kw in ({"max_hold_days": 3}, {"min_hold_days": 3}, {"stop_loss_pct": 10.0},
               {"stop_exit_pct": np.zeros(53)}, {"hold_days": np.full(53, 5)}):
        with pytest.raises(ValueError):
            sim.simulate(full, y, 50.0, decide_every=5, **kw)
    for bad in (0, -5, "5", True, 2.5):
        with pytest.raises(SystemExit):
            decide_every_of({"decide_every": bad})
    for key, val in (("top_k", [3]), ("threshold_pairs", [[60, 50]]), ("graded", True), ("graded_rank", True),
                     ("mix", [{"name": "x"}]), ("trader", [{"tag": "x"}]), ("position_exits", {"methods": ["x"]})):
        with pytest.raises(SystemExit):
            decide_every_of({"decide_every": 5, key: val})
        assert decide_every_of({key: val}) is None               # オプションが無ければ何も言わない


def test_evaluate_trading_thins_the_decisions_only(run):
    p = fp.panel()
    a, _sa, _ga, _da, ea = evaluate_trading(p, _feats(p), _exp(), run)
    b, _sb, _gb, _db, eb = evaluate_trading(p, _feats(p), _exp(5), run)
    assert set(a["手法"]) == set(b["手法"]) and len(a) == len(b)
    a = a.sort_values(["手法", "閾値", "fold"]).reset_index(drop=True)
    b = b.sort_values(["手法", "閾値", "fold"]).reset_index(drop=True)
    assert np.allclose(a["的中率"], b["的中率"]) and np.allclose(a["IC"], b["IC"])        # 買い% は同じ（違うのは判断の間隔だけ）
    bh_a, bh_b = a[a["手法"] == BH], b[b["手法"] == BH]
    assert np.array_equal(bh_a["純利bp"].to_numpy(), bh_b["純利bp"].to_numpy())           # 対 B&H の相手は動かない
    assert np.array_equal(bh_a["取引回数"].to_numpy(), bh_b["取引回数"].to_numpy())
    # 直前リターンの符号はほぼ毎日ひっくり返る → 5 本に 1 回しか判断しなければ売買は必ず減る
    la = a[(a["手法"] == LAST) & (a["閾値"] == 50.0)]["取引回数"].to_numpy()
    lb = b[(b["手法"] == LAST) & (b["閾値"] == 50.0)]["取引回数"].to_numpy()
    assert len(la) == len(lb) and len(la) > 0 and (lb < la).all()
    # θ ＝ 50 では「持つ ＝ 買い% > 50」なので、判断日を間引いて売買が増えることはない
    ma = a[(a["手法"] == ALL) & (a["閾値"] == 50.0)]["取引回数"].to_numpy()
    mb = b[(b["手法"] == ALL) & (b["閾値"] == 50.0)]["取引回数"].to_numpy()
    assert (mb <= ma).all()
    # 建てるのも降りるのも判断日だけ ＝ 強制清算を除く保有日数は 5 の倍数
    h = eb["holds"]
    assert len(h) > 0 and (h[~h["強制清算"].astype(bool)]["保有日数"] % 5 == 0).all()
    ha = ea["holds"]
    assert (ha[~ha["強制清算"].astype(bool)]["保有日数"] % 5 != 0).any()                  # 毎日判断の側はそうならない


# ---------- ③ 検証方式の名前と予測モデル名の綴り

def test_identification_and_spelling():
    cfg = {"trading": {"style": "threshold", "decide_every": 5}, "cost_bp": 5.0}
    style = catalog.threshold_style(cfg)
    assert style == "閾値売買（5日ごと）" and catalog.is_threshold(style)
    both = catalog.threshold_style({"trading": {"style": "threshold", "execution": "next_open", "decide_every": 5}})
    assert both == "閾値売買（翌日始値・5日ごと）"                                        # ほかの変種とは `・` でつなぐ
    row = {"鍵": "全部使う（基準）", "モデル": "Ridge", "粒度": "日足", "地平": "1 本（1 日）", "特徴量の層": "own", "層": "adjusted",
           "期間": "2018-01-31", "銘柄": "63", "集合": "daily", "検証方式": style, "形式": "共通", "較正": "std", "閾値": "50"}
    assert names.trial_name(row) == "own.all.ridge.shared~decide5@50"
    assert names.model_name(row) == "own.all.ridge.shared~decide5"
    assert names.trial_name({**row, "検証方式": "閾値売買"}) == "own.all.ridge.shared@50"   # 素の行の名前は動かない
    h2 = {**row, "鍵": "H2 先5日ゲート（全列・学習）", "閾値": "55"}
    assert names.trial_name(h2) == "own.h2-gate5.ridge.shared~decide5@55"
    with pytest.raises(SystemExit):
        names.trial_name({**row, "検証方式": "閾値売買（3日ごと）"})                       # 水準は N ＝ 5 の 1 つだけ
