"""系列の変換。⚠ **推定の有無で扱いが変わる**（rules.md 3 章）。

  - 対数・差分・リターン化 → **推定しない**ので `data/derived/` に置いてよい
  - ⚠ **分数差分の次数 d の推定** → **標本から学ぶので訓練分割の内側でしか fit できない**
    （推定は `fitted.py` の担当。ここには「d を渡されたら差分を取る」までしか置かない）
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def log_return(close: pd.Series, k: int = 1) -> pd.Series:
    return np.log(close).diff(k)


def frac_diff_weights(d: float, size: int, tol: float = 1e-5) -> np.ndarray:
    """分数差分の重み。⚠ **窓は過去だけ**（`w[0]` が現在の足）。"""
    w = [1.0]
    for k in range(1, size):
        nxt = -w[-1] * (d - k + 1) / k
        if abs(nxt) < tol:
            break
        w.append(nxt)
    return np.array(w)


def frac_diff(x: pd.Series, d: float, size: int = 200) -> pd.Series:
    """分数差分。⚠ **d は引数で受け取る。ここで推定しない**（推定は訓練分割の内側）。"""
    w = frac_diff_weights(d, size)
    return x.rolling(len(w)).apply(lambda s: float(np.dot(w, s[::-1])), raw=True)
