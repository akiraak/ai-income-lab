"""F3 埋め込み型。⚠ **モデルの学習そのものに選別が入っている**（Lasso の係数・木の重要度）。

⚠ **木の重要度（MDI）は相関した列のあいだで重要度を分け合う**ので、
⚠ **「効いているのに重要度が低い」列が出る。** F3-5 はそれをクラスタでまとめて避ける。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LassoCV

from ail.registry import register


def _forest(seed):
    return RandomForestRegressor(n_estimators=60, max_depth=6, n_jobs=-1, random_state=seed)


@register("selector", "F3-1 Lasso")
def sel_lasso(X, y, k, ctx):
    m = LassoCV(cv=3, random_state=ctx["seed"], n_alphas=20, max_iter=2000).fit(X, y)
    keep = list(X.columns[np.abs(m.coef_) > 0])
    return keep or list(X.columns[np.argsort(-np.abs(m.coef_))[:1]])


@register("selector", "F3-2 木の重要度")
def sel_mdi(X, y, k, ctx):
    rf = _forest(ctx["seed"]).fit(X, y)
    return list(X.columns[np.argsort(rf.feature_importances_)[::-1][:k]])


@register("selector", "F3-3 並べ替え(MDA)")
def sel_mda(X, y, k, ctx):
    """列を 1 本ずつ壊して、⚠ **成績がどれだけ落ちるか**で測る。"""
    rf = _forest(ctx["seed"]).fit(X, y)
    base = rf.score(X, y)
    rng = np.random.default_rng(ctx["seed"])
    drop = {}
    for c in X.columns:
        Xp = X.copy(); Xp[c] = rng.permutation(Xp[c].values)
        drop[c] = base - rf.score(Xp, y)
    return list(pd.Series(drop).nlargest(k).index)


@register("selector", "F3-5 クラスタ化MDA")
def sel_cmda(X, y, k, ctx):
    """⚠ **相関した列をまとめて壊す。** 1 本ずつ壊すと、代役がいる列は「効いていない」と誤判定される。"""
    C = X.corr().abs().fillna(0).values
    ncl = max(2, k // 2)
    lab = KMeans(n_clusters=ncl, n_init=5, random_state=ctx["seed"]).fit_predict(1 - C)
    rf = _forest(ctx["seed"]).fit(X, y)
    base = rf.score(X, y)
    rng = np.random.default_rng(ctx["seed"])
    drop = {}
    for cl in np.unique(lab):
        Xp = X.copy()
        for c in X.columns[lab == cl]:
            Xp[c] = rng.permutation(Xp[c].values)
        drop[cl] = base - rf.score(Xp, y)
    keep = []
    for cl in sorted(drop, key=drop.get, reverse=True):
        keep += list(X.columns[lab == cl])
        if len(keep) >= k:
            break
    return keep[:k]
