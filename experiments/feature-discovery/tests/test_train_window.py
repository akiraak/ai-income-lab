"""訓練の窓 `[validation] max_train_days`（rules.md 21 章。2026-10-08）。

⚠ **無い config は 1 ビットも変わらない**（fold の切れ目・検証の行・訓練の行）。⚠ **窓の外の行は訓練に入らない。**
⚠ 検証結果一覧では検証方式の変種 `閾値売買（訓練直近N日）`（数える・判定は閾値売買のまま）・予測モデル名は `~trainN`。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import catalog, names, runs
from ail.validation import splits
from cli.run import evaluate_trading
from tests import _fingerprint as fp
import ail.bootstrap  # noqa: F401


def _folds(panel, n=None):
    edges = splits.date_edges(panel["ts"], 5)
    return list(splits.folds_by_dates(panel, edges, 1440.0, max_train_days=n)), edges


def test_none_changes_nothing():
    p = fp.panel()
    a, _ = _folds(p)
    edges = splits.date_edges(p["ts"], 5)
    b = list(splits.folds_by_dates(p, edges, 1440.0))
    assert len(a) == len(b)
    for (fa, tra, tea), (fb, trb, teb) in zip(a, b):
        assert fa == fb and tra.equals(trb) and tea.equals(teb)


@pytest.mark.parametrize("n", [100, 250])
def test_window_keeps_only_the_last_n_dates(n):
    p = fp.panel()
    full, edges = _folds(p)
    win, _ = _folds(p, n)
    assert [f for f, _, _ in full] == [f for f, _, _ in win]
    days = pd.Series(pd.to_datetime(p["ts"]).unique()).sort_values().reset_index(drop=True)
    for (f, trw, tew), (_, trf, tef) in zip(win, full):
        assert tew.equals(tef)                              # ⚠ 検証の行は窓に依らない
        before = days[days < edges[f]]
        start = before.iloc[-n] if len(before) > n else before.iloc[0]
        assert pd.to_datetime(trw["ts"]).min() >= start      # ⚠ 窓の外の行は入らない
        assert pd.to_datetime(trw["ts"]).nunique() <= n
        # 窓の中の行は「全部」の訓練と同じ（パージも同じ）
        assert trw.equals(trf[pd.to_datetime(trf["ts"]) >= start])
        if len(before) > n:
            assert len(trw) < len(trf)


@pytest.mark.parametrize("bad", [0, -5, 2.5, True, "2520"])
def test_bad_value_stops(bad):
    with pytest.raises(SystemExit):
        splits.max_train_days({"max_train_days": bad})


def test_style_and_name_and_counting():
    assert catalog.threshold_style({"trading": {"style": "threshold"}, "validation": {}}) == "閾値売買"
    cfg = {"trading": {"style": "threshold"}, "validation": {"max_train_days": 2520}}
    style = catalog.threshold_style(cfg)
    assert style == "閾値売買（訓練直近2520日）"
    assert catalog.is_threshold(style)
    row = {"手法名": "全部使う（基準）", "検証方式": style}
    assert catalog.is_trial(row)                              # ⚠ 数える（全部使うも検証方式が処置）
    assert not catalog.is_trial({"手法名": "基準 常に上（ドリフト）", "検証方式": style})
    key = dict(鍵="F2-2", モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own", 層="adjusted",
               期間="1995-02-10", 銘柄="63", 検証方式=style, 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key) == "own.f2-2.ridge.shared~p1995-02-10~train2520@50"
    assert names.trial_name({**key, "検証方式": "閾値売買"}) == "own.f2-2.ridge.shared~p1995-02-10@50"


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_window", {}, seed=0)


def _exp(n=None):
    v = {"split": "walk_forward_dates", "folds": 5, "seed": 0}
    if n:
        v["max_train_days"] = n
    return {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"},
            "validation": v, "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "selectors": ["全部使う（基準）"], "model": "Ridge",
            "baselines": ["常に上（ドリフト）"]}


def test_window_changes_the_model_but_not_buy_and_hold(run):
    p = fp.panel()
    feats = [c for c in p.columns if c not in ("symbol", "ts", "y")]
    full, *_ = evaluate_trading(p, feats, _exp(), run)
    win, *_ = evaluate_trading(p, feats, _exp(150), run)
    bh = lambda r: r[r["手法"] == "基準 常に上（ドリフト）"].reset_index(drop=True)
    pd.testing.assert_frame_equal(bh(full), bh(win))        # B&H は訓練を使わない
    m = lambda r: r[r["手法"] == "全部使う（基準）"]["純利bp"].to_numpy()
    assert not np.allclose(m(full), m(win))                  # 窓が効いている
