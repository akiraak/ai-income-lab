"""比較対象 3 種（記録 `cgan-scenario.md` §3-1。⚠ **2026-09-19 にテストの成績を見る前に固定**）。

1. 履歴ベース — 連続 5 日のリターンの塊を復元抽出（塊の中の順番は保つ。条件は見ない）
2. 軽量モデル — 起点の足の特徴量だけ。上昇確率はロジスティック回帰、5 日累積リターンは線形の分位点回帰
3. 既存モデル — Ridge の点予測（⚠ 分布の指標は「対象外」。無理に確率モデルとして扱わない）

⚠ **3 つとも「答えがテストの始まりより前に確定した起点」（学習 ∪ 検証）で fit する。**
選ぶ checkpoint が無いので、検証期間を使わせないと不当に弱い基準線になる（cGAN に厳しい側に倒す）。
"""

from __future__ import annotations

import numpy as np

from ail.scenario import metrics

LIGHT_QUANTILES = (0.025,) + tuple(round(0.05 * k, 2) for k in range(1, 20)) + (0.975,)   # 21 本


def historical(Y_pool: np.ndarray, n_origins: int, n: int, seed: int = 0) -> np.ndarray:
    """`Y_pool` `[k, horizon]`（日次対数）から n 本を復元抽出。⚠ **どの起点にも同じ n 本**（条件を見ない）。

    返り値 `[n_origins, n, horizon]`（メモリを食わないよう broadcast した読み取り専用の view）。
    """
    rng = np.random.default_rng(seed)
    draw = np.asarray(Y_pool, dtype=np.float32)[rng.integers(0, len(Y_pool), size=n)]
    return np.broadcast_to(draw, (n_origins, n, draw.shape[1]))


def light(X_fit: np.ndarray, Y_fit: np.ndarray, X_test: np.ndarray, n: int) -> dict:
    """起点の足の特徴量（窓の最後の 1 行・標準化済み）だけを使う軽量モデル。

    返り値: `prob_up` `[n_test]`・`samples_5d` `[n_test, n]`（⚠ **分位点を線形につないだ分位関数から取った近似**。
    2.5% より外・97.5% より外は端の値で打ち切る）。⚠ 経路は出さないので、下落リスクは「対象外」。
    """
    from sklearn.linear_model import LogisticRegression, QuantileRegressor

    a, b = X_fit[:, -1, :].astype(np.float64), X_test[:, -1, :].astype(np.float64)
    y5 = metrics.cumulative_returns(Y_fit)[:, -1].astype(np.float64)
    prob = LogisticRegression(C=1.0, max_iter=1000).fit(a, (y5 > 0).astype(int)).predict_proba(b)[:, 1]
    q = np.column_stack([QuantileRegressor(quantile=t, alpha=0.0, solver="highs").fit(a, y5).predict(b)
                         for t in LIGHT_QUANTILES])
    q = np.sort(q, axis=1)                                          # 交差した分位点は並べ替える
    u = (np.arange(n) + 0.5) / n
    samples = np.stack([np.interp(u, LIGHT_QUANTILES, row) for row in q])
    return {"prob_up": prob, "samples_5d": samples}


def ridge_point(X_fit: np.ndarray, Y_fit: np.ndarray, X_test: np.ndarray) -> np.ndarray:
    """Ridge（α=1）。6 列 × 60 日を平らにした列 → 5 日累積リターンの点予測 `[n_test]`。"""
    from sklearn.linear_model import Ridge

    y5 = metrics.cumulative_returns(Y_fit)[:, -1]
    m = Ridge(alpha=1.0).fit(X_fit.reshape(len(X_fit), -1), y5)
    return m.predict(X_test.reshape(len(X_test), -1))
