"""⚠ **デフレーテッド SR（E8 L3）／ FDR（L7）／ 実効標本数**。

⚠ **63 系列は独立ではない**（SPY は残り 48 社を丸ごと含む）。行数を標本数と読むと
⚠ **t 値を過大に読む。** ここで割引率を出す。

⚠ **良い数字が出たときにだけ意味がある道具である。** 全部が負けているうちは通す必要が無い
（rules.md 12 章）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def effective_breadth(returns: pd.DataFrame) -> dict:
    """相関行列の固有値から「実質いくつの独立な系列か」を出す。

    ⚠ **`(Σλ)² / Σλ²`（参加率の逆数）を使う。** 63 本の系列が全部同じ動きなら 1 に、
    完全に独立なら 63 になる。⚠ **t 値は `sqrt(実効本数 / 見かけの本数)` を掛けて割り引く。**
    """
    C = returns.corr().values
    lam = np.linalg.eigvalsh(np.nan_to_num(C, nan=0.0))
    lam = lam[lam > 0]
    n_eff = float(lam.sum() ** 2 / (lam ** 2).sum())
    n = returns.shape[1]
    return {"系列数": n, "実効系列数": n_eff, "t値の割引": float(np.sqrt(n_eff / n))}


def deflated_sharpe(sr: float, n_obs: int, n_trials: int,
                    skew: float = 0.0, kurt: float = 3.0) -> dict:
    """Bailey-López de Prado のデフレーテッド SR。

    ⚠ **`n_trials` は「試した手法の総数」**。11 手法 × 2 粒度 × 地平の数を全部数える。
    ⚠ **数え落とすと必ず甘くなる。**
    """
    if n_trials < 2 or n_obs < 3:
        return {"SR0": np.nan, "DSR": np.nan}
    e, g = 0.5772156649, stats.norm.ppf(1 - 1 / n_trials)
    g2 = stats.norm.ppf(1 - 1 / (n_trials * np.e))
    sr0 = ((1 - e) * g + e * g2) / np.sqrt(n_obs)      # 偶然だけで出る最大 SR の期待値
    denom = np.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
    z = (sr - sr0) * np.sqrt(n_obs - 1) / max(denom, 1e-12)
    return {"SR0": float(sr0), "DSR": float(stats.norm.cdf(z))}


def benjamini_hochberg(pvalues: np.ndarray, q: float = 0.10) -> np.ndarray:
    """⚠ **偽発見率を q に抑える。** 返り値は「棄却してよいか」の真偽値。"""
    p = np.asarray(pvalues, dtype=float)
    order = np.argsort(p)
    m = len(p)
    thr = (np.arange(1, m + 1) / m) * q
    passed = p[order] <= thr
    cut = np.max(np.where(passed)[0]) + 1 if passed.any() else 0
    out = np.zeros(m, dtype=bool)
    out[order[:cut]] = True
    return out
