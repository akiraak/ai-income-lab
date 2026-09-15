"""`seq` 層 — ⚠ **過去 60 営業日の対数リターンの窓そのもの**（[プラン](../../../../docs/plans/tsc-minirocket-hydra-quant.md) §0-1）。

⚠ **時系列分類器（`ail/detectors/tsc.py`）の入力を表の列として持つための層。**
検知器は訓練と検証しか受け取らず、⚠ **パージで削れた行が抜ける**ので、検証の頭の窓を検知器の中では組めない。
⚠ **表の段で作れば 1994 年からの足で全行が埋まる**（プラン §0-2 の 1）。

⚠ **接頭辞は `own_`。** その銘柄自身の履歴しか見ないので層の意味は `own` と同じ（`trend` 層と同じ扱い）。
⚠ **既存の `own` 層（35 本）は 1 列も変えない。** ⚠ **`own_seq60_r0` は `own_ret_1` と同じ値**だが、
own.py の式に触らないために別の層に置く。

    own_seq60_r{k} ＝ 足 i − k のリターン log(c[i−k] / c[i−k−1])   （k = 0 … 59）

⚠ **足 i の列は足 i までしか見ない。** `shift(k)`（k ≥ 0）だけを使い、`shift(-k)` は 1 つも使わない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.registry import register

PREFIX = "own_"
# ⚠ **窓の長さは事前固定**（プラン §0-1。own 層の最長窓と同じ 60）。⚠ **結果を見て動かさない**（rules.md 14-9）
WINDOW = 60


def column(k: int, window: int = WINDOW) -> str:
    return f"{PREFIX}seq{window}_r{k}"


def build_one(df: pd.DataFrame, window: int = WINDOW) -> pd.DataFrame:
    """1 銘柄の足から `seq` 層を作る。返り値は df と同じ行数・同じ並び。"""
    r = np.log(df["close"]).diff()                 # ⚠ 足 i のリターン = c[i]/c[i-1]。未来ではない
    x = pd.DataFrame({f"seq{window}_r{k}": r.shift(k) for k in range(window)}, index=df.index)
    return x.add_prefix(PREFIX)


def window_columns(feats, window: int = WINDOW) -> list[str]:
    """窓の列を **新しい順（r0 = 足 i）** で返す。⚠ **1 本でも欠けたら止める**（黙って短い窓にしない）。"""
    cols = [column(k, window) for k in range(window)]
    have = set(feats)
    missing = [c for c in cols if c not in have]
    if missing:
        raise SystemExit(f"⚠ seq 層の列が表に無い（{len(missing)} 本。例 {missing[:2]}）。"
                         "`feature_layers` に \"seq\" を入れて `cli.build` し直す")
    return cols


@register("feature", "seq")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    return {s: build_one(df) for s, df in panel.items()}
