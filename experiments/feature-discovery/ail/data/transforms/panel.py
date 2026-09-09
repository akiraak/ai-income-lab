"""断面のためのピボット（時刻 × 銘柄の行列）。⚠ **推定しない**ので `data/derived/` に置いてよい。

⚠ **前方埋めをしない。** 休場の銘柄を埋めると、その足に「まだ起きていない値」が入る（rules.md 7 章）。
⚠ **全銘柄が揃う時刻でしか断面の意味は無い**ので、揃っている本数を一緒に返す。
"""

from __future__ import annotations

import pandas as pd


def pivot(panel: dict[str, pd.DataFrame], column: str) -> pd.DataFrame:
    """{銘柄: 足} を 時刻 × 銘柄 の行列にする。⚠ **時刻の完全一致だけで揃える。**"""
    return pd.DataFrame({s: pd.Series(df[column].values,
                                      index=pd.to_datetime(df["time_ms"], unit="ms", utc=True))
                         for s, df in panel.items()})


def common_window(wide: pd.DataFrame, min_symbols: int) -> pd.DataFrame:
    """⚠ **銘柄が `min_symbols` 本そろっている時刻だけ**に絞る。"""
    return wide[wide.notna().sum(axis=1) >= min_symbols]
