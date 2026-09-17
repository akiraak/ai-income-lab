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


# ⚠ **木の水準は `ail/models/trees.py` の `LightGBM` と同じ**（プラン §0-2。振らない）。
# ⚠ **早期打ち切りは使わない** — 選別は予測ではないので holdout を切らず、訓練分割を全部使う
_LGBM_LEVELS = dict(num_leaves=31, learning_rate=0.05, min_child_samples=100,
                    colsample_bytree=0.8, subsample=1.0, deterministic=True,
                    force_row_wise=True, n_jobs=4, verbose=-1)


@register("selector", "F3-4 SHAP")
def sel_shap(X, y, k, ctx):
    """⚠ **LightGBM の `pred_contrib`（厳密な TreeSHAP）で、平均 `|SHAP|` の上位 k 本。**

    ⚠ **`shap` パッケージは入れない** — LightGBM 本体が同じ値を返す（プラン §0-1）。
    ⚠ **返る列は「入力の列数 ＋ 1」**で、最後の 1 本は期待値（基準値）なので落とす。

    ⚠ **SHAP は説明であって因果ではない**（Kumar ら 2020）。⚠ **「効いている列」ではなく
    ⚠ **「そのモデルの予測をどれだけ動かしたか」**を測っている。⚠ **モデルが雑音を学べば、
    その雑音の寄与が大きく出る。**
    """
    import lightgbm as lgb

    m = lgb.LGBMRegressor(n_estimators=int(ctx.get("lgbm_estimators", 500)),
                          random_state=ctx.get("seed", 0), **_LGBM_LEVELS)
    m.fit(X, np.asarray(y, dtype=float))
    contrib = np.asarray(m.predict(X, pred_contrib=True))[:, :X.shape[1]]
    imp = np.abs(contrib).mean(axis=0)
    return list(X.columns[np.argsort(-imp)[:min(int(k), X.shape[1])]])


# ⚠ **目標の偽発見率。事前固定**（プラン §0-2。⚠ **結果を見て緩めない**）
KNOCKOFF_Q = 0.1
# ⚠ **Lasso の経路の粗さ**（F3-1b と同じ。⚠ 振らない）
_KNOCKOFF_ALPHAS = 100


def gaussian_knockoffs(X: np.ndarray, seed: int) -> np.ndarray:
    """ガウス model-X の knockoff を等相関構成で作る（Candès ら 2018 の式）。

        X̃ = X (I − Σ⁻¹ S) + E C,   S = diag(s),  s = min(1, 2 λ_min(Σ)),
        CᵀC = 2S − S Σ⁻¹ S,  E ~ N(0, I)

    ⚠ **Σ は訓練分割だけから推定する**（rules.md 3 章 B）。
    ⚠ **「特徴量がガウス分布である」という仮定に乗っている** — own 35 列は裾が重いので、
    ⚠ **仮定が外れれば偽発見率の保証も外れる**（記録の限界に書く）。
    """
    X = np.asarray(X, dtype=float)
    n, p = X.shape
    mu = X.mean(axis=0)
    Xc = X - mu
    sigma = np.cov(Xc, rowvar=False)
    sigma = sigma + np.eye(p) * 1e-8                 # ⚠ 特異になりにくくする（数値の保険）
    lam_min = float(np.linalg.eigvalsh(sigma).min())
    s = np.full(p, min(1.0, 2.0 * max(lam_min, 0.0)) * float(np.mean(np.diag(sigma))))
    S = np.diag(s)
    inv = np.linalg.pinv(sigma)
    mat = 2.0 * S - S @ inv @ S
    w, V = np.linalg.eigh(mat)                       # ⚠ 数値誤差で微小な負が出るので切り上げる
    C = V @ np.diag(np.sqrt(np.clip(w, 0.0, None))) @ V.T
    # ⚠ **種は `seed` そのものではなく、形も混ぜた別の流れから引く**（2026-09-15 に踏んだ罠）。
    # ⚠ **`default_rng(seed)` で引くと、同じ種で作った合成データと E がビット単位で一致し、
    # ⚠ **偽物が本物のコピーになる**（対角の相関 0.99）。⚠ **そうなると枠は何も検出できない。**
    E = np.random.default_rng(np.random.SeedSequence([int(seed), n, p, 0x6B6E6F66])).normal(size=(n, p))
    return mu + Xc @ (np.eye(p) - inv @ S) + E @ C


@register("selector", "F3-6 Model-X knockoffs")
def sel_knockoffs(X, y, k, ctx):
    """⚠ **偽の列（knockoff）を作って、本物がそれより強く選ばれた列だけを採る。**

    統計量は Lasso の経路に入る早さ: `Z_j` ＝ その列の係数が最初に非ゼロになった α、
    `W_j = Z_j − Z̃_j`。⚠ **knockoff+ のしきい値**

        τ = min{ t > 0 : (1 + #{W ≤ −t}) / max(1, #{W ≥ t}) ≤ q }

    を満たす最小の t を採り、`W_j ≥ τ` の列を返す（q = `KNOCKOFF_Q`）。

    ⚠ **k を使わない**（本数は枠が決める。F1-5 検定+FDR・F2-3 Boruta と同じ）。
    ⚠ **1 本も残らなければ `W` が最大の 1 本に落とす**（既存の約束。⚠ **黙って別の列は返さない** —
    どちらになったかは選んだ本数として台帳に出る）。
    """
    Xv = np.asarray(X, dtype=float)
    Xt = gaussian_knockoffs(Xv, int(ctx.get("seed", 0)))
    aug = np.hstack([Xv, Xt])
    alphas, coefs, _ = lasso_path(aug, np.asarray(y, dtype=float), n_alphas=_KNOCKOFF_ALPHAS)
    nz = np.abs(coefs) > 0                           # (列, α)。⚠ α は強い順
    first = np.where(nz.any(axis=1), alphas[np.argmax(nz, axis=1)], 0.0)
    p = Xv.shape[1]
    W = first[:p] - first[p:]
    ts = np.unique(np.abs(W[W != 0]))
    tau = None
    for t in np.sort(ts):
        fdp = (1 + int((W <= -t).sum())) / max(1, int((W >= t).sum()))
        if fdp <= KNOCKOFF_Q:
            tau = float(t)
            break
    keep = list(X.columns[W >= tau]) if tau is not None else []
    return keep or list(X.columns[[int(np.argmax(W))]])
