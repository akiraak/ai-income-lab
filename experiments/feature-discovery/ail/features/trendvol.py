"""`trendvol` 層 — ⚠ **出来高の長い窓（20 / 60 / 200 営業日）**。`trend` 層と同じ 3 スケールに同じ 4 列
（[プラン §2-1](../../../../docs/plans/archive/volume-trend-input.md)・rules.md 15-2 規約 2-2）。

⚠ **`trend` 層（終値だけの 6 列）は 1 列も触らない**（14-4 規約 2・15-2 規約 2）。出来高は別のファイル・別の層に置き、
⚠ **config の `feature_layers` に書いたときだけ表に入る**（既定の表は 1 ビットも変わらない）。

⚠ **接頭辞は `own_`、列名は `own_trend{W}_v*`。** こう付けると `trend.scale_columns(feats, W)` が自動で拾うので、
検知器に差すときもコードを変えずに済む（プラン §2-5 の A）。

⚠ **足 i の列は足 i までしか見ない。** 窓はすべて i で閉じ、`shift(-k)` は 1 つも使わない。
⚠ **出来高 0 の日は log で −∞ になるので NaN にする**（`ail/data/check.py` の `zero_volume` は警告どまりで実データに残っている）。
⚠ **分割調整は済んだ表を読む**（`ail/data/adjust.py` が価格を割った日に出来高を掛けている）。⚠ **層の側で二重に直さない。**
⚠ **数値は 1 つも手で置かない**（窓は `trend.WINDOWS` を使い回す。rules.md 14-9）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.features import trend
from ail.registry import register

PREFIX = trend.PREFIX          # own_
WINDOWS = trend.WINDOWS        # ⚠ trend 層と同じ窓（15-2 規約 1: 全スケールに同じ列）
COLUMNS = ("vratio", "dollar", "updown", "vslope")


def _log_pos(s: pd.Series) -> pd.Series:
    """log(s)。⚠ **0 以下は NaN**（−∞ を表に入れない）。"""
    return np.log(s.where(s > 0))


def build_one(df: pd.DataFrame, windows=WINDOWS) -> pd.DataFrame:
    """1 銘柄の足から `trendvol` 層を作る。返り値は df と同じ行数・同じ並び。

    | 列 | 定義 | 由来 |
    | --- | --- | --- |
    | `vratio` | log(v ÷ v の W 日平均) | `own_vratio_*`（窓 5〜60）と同じ形を長い窓に |
    | `dollar` | log((c × v) の W 日平均) | `own_dollar_20` と同じ形 |
    | `updown` | log(上げ日の平均出来高 ÷ 下げ日の平均出来高)（窓 W） | 調査 §5 の「上げ日と下げ日の出来高比」 |
    | `vslope` | log v を窓 W で 1 次回帰した傾き × W | `trend._slope` をそのまま使う |
    """
    c, v = df["close"], df["volume"].astype(float)
    lv = _log_pos(v)
    r = np.log(c).diff()
    # ⚠ **最初の 1 行は「上げたか」が決まらない**（trend.py と同じ扱い。NaN のまま残す）
    up = (r > 0).astype(float).where(r.notna())
    dn = (r < 0).astype(float).where(r.notna())
    x = pd.DataFrame(index=df.index)
    for w in windows:
        x[f"trend{w}_vratio"] = lv - _log_pos(v.rolling(w).mean())
        x[f"trend{w}_dollar"] = _log_pos((c * v).rolling(w).mean())
        v_up = (v * up).rolling(w).sum() / up.rolling(w).sum()     # 上げ日の平均出来高（窓内）
        v_dn = (v * dn).rolling(w).sum() / dn.rolling(w).sum()     # 下げ日の平均出来高（窓内）
        x[f"trend{w}_updown"] = _log_pos(v_up) - _log_pos(v_dn)   # ⚠ どちらかが無い窓は NaN
        x[f"trend{w}_vslope"] = trend._slope(lv, w) * w
    return x.replace([np.inf, -np.inf], np.nan).add_prefix(PREFIX)


def columns(w: int) -> list[str]:
    """スケール w の 4 列の名前（テストと検算用）。"""
    return [f"{PREFIX}trend{w}_{c}" for c in COLUMNS]


@register("feature", "trendvol")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    ws = tuple(ctx.get("trend_windows", WINDOWS))     # ⚠ trend 層と同じ鍵で窓を受ける（揃える）
    return {s: build_one(df, ws) for s, df in panel.items()}
