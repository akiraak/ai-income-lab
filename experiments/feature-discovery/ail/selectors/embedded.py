"""F3 埋め込み型。⚠ **モデルの学習そのものに選別が入っている**（Lasso の係数・木の重要度）。

⚠ **木の重要度（MDI）は相関した列のあいだで重要度を分け合う**ので、
⚠ **「効いているのに重要度が低い」列が出る。** F3-5 はそれをクラスタでまとめて避ける。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LassoCV, lasso_path

from ail.registry import register

# ⚠ **F3-1b の探索の粗さ。** ⚠ **事前固定**（rules.md 13-3 規約 4。結果を見て動かさない）
_PATH_ALPHAS = 100


def _forest(seed):
    return RandomForestRegressor(n_estimators=60, max_depth=6, n_jobs=-1, random_state=seed)


@register("selector", "F3-1 Lasso")
def sel_lasso(X, y, k, ctx):
    """⚠ **この実装は α が強いと 0 本になり、先頭列 1 本へ落ちる**（2026-09-13 に判明）。

    ⚠ **直さない。** 台帳の既存 20 行がこの中身で測られており、⚠ **直すと全部が「別の手法」になる**
    （rules.md 14-10 規約 5「既存行は再計算しない」／ 利用者の裁定 2026-09-13 の (d)）。
    ⚠ **直したものは下の `F3-1b`。** ⚠ **新しい実験ではこちらではなく F3-1b を使う。**

    ⚠ **`k` を受け取りながら使っていない**のも当時のまま（残すことが (d) の約束である）。
    """
    m = LassoCV(cv=3, random_state=ctx["seed"], n_alphas=20, max_iter=2000).fit(X, y)
    keep = list(X.columns[np.abs(m.coef_) > 0])
    return keep or list(X.columns[np.argsort(-np.abs(m.coef_))[:1]])


@register("selector", "F3-1b Lasso（本数を固定）")
def sel_lasso_path(X, y, k, ctx):
    """F3-1 の直した版。⚠ **α を強い側から辿り、非ゼロが k 本に達した最初の α で止める。**

    ⚠ **黙ったフォールバックを作らないのが本手法の主題である**（F3-1 はそれで壊れていた）。
    そのため返す本数そのものを信号にする:

    - 非ゼロが **k 本以上**になる α がある → ⚠ **`|coef|` の大きい順にちょうど k 本**
    - 最小の α でも届かない        → ⚠ **届いた本数だけ返す**（台帳の「本数」が k を下回って見える）
    - 経路のどこでも **0 本**       → ⚠ **止める。** 黙って代わりの列を返さない

    ⚠ **`ctx["seed"]` は使わない。** `lasso_path` は座標降下で乱数を引かないので決定的である。
    ⚠ **標準化は呼ぶ側が訓練分割の内側で済ませている**（`cli/run.py`。rules.md 3 章 B）。
    """
    # ⚠ **`lasso_path` の `n_alphas` は非推奨ではない**（1.7 で非推奨になったのは `LassoCV` のほう。
    # ⚠ **`lasso_path` の `alphas` は配列しか受けない** ので、整数を渡すと 1.7.2 では落ちる）
    _, coefs, _ = lasso_path(np.asarray(X, dtype=float), np.asarray(y, dtype=float),
                             n_alphas=_PATH_ALPHAS)
    # coefs: (列, α)。⚠ **α は強い順**なので、左から見て最初に k 本に届いた列を採る
    nz = (np.abs(coefs) > 0).sum(axis=0)
    hit = np.flatnonzero(nz >= k)
    j = int(hit[0]) if hit.size else int(np.argmax(nz))
    c = np.abs(coefs[:, j])
    take = min(k, int((c > 0).sum()))
    if take == 0:
        raise SystemExit(
            "⚠ F3-1b: lasso_path のどの α でも非ゼロの係数が 1 本も無い"
            f"（列 {X.shape[1]} 本・行 {len(X):,}・α {_PATH_ALPHAS} 点）。"
            "⚠ **黙って代わりの列は返さない**（F3-1 がそれで壊れていた）。入力を確かめる")
    return list(X.columns[np.argsort(-c)[:take]])


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
