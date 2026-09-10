"""早期打ち切り用の検証を「訓練分割の内側の末尾」から切る（rules.md 3 章 B・9 章）。

⚠ **検証 fold には触れない。** 訓練行は時刻順のまま渡ってくる（`cli/run.py` は
`splits.walk_forward` が時刻順に並べた行を、順を変えずに標準化して渡す）。
末尾 10% を早期打ち切りの検証にし、⚠ **境目に隙間を空けてラベルの重なりを断つ**（パージ）。

⚠ **行にはもう時刻が無い**（特徴量だけ）ので、隙間は行数で取る。日足 63 銘柄なら
1 地平 ≒ 63 行（間引き後は約半分）。⚠ **控えめに多めに捨てる**（`max(64, n // 200)`）。
"""

from __future__ import annotations

# 早期打ち切りを諦める下限。⚠ **小さい標本で末尾を割ると訓練が痩せすぎる**
MIN_TRAIN = 500


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
