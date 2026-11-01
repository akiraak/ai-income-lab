"""1 日遅れの引けで執行する `[trading] execution = "next_close"`（rules.md 32 章。2026-11-01）。

⚠ **無い config は 1 ビットも変わらない**（既定経路の指紋は `test_trading_run.py`）。口は 22 章（翌日始値）と同じで、
継ぐ値段が調整済み日足の終値なだけ: y_exec[t] ＝ log(終値 t+2 ／ 終値 t+1)。
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
from tests.test_next_open import _exp, _feats
import ail.bootstrap  # noqa: F401


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_next_close", {}, seed=0)


def test_close_returns_are_one_bar_later_than_the_label(tmp_path):
    d = str(tmp_path)
    days = pd.date_range("2020-01-02", periods=8, freq="D", tz="UTC")
    closes = {"AAA": [10, 11, 12, 13, 12, 14, 15, 16], "BBB": [50, 49, 48, 50, 52, 51, 53, 54]}
    for s, c in closes.items():
        o = [x + 0.5 for x in c]                           # ⚠ 始値は別の値（終値を読んでいることを確かめる）
        store.write_bars(d, s, pd.DataFrame({"time_ms": days.view("int64") // 10**6, "open": o,
                                             "high": o, "low": c, "close": c, "volume": 1.0}))
    panel = pd.DataFrame({"symbol": ["BBB"] * 6 + ["AAA"] * 8,
                          "ts": list(days[2:]) + list(days)}).sample(frac=1.0, random_state=0)
    got, missing = next_open_returns(panel, d, column="close")
    for (s, t), v in zip(panel[["symbol", "ts"]].itertuples(index=False), got):
        i = list(days).index(t)
        c = closes[s]
        want = np.log(c[i + 2] / c[i + 1]) if i + 2 < len(c) else 0.0   # ⚠ ラベル y（t → t+1）の 1 本後ろ
        assert v == pytest.approx(want)
    assert missing == 4


def test_default_path_is_unchanged_and_lagged_series_moves_pnl_only(run):
    p = fp.panel()
    a, *_ = evaluate_trading(p, _feats(p), _exp(), run)
    # 銘柄ごとに y を 1 本先へずらした系列（32-1 の 1 の形）
    lag = p.groupby("symbol")["y"].shift(-1).fillna(0.0)
    b, *_ = evaluate_trading(p.assign(**{EXEC_COLUMN: lag}), _feats(p), _exp("next_close"), run)
    assert np.allclose(a["的中率"], b["的中率"]) and np.allclose(a["取引回数"], b["取引回数"])
    assert not np.allclose(a["純利bp"], b["純利bp"])
    c, *_ = evaluate_trading(p.assign(**{EXEC_COLUMN: p["y"]}), _feats(p), _exp("next_close"), run)
    pd.testing.assert_frame_equal(a, c)                    # y_exec ＝ y なら終値執行と同じ


def test_needs_the_series_and_style_name(run):
    assert execution_of({"execution": "next_close"}) == "next_close"
    p = fp.panel()
    with pytest.raises(SystemExit):
        evaluate_trading(p, _feats(p), _exp("next_close"), run)
    style = catalog.threshold_style({"trading": {"style": "threshold", "execution": "next_close"}})
    assert style == "閾値売買（翌日終値）" and catalog.is_threshold(style)
    key = dict(鍵="全部使う（基準）", モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own",
               層="adjusted", 期間="2018-01-31", 銘柄="63", 検証方式=style, 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key) == "own.all.ridge.shared~exec-close@50"
