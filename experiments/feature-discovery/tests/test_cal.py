"""`cal` 層（暦の列。[プラン](../../../docs/plans/archive/weekday-feature.md)）。

⚠ **落としたいのは 4 つ**: 行数と並びが入力と同じ ／ 先読みが無い（末尾を切っても前の値が変わらない）／
曜日の one-hot が日付どおり ／ config に書かない表には 1 列も入らない（`build.ORDER` の最後・接頭辞 `cal_`）。
"""

from __future__ import annotations

import pandas as pd

from ail import contracts, registry
from ail.features import cal
import ail.bootstrap  # noqa: F401


def _bars():
    # 2026-09-01（火）〜 09-11（金）。09-05・06 は土日、09-07（月）は Labor Day で足が無い
    days = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]
    ts = pd.to_datetime(days, utc=True)
    c = pd.Series(range(100, 100 + len(days)), dtype=float)
    return pd.DataFrame({"ts": ts, "open": c, "high": c, "low": c, "close": c, "volume": 1.0})


def test_same_rows_order_and_columns():
    b = _bars()
    x = cal.build_one(b)
    assert len(x) == len(b) and (x.index == b.index).all()
    assert tuple(x.columns) == cal.COLUMNS and all(c.startswith(cal.PREFIX) for c in x.columns)


def test_weekday_one_hot_and_gap():
    x = cal.build_one(_bars())
    onehot = x[[c for c in x.columns if "_dow_" in c]]
    assert (onehot.sum(axis=1) == 1).all()
    assert list(onehot.idxmax(axis=1).str.replace("cal_dow_", "")) == ["tue", "wed", "thu", "fri", "tue", "wed", "thu", "fri"]
    gap = x["cal_gap_prev"]
    assert pd.isna(gap.iloc[0]) and list(gap.iloc[1:]) == [1, 1, 1, 4, 1, 1, 1]      # 金 → 火（連休明け）は 4 日


def test_no_lookahead():
    """⚠ **末尾を切っても前の値が 1 つも変わらない**（rules.md 7 章）。"""
    b = _bars()
    pd.testing.assert_frame_equal(cal.build_one(b).iloc[:5], cal.build_one(b.iloc[:5]))


def test_registered_last_and_prefixed():
    from cli import build
    assert registry.resolve("feature", "cal") is cal.layer
    assert build.ORDER[-1] == "cal"                      # ⚠ 既存の層の並びを動かさない
    assert contracts.layer_of("cal_dow_mon") == "cal_"
    assert "cal_dow_mon" in contracts.feature_columns(pd.DataFrame(columns=["ts", "y", "cal_dow_mon"]))
