"""翌日始値で執行する `[trading] execution = "next_open"`（rules.md 22 章。2026-10-08）。

⚠ **無い config は 1 ビットも変わらない**（既定経路の指紋は `test_trading_run.py`）。⚠ 変わるのは損益の系列だけで、
合図・的中率・IC は同じ。⚠ 始値は (銘柄, 日) で継ぎ、足 t より後の値段しか使わない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import catalog, names, runs
from ail.contracts import is_meta
from ail.data import store
from cli.run import EXEC_COLUMN, evaluate_trading, execution_of, next_open_returns
from tests import _fingerprint as fp
import ail.bootstrap  # noqa: F401


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_next_open", {}, seed=0)


def _exp(execution=None):
    t = {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"}
    if execution:
        t["execution"] = execution
    return {"trading": t, "validation": {"split": "walk_forward_dates", "folds": 5, "seed": 0},
            "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "selectors": ["全部使う（基準）"], "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


def _feats(p):
    return [c for c in p.columns if not is_meta(c)]


def test_open_returns_are_joined_by_symbol_and_day(tmp_path):
    d = str(tmp_path)
    days = pd.date_range("2020-01-02", periods=8, freq="D", tz="UTC")
    opens = {"AAA": [10, 11, 12, 13, 12, 14, 15, 16], "BBB": [50, 49, 48, 50, 52, 51, 53, 54]}
    for s, o in opens.items():
        store.write_bars(d, s, pd.DataFrame({"time_ms": days.view("int64") // 10**6, "open": o,
                                             "high": o, "low": o, "close": o, "volume": 1.0}))
    # 表は銘柄の順も日の並びも足とは違う（継ぐのは (銘柄, 日)）・BBB は 3 日目から
    panel = pd.DataFrame({"symbol": ["BBB"] * 6 + ["AAA"] * 8,
                          "ts": list(days[2:]) + list(days)}).sample(frac=1.0, random_state=0)
    got, missing = next_open_returns(panel, d)
    for (s, t), v in zip(panel[["symbol", "ts"]].itertuples(index=False), got):
        i = list(days).index(t)
        o = opens[s]
        want = np.log(o[i + 2] / o[i + 1]) if i + 2 < len(o) else 0.0     # ⚠ 足 t より後の値段だけ
        assert v == pytest.approx(want)
    assert missing == 4                                                  # 2 銘柄 × 最後の 2 日


def test_exec_column_is_never_a_feature():
    assert is_meta(EXEC_COLUMN)


def test_default_and_explicit_close_are_identical(run):
    p = fp.panel()
    a, *_ = evaluate_trading(p, _feats(p), _exp(), run)
    b, *_ = evaluate_trading(p, _feats(p), _exp("close"), run)
    pd.testing.assert_frame_equal(a, b)


def test_same_series_gives_the_same_result(run):
    """y_exec ＝ y なら終値執行と 1 ビットも同じ ＝ 差し替えたのは損益の系列だけ。"""
    p = fp.panel()
    a, sa, *_ = evaluate_trading(p, _feats(p), _exp(), run)
    b, sb, *_ = evaluate_trading(p.assign(**{EXEC_COLUMN: p["y"]}), _feats(p), _exp("next_open"), run)
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(sa, sb)


def test_other_series_changes_pnl_but_not_the_signal(run):
    p = fp.panel()
    rng = np.random.default_rng(7)
    q = p.assign(**{EXEC_COLUMN: rng.normal(0, 0.01, len(p))})
    a, *_ = evaluate_trading(p, _feats(p), _exp(), run)
    b, *_ = evaluate_trading(q, _feats(p), _exp("next_open"), run)
    assert np.allclose(a["的中率"], b["的中率"]) and np.allclose(a["IC"], b["IC"])   # 合図と当たり方は同じ
    assert np.allclose(a["取引回数"], b["取引回数"])                                   # 状態機械も同じ
    assert not np.allclose(a["純利bp"], b["純利bp"])
    # B&H も同じ系列で回る（22-1 の 3）: 粗利 ＝ fold の y_exec の合計（bp・銘柄平均）
    bh = b[(b["手法"] == "基準 常に上（ドリフト）") & (b["閾値"] == 50.0)]
    assert not np.allclose(bh["粗利bp"], a[(a["手法"] == "基準 常に上（ドリフト）") & (a["閾値"] == 50.0)]["粗利bp"])


def test_bad_settings_stop(run):
    with pytest.raises(SystemExit):
        execution_of({"execution": "open"})
    p = fp.panel()
    with pytest.raises(SystemExit):
        evaluate_trading(p, _feats(p), _exp("next_open"), run)    # y_exec を継いでいない


def test_style_and_name():
    style = catalog.threshold_style({"trading": {"style": "threshold", "execution": "next_open"}})
    assert style == "閾値売買（翌日始値）" and catalog.is_threshold(style)
    assert catalog.threshold_style({"trading": {"style": "threshold", "execution": "close"}}) == "閾値売買"
    assert catalog.is_trial({"手法名": "T3 QUANT（60日窓）", "検証方式": style})
    key = dict(鍵="全部使う（基準）", モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own",
               層="adjusted", 期間="2018-01-31", 銘柄="63", 検証方式=style, 形式="共通", 較正="std", 閾値="55")
    assert names.trial_name(key) == "own.all.ridge.shared~exec-open@55"
