"""F2 ラッパー型。⚠ **モデルを実際に回して選ぶ**（強い・遅い）。

⚠ **回す回数だけ多重検定になる。** F2 で選んだ列は、F1 で選んだ列より過学習しやすい。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

from ail.registry import register


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
