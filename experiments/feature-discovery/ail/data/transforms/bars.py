"""足の組み替え。⚠ **パラメータを推定しない変換**なので `data/derived/` に置いてよい（rules.md 3 章の A）。

⚠ **入力は `adjusted/`。** 目盛りの断層が入ったままドルバーを切ると、
⚠ **足の切れ目そのものがずれる**（分割前の期間だけ「1 本あたりの金額」が 10 倍になる）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def dollar_bars(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """売買代金が `threshold` たまるごとに 1 本にまとめる。

    ⚠ **時間で切るより標本が均される**（薄い時間帯の足が減る）。E8 の系統 A の前処理。
    """
    return _accumulate(df, (df["close"] * df["volume"]).values, threshold)


def volume_bars(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """出来高が `threshold` たまるごとに 1 本にまとめる。"""
    return _accumulate(df, df["volume"].values, threshold)


def _accumulate(df: pd.DataFrame, weight: np.ndarray, threshold: float) -> pd.DataFrame:
    if threshold <= 0:
        raise ValueError("threshold は正の数")
    cum = np.cumsum(np.nan_to_num(weight))
    group = np.floor(cum / threshold).astype(np.int64)
    g = df.groupby(group)
    out = pd.DataFrame({
        "time_ms": g["time_ms"].first(),     # ⚠ 足の開始時刻（rules.md 4 章）
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
        "volume": g["volume"].sum(),
    }).reset_index(drop=True)
    return out


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """時間で組み替える（`5min` `1h` `1D`）。⚠ **空の区間は落とす**（前方埋めをしない）。"""
    idx = pd.to_datetime(df["time_ms"], unit="ms", utc=True)
    g = df.set_index(idx).resample(rule)
    out = pd.DataFrame({
        "time_ms": g["time_ms"].first(), "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(), "volume": g["volume"].sum(),
    }).dropna(subset=["close"]).reset_index(drop=True)
    out["time_ms"] = out["time_ms"].astype("int64")
    return out
