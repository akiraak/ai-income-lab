"""木・アンサンブル（勾配ブースティング）。⚠ **Ridge（linear.py）を超えないと採らない**（E8 の決定）。

⚠ **ハイパーパラメータは事前に固定し、チューニングしない**（[プラン §1](../../../../docs/plans/archive/gpu-models.md)。
⚠ **振った水準の数だけ n_trials が増える**。rules.md 11 章 規約 4）。
"""

from __future__ import annotations

from ail.registry import register
from ail.models.holdout import tail_holdout


@register("model", "LightGBM")
def lightgbm_gbdt(Xtr, ytr, Xte, ctx):
    import lightgbm as lgb

    seed = int(ctx.get("seed", 0))
    (Xf, yf), holdout = tail_holdout(Xtr, ytr)
    m = lgb.LGBMRegressor(
        num_leaves=31, learning_rate=0.05,
        n_estimators=int(ctx.get("lgbm_estimators", 500)),
        min_child_samples=100, colsample_bytree=0.8, subsample=1.0,
        random_state=seed, deterministic=True, force_row_wise=True,
        n_jobs=4, verbose=-1,
    )
    if holdout is None:
        m.fit(Xf, yf)
    else:
        Xv, yv = holdout
        m.fit(Xf, yf, eval_X=Xv, eval_y=yv, eval_metric="l2",
              callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(0)])
    return m.predict(Xte)
