"""F2 ラッパー型。⚠ **モデルを実際に回して選ぶ**（強い・遅い）。

⚠ **回す回数だけ多重検定になる。** F2 で選んだ列は、F1 で選んだ列より過学習しやすい。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_selection import RFE
from sklearn.linear_model import Ridge

from ail.registry import register


@register("selector", "F2-2 RFE")
def sel_rfe(X, y, k, ctx):
    """⚠ **弱い列を 1 本ずつ落として、そのたびにモデルを回し直す**（再帰的特徴量削減）。

    ⚠ **重要度の定義に依存する**ので、F3-1（係数）・F3-2（木の重要度）の弱点をそのまま引き継ぐ。
    ⚠ **推定器は本体のモデルと揃えて Ridge にする**（選別の効きとモデルの違いを混ぜないため。
    プラン `plans/selectors-small-four.md` §1 で事前固定）。

    ⚠ **列の尺度に依存する。** 係数の大きさで順位を付けるので、⚠ **尺度が揃っていない表を渡すと
    ⚠ **「尺度が小さい列ほど係数が大きい」だけで順位が決まる**（2026-09-12 にテストで踏んだ）。
    ⚠ **`cli/run.py` は `StandardScaler` を当てた表を渡すので本番では揃っている**が、
    ⚠ **この前提が崩れると F2-2 はラベルを見ていないのと同じになる。**
    """
    n = min(int(k), X.shape[1])
    est = Ridge(alpha=ctx.get("alpha", 1.0))
    fit = RFE(est, n_features_to_select=n, step=1).fit(X, y)
    return list(X.columns[fit.support_])


@register("selector", "F2-3 Boruta")
def sel_boruta(X, y, k, ctx):
    """⚠ **影の特徴量（列を並べ替えた偽物）より重要度が高い列だけ**を残す。

    ⚠ **k を使わない。** 「何本選ぶか」を決め打ちしないのがこの手法の主張である。
    """
    rng = np.random.default_rng(ctx["seed"])
    sh = X.apply(lambda c: rng.permutation(c.values))
    sh.columns = [f"shadow_{c}" for c in X.columns]
    both = pd.concat([X, sh], axis=1)
    rf = RandomForestRegressor(n_estimators=60, max_depth=6, n_jobs=-1, random_state=ctx["seed"])
    rf.fit(both, y)
    imp = pd.Series(rf.feature_importances_, index=both.columns)
    thr = imp[[c for c in sh.columns]].max()
    keep = [c for c in X.columns if imp[c] > thr]
    return keep or list(imp[X.columns].nlargest(1).index)
