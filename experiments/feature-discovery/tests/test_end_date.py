"""表の期間の口 `end_date`（rules.md 27-3。2026-10-19）。⚠ **無い config は 1 行も変わらない・両端は含む。**"""

from __future__ import annotations

import pandas as pd

from cli import build


def _df():
    ts = pd.to_datetime(["2018-06-26", "2018-06-27", "2026-09-09", "2026-09-10"], utc=True)
    return pd.DataFrame({"ts": list(ts) * 2, "symbol": ["A"] * 4 + ["B"] * 4, "y": range(8)})


def test_no_dates_changes_nothing():
    df = _df()
    out = build.cut_period(df, None, None)
    assert out.equals(df)


def test_start_and_end_are_inclusive():
    df = _df()
    out = build.cut_period(df, "2018-06-27", "2026-09-09")
    assert sorted(out["ts"].dt.strftime("%Y-%m-%d").unique()) == ["2018-06-27", "2026-09-09"]
    assert len(out) == 4
    only_end = build.cut_period(df, None, "2026-09-09")
    assert only_end["ts"].max().strftime("%Y-%m-%d") == "2026-09-09" and len(only_end) == 6
