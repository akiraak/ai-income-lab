"""event study の道具。⚠ **危ないのは 2 つ — 週末またぎの「翌日」と、曜日をそろえない対照。**"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cli import eventstudy


def trading_index(start="2020-01-06", periods=60):
    """月〜金だけの索引（UTC 正規化）。"""
    return pd.bdate_range(start, periods=periods, tz="UTC")


def test_event_days_skip_weekends():
    """⚠ **金曜の発表の「翌日」は月曜**（暦の +1 日ではなく取引日の +1）。"""
    idx = trading_index()
    friday = pd.DatetimeIndex([pd.Timestamp("2020-01-10", tz="UTC")])
    assert list(eventstudy.event_days(friday, idx, 0)) == [pd.Timestamp("2020-01-10", tz="UTC")]
    assert list(eventstudy.event_days(friday, idx, 1)) == [pd.Timestamp("2020-01-13", tz="UTC")]
    assert list(eventstudy.event_days(friday, idx, -1)) == [pd.Timestamp("2020-01-09", tz="UTC")]


def test_event_days_drop_dates_without_bars():
    """⚠ **足が無い日（休場・未来の予定）は落とす**（-1 のまま index に入れない）。"""
    idx = trading_index(periods=10)
    days = pd.DatetimeIndex([pd.Timestamp("2020-01-08", tz="UTC"),
                             pd.Timestamp("2020-01-11", tz="UTC"),    # 土曜
                             pd.Timestamp("2030-01-09", tz="UTC")])   # 未来
    assert list(eventstudy.event_days(days, idx, 0)) == [pd.Timestamp("2020-01-08", tz="UTC")]


def test_permutation_controls_are_weekday_matched():
    """⚠ **対照は同じ曜日だけから引く。** 水曜の発表に月曜を混ぜると曜日効果が紛れ込む。"""
    idx = trading_index(periods=100)
    # 水曜だけ |r| = 100、他は 0 → 曜日をそろえた対照なら ctrl_abs も 100 になる
    r = pd.Series(np.where(idx.dayofweek == 2, 100.0, 0.0), index=idx)
    events = idx[idx.dayofweek == 2][:5]
    pool = idx.difference(events)
    out = eventstudy.matched_permutation(r, events, pool, seed=0)
    assert out["abs_bp"] == 100.0 and out["ctrl_abs_bp"] == 100.0
    assert out["p_abs"] > 0.5                 # 対照と同じ分布なので有意にならない


def test_permutation_detects_a_real_difference():
    idx = trading_index(periods=200)
    rng = np.random.default_rng(1)
    r = pd.Series(rng.normal(0, 10, len(idx)), index=idx)
    events = idx[idx.dayofweek == 2][:8]
    r[events] = r[events] * 10                # 発表日だけボラを 10 倍にする
    pool = idx.difference(events)
    out = eventstudy.matched_permutation(r, events, pool, seed=0)
    assert out["p_abs"] < 0.01
