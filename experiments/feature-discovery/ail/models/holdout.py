"""早期打ち切り用の検証を「訓練分割の内側の末尾」から切る（rules.md 3 章 B・9 章）。

⚠ **検証 fold には触れない。** 訓練行は時刻順のまま渡ってくる（`cli/run.py` は
`splits.walk_forward` が時刻順に並べた行を、順を変えずに標準化して渡す）。
末尾 10% を早期打ち切りの検証にし、⚠ **境目に隙間を空けてラベルの重なりを断つ**（パージ）。

⚠ **行にはもう時刻が無い**（特徴量だけ）ので、隙間は行数で取る。日足 63 銘柄なら
1 地平 ≒ 63 行（間引き後は約半分）。⚠ **控えめに多めに捨てる**（`max(64, n // 200)`）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# 早期打ち切りを諦める下限。⚠ **小さい標本で末尾を割ると訓練が痩せすぎる**
MIN_TRAIN = 500
# ⚠ **1 営業日は暦で 1.5 日として数える**（週末と休場を保守側に丸める。プラン §2-2 の 4）。
# ⚠ **営業日のままカレンダーに当てると、パージが 3 割足りなくなる** ＝ ラベルが重なったまま残る
CALENDAR_PER_BAR = 1.5


def tail_holdout(X, y, frac: float = 0.1, gap: int | None = None):
    """(訓練, 検証) を返す。検証が切れないときは `(X, y), None`。

    X は DataFrame でも ndarray でもよい。⚠ **並びは変えない**（時刻順が前提）。
    """
    n = len(X)
    n_val = max(1, int(n * frac))
    if gap is None:
        gap = max(64, n // 200)
    lo = n - n_val - gap
    if lo < MIN_TRAIN:
        return (X, y), None
    head = X.iloc[:lo] if hasattr(X, "iloc") else X[:lo]
    tail = X.iloc[n - n_val:] if hasattr(X, "iloc") else X[n - n_val:]
    return (head, y[:lo]), (tail, y[n - n_val:])


def purge_days(bars: float) -> float:
    """ラベルが跨ぐ営業日 → ⚠ **暦の日数**（保守側に丸める）。"""
    return float(bars) * CALENDAR_PER_BAR


def date_holdout(ts, label_bars: float, frac: float = 0.1,
                 min_train: int = MIN_TRAIN, min_val: int = 50):
    """⚠ **日付で切る holdout。** 訓練分割の尻 `frac` を較正・門に回し、⚠ **頭との間をパージする。**

    ⚠ **`tail_holdout` は行数で隙間を取る**（63 銘柄なら 28 日ぶん）ので、
    ⚠ **200 営業日のラベルでは頭と尻のラベルが重なる** — 較正も門の値も楽観側に外れる。
    長いラベルを使う手法はこちらを使う（[プラン §2-2](../../../../docs/plans/archive/downtrend-detection.md) の 5）。

    戻り値は (頭のマスク, 尻のマスク) の 2 本。⚠ **切れないときは None**（素通しして呼び元が決める）。
    """
    t = pd.to_datetime(pd.Series(np.asarray(ts)).reset_index(drop=True))
    days = np.sort(t.unique())
    if len(days) < 10:
        return None
    cut = pd.Timestamp(days[int(len(days) * (1.0 - frac))])
    head_end = cut - pd.Timedelta(days=purge_days(label_bars))
    head = (t < head_end).to_numpy()
    hold = (t >= cut).to_numpy()
    if int(head.sum()) < min_train or int(hold.sum()) < min_val:
        return None
    return head, hold
