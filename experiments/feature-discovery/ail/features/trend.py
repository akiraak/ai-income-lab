"""`trend` 層 — ⚠ **スケール（窓）だけが違う同じ形の 6 列**（[プラン §2-1](../../../../docs/plans/archive/downtrend-detection.md)）。

⚠ **接頭辞は `own_`。** その銘柄自身の履歴しか見ないので層の意味は `own` と同じで、
⚠ **違うのは窓が長いことだけ**（20 / 60 / 200 営業日）。⚠ **既存の `own` 層（35 本）は 1 列も変えない**
（own.py の注意書き「数式を変えてはいけない」を守るため、長い窓は別のファイルに置く）。

⚠ **3 スケールに同じ 6 列を与える。** 列の顔ぶれが違うと、勝ち負けが「スケールの差」なのか
⚠ **「列の差」なのか分からなくなる**（下降トレンドの検知は、まさにスケールの差を見るタスクである）。

⚠ **足 i の特徴量は足 i までしか見ない。** 窓はすべて i で閉じ、`shift(-k)` は 1 つも使わない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.registry import register

PREFIX = "own_"
# ⚠ **窓は事前固定**（プラン §2-1）。⚠ **結果を見て動かさない**（rules.md 14-9: 手で選んだ数値は自由度）
WINDOWS = (20, 60, 200)


def _slope(log_close: pd.Series, w: int) -> pd.Series:
    """対数終値を窓 w で 1 次回帰したときの傾き（1 本あたり）。⚠ **窓は i で閉じる。**

    ⚠ **`rolling.apply` を使わない**（431,759 行 × 3 窓では終わらない）。窓内の位置 j ＝ 0..w−1 に
    対して Σ(j − j̄)(y − ȳ) / Σ(j − j̄)² を、⚠ **絶対位置 k の移動和だけで書き直す**:
    Σ_j j·y = Σ_k k·y_k − (i − w + 1)·Σ_k y_k。どちらも素の `rolling().sum()` で出る。
    """
    y = log_close
    k = np.arange(len(y), dtype=float)
    s = y.rolling(w).sum()                                   # Σ y
    u = pd.Series(k * y.to_numpy(), index=y.index).rolling(w).sum()   # Σ k·y
    start = pd.Series(k - (w - 1), index=y.index)            # 窓の先頭の絶対位置
    jbar = (w - 1) / 2.0
    num = u - start * s - jbar * s                           # Σ (j − j̄)·y
    den = w * (w * w - 1) / 12.0                             # Σ (j − j̄)²
    return num / den


def build_one(df: pd.DataFrame, windows=WINDOWS) -> pd.DataFrame:
    """1 銘柄の足から `trend` 層を作る。返り値は df と同じ行数・同じ並び。"""
    c = df["close"]
    lc = np.log(c)
    r = lc.diff()
    # ⚠ **最初の 1 行は「上げたか」が決まらない。** `(r > 0)` は NaN を False にしてしまうので、
    # ⚠ **NaN を NaN のまま残す**（残さないと助走の 1 行ぶんだけ割合が下振れする）
    up = (r > 0).astype(float).where(r.notna())
    x = pd.DataFrame(index=df.index)
    for w in windows:
        x[f"trend{w}_dist"] = lc - np.log(c.rolling(w).mean())   # 移動平均からの乖離（対数）
        x[f"trend{w}_slope"] = _slope(lc, w) * w                 # 窓ぜんたいで見た当てはめ幅
        x[f"trend{w}_dd"] = lc - np.log(c.rolling(w).max())      # ⚠ 窓内の最高値からの下落（≤ 0）
        x[f"trend{w}_up"] = up.rolling(w).mean()                 # 上げた日の割合
        x[f"trend{w}_vol"] = r.rolling(w).std()                  # ⚠ ボラティリティ・ゲートの通り道
        x[f"trend{w}_ret"] = lc.diff(w)                          # 窓の累積リターン
    return x.add_prefix(PREFIX)


def scale_columns(feats, w: int) -> list[str]:
    """スケール w の 6 列（⚠ **列の選び方は事前固定。学習で選ばせない**）。

    ⚠ **`LEAK_` の列は、そのスケールのものだけ混ぜる**（rules.md 13-10 の配線の検査）。
    ⚠ **混ぜ忘れると leak 対照が跳ねず、「配線が壊れている」と見分けがつかなくなる。**
    """
    head = f"{PREFIX}trend{w}_"
    cols = [c for c in feats if c.startswith(head)]
    cols += [c for c in feats if c.startswith("LEAK_") and c.endswith(f"_{w}")]
    return cols


@register("feature", "trend")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    ws = tuple(ctx.get("trend_windows", WINDOWS))
    return {s: build_one(df, ws) for s, df in panel.items()}
