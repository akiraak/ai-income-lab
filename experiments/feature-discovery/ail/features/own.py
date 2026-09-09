"""`own_` その銘柄自身の履歴だけから作る特徴量（35 本）。

⚠ **足 i の特徴量は足 i までしか見ない。** 窓はすべて i で閉じ、`shift(-k)` は 1 つも使わない。

⚠ **この 35 本は 2026-09-08 以前の `features.py` と 1 対 1 に対応する**（接頭辞 `own_` が付いただけ）。
⚠ **数式を変えてはいけない。** 配線を移し替えたことの確認は「旧 `evaluate.py` と同じ数字が出るか」で行うので、
⚠ **ここを同時に良くすると、数字が変わった原因が配線なのか式なのか分からなくなる。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.registry import register

PREFIX = "own_"


def build_one(df: pd.DataFrame) -> pd.DataFrame:
    """1 銘柄の足から `own_` 特徴量を作る。返り値は df と同じ行数・同じ並び。"""
    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
    r = np.log(c).diff()                       # ⚠ 足 i のリターン = c[i]/c[i-1]。未来ではない
    x = pd.DataFrame(index=df.index)

    # --- 1. リターンのラグ（直近の値動きそのもの） ---
    for k in (1, 2, 3, 5, 10, 20, 60):
        x[f"ret_{k}"] = np.log(c).diff(k)

    # --- 2. ボラティリティ ---
    for w in (5, 20, 60):
        x[f"vol_{w}"] = r.rolling(w).std()
    hl = np.log(h / l).replace([np.inf, -np.inf], np.nan)          # Parkinson（高値安値の幅）
    for w in (5, 20, 60):
        x[f"park_{w}"] = np.sqrt((hl ** 2).rolling(w).mean() / (4 * np.log(2)))

    # --- 3. 位置（移動平均からの乖離・レンジ内の位置） ---
    for w in (5, 20, 60):
        ma, sd = c.rolling(w).mean(), c.rolling(w).std()
        x[f"z_{w}"] = (c - ma) / sd
        hh, ll = h.rolling(w).max(), l.rolling(w).min()
        x[f"pos_{w}"] = (c - ll) / (hh - ll)

    # --- 4. 出来高 ---
    for w in (5, 20, 60):
        x[f"vratio_{w}"] = v / v.rolling(w).mean()
    x["dollar_20"] = (c * v).rolling(20).mean()

    # --- 5. 形（歪度・尖度・自己相関） ---
    for w in (20, 60):
        x[f"skew_{w}"] = r.rolling(w).skew()
        x[f"kurt_{w}"] = r.rolling(w).kurt()
    x["ac1_60"] = r.rolling(60).apply(lambda s: pd.Series(s).autocorr(1), raw=False)

    # --- 6. バーの形 ---
    rng = (h - l).replace(0, np.nan)
    x["body"] = (c - o) / rng
    x["upper"] = (h - np.maximum(c, o)) / rng
    x["lower"] = (np.minimum(c, o) - l) / rng

    # --- 7. 時刻（カレンダー。E8 の系統 I） ---
    mins = df["ts"].dt.hour * 60 + df["ts"].dt.minute
    x["tod_sin"] = np.sin(2 * np.pi * mins / 1440)
    x["tod_cos"] = np.cos(2 * np.pi * mins / 1440)
    x["dow"] = df["ts"].dt.dayofweek

    # --- 8. 足の間隔（⚠ 1 分刻みでない箇所がある。欠測の代理） ---
    x["gap_min"] = df["ts"].diff().dt.total_seconds() / 60

    return x.add_prefix(PREFIX)


@register("feature", "own")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    return {s: build_one(df) for s, df in panel.items()}
