"""登録簿 `20261025-xs-excess-label`: 訓練のラベルを断面の超過リターン（y − 同じ日の全銘柄の平均）にする口。

⚠ `label` の無い config は 1 ビットも変わらない（`prep.train_label` は `tr["y"].values` そのもの）／ 超過ラベルは日ごとの平均が 0 ／
識別項目は検証方式の変種 `閾値売買（断面超過ラベル）` ＝ 名前 `~xs-label` ／ 学習範囲 shared だけ。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import catalog, names, runs
from ail.validation import prep
from cli.run import evaluate_trading
from tests.test_trader_shape import _exp, _feats, _panel


def test_train_label_default_is_the_same_object_and_excess_has_zero_daily_mean():
    p = _panel()
    base = prep.train_label({}, p)
    assert base is p["y"].values or np.shares_memory(base, p["y"].values)
    xs = prep.train_label({"label": "xs_excess"}, p)
    assert len(xs) == len(p) and np.allclose(pd.Series(xs).groupby(p["ts"].to_numpy()).mean(), 0.0)
    assert np.allclose(xs, p["y"].to_numpy() - p.groupby("ts")["y"].transform("mean").to_numpy())
    with pytest.raises(SystemExit):
        prep.train_label({"label": "other"}, p)


def test_evaluate_trading_changes_buy_pct_only_with_the_label(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    run = runs.Run("test_xs", {}, seed=0)
    panel = _panel()
    a = evaluate_trading(panel, _feats(panel), _exp(), run)[0]
    b = evaluate_trading(panel, _feats(panel), {**_exp(), "label": "xs_excess"}, run)[0]
    assert set(a["手法"]) == set(b["手法"]) and len(a) == len(b)
    m = "全部使う（基準）"
    assert not np.allclose(a[a["手法"] == m]["純利bp"].to_numpy(), b[b["手法"] == m]["純利bp"].to_numpy())
    bh = "基準 常に上（ドリフト）"
    assert np.allclose(a[a["手法"] == bh]["純利bp"].to_numpy(), b[b["手法"] == bh]["純利bp"].to_numpy())   # 損益の器は同じ
    with pytest.raises(SystemExit):
        e = {**_exp(), "label": "xs_excess"}
        e["trading"] = {**e["trading"], "form": "per_symbol"}
        evaluate_trading(panel, _feats(panel), e, run)


def test_identification_and_spelling():
    cfg = {"trading": {"style": "threshold"}, "cost_bp": 5.0, "label": "xs_excess"}
    assert catalog.threshold_style(cfg) == "閾値売買（断面超過ラベル）" and catalog.is_threshold(catalog.threshold_style(cfg))
    assert catalog.threshold_style({"trading": {"style": "threshold"}, "cost_bp": 5.0}) == "閾値売買"
    row = {"鍵": "全部使う（基準）", "モデル": "Ridge", "粒度": "日足", "地平": "1 本（1 日）", "特徴量の層": "own", "層": "adjusted",
           "期間": "2018-01-31", "銘柄": "63", "集合": "daily", "検証方式": "閾値売買（断面超過ラベル）", "形式": "共通", "較正": "std", "閾値": "50"}
    assert names.trial_name(row) == "own.all.ridge.shared~xs-label@50"
