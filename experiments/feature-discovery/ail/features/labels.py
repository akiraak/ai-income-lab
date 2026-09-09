"""ラベル。地平 k ／ 跨いだ実時間 `y_elapsed_min` ／ ⚠ **末尾 k 本は捨てる**（rules.md 8 章）。

    y            足 i の終値から k 本先の終値までの対数リターン（＝ 未来。これだけが未来を見てよい）
    y_sign       上がったか（1 / 0）
    y_elapsed_min ⚠ **ラベルが跨いだ実時間（分）。** 夜や週末を跨いだ行を後で落とすのに要る

⚠ **末尾 k 本を残すと NaN のラベルが混じる。** 落とすのは特徴量ではなくラベル側の都合なので、
⚠ **必ずここでまとめて落とす**（各層に散らすと、層ごとに行数がずれる）。

⚠ **`--leak` は配線の検査専用。** ラベルそのものを特徴量に混ぜた列を 1 本足す。
⚠ **これを入れて的中率が跳ね上がらなければ、検証の配線が壊れている**（rules.md 7 章）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LEAK_COLUMN = "LEAK_future_ret"


def build_one(df: pd.DataFrame, horizon: int, leak: bool = False) -> pd.DataFrame:
    c = df["close"]
    fwd = np.log(c).shift(-horizon) - np.log(c)
    y = pd.DataFrame(index=df.index)
    y["y"] = fwd
    y["y_sign"] = (fwd > 0).astype(float)
    y["y_elapsed_min"] = (df["ts"].shift(-horizon) - df["ts"]).dt.total_seconds() / 60
    if leak:
        y[LEAK_COLUMN] = fwd      # ⚠ わざとした先読み。配線の検査にだけ使う
    return y


def trim(frames: list[pd.DataFrame], horizon: int) -> list[pd.DataFrame]:
    """⚠ **末尾 `horizon` 本を全部の表から同じだけ落とす。**"""
    return [f.iloc[:-horizon] if horizon > 0 else f for f in frames]
