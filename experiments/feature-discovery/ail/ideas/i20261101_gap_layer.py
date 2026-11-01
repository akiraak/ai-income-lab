"""登録簿 `20261101-gap-intraday-layer`（参考なし・データ。rules.md 31 章・記録 dev-loop.md）:
1 日のリターンを **夜間（前日終値 → 始値）** と **日中（始値 → 終値）** に分けた層 `gap_`。

列（8 本・登録簿の `structure` の写し）:
  夜間 ＝ `gap_on_1` ／ `gap_on_5` ／ `gap_on_20`（夜間の対数リターンの 1 ／ 5 ／ 20 日の和）
  日中 ＝ `gap_id_1` ／ `gap_id_5` ／ `gap_id_20`（日中の対数リターンの 1 ／ 5 ／ 20 日の和）
  `gap_opp_20`（夜間と日中の符号が逆だった日の割合・20 日）・`gap_ratio_20`（夜間の絶対値の和 ÷ 日中の絶対値の和・20 日）

⚠ 時点: 足 i の値は足 i の終値まで（own_ と同じ）。始値・終値は表を作る足（調整済み日足）のまま。
⚠ config の `feature_layers` に `gap` があるときだけ組む（`build.layer_order` が後ろに足す）＝ 無い config は 1 ビットも変わらない。
⚠ own の `own_gap_min`（足の間隔）とは別物（接頭辞が違う）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.registry import register

PREFIX = "gap_"
COLUMNS = ("on_1", "on_5", "on_20", "id_1", "id_5", "id_20", "opp_20", "ratio_20")


def gap_one(df: pd.DataFrame) -> pd.DataFrame:
    """1 銘柄の足 → 8 列（df と同じ行数・同じ並び）。"""
    c = df["close"].astype(float).where(lambda x: x > 0)
    o = df["open"].astype(float).where(lambda x: x > 0)
    on = np.log(o / c.shift(1))                 # 夜間: 前の足の終値 → この足の始値
    iday = np.log(c / o)                        # 日中: この足の始値 → 終値
    x = pd.DataFrame(index=df.index)
    for w in (1, 5, 20):
        x[f"on_{w}"] = on.rolling(w).sum()
        x[f"id_{w}"] = iday.rolling(w).sum()
    opp = (np.sign(on) * np.sign(iday) < 0).astype(float).where(on.notna() & iday.notna())
    x["opp_20"] = opp.rolling(20).mean()
    den = iday.abs().rolling(20).sum()
    x["ratio_20"] = on.abs().rolling(20).sum() / den.where(den > 0)
    return x[list(COLUMNS)].replace([np.inf, -np.inf], np.nan)


@register("feature", "gap")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    return {s: gap_one(df).add_prefix(PREFIX) for s, df in panel.items()}
