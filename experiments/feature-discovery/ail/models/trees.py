"""木・アンサンブル（勾配ブースティング）。⚠ **Ridge（linear.py）を超えないと採らない**（E8 の決定）。

⚠ **ハイパーパラメータは評価期間の結果で動かさない**（[プラン §1](../../../../docs/plans/archive/gpu-models.md)。
⚠ **外から振った水準の数だけ n_trials が増える**。rules.md 11 章 規約 4・13-6 規約 3）。
⚠ **葉の数・学習率・最小標本は ctx から読む**（`lgbm_leaves`・`lgbm_lr`・`lgbm_min_child`。2026-10-09）。
渡すのは `LightGBM（内側選抜）`（`tuned.py`。訓練分割の尻で選ぶ ＝ rules.md 14-12）だけで、
⚠ **既定の値はいままでの定数と同じ** ＝ 項目の無い config は 1 ビットも変わらない。
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
        num_leaves=int(ctx.get("lgbm_leaves", 31)), learning_rate=float(ctx.get("lgbm_lr", 0.05)),
        n_estimators=int(ctx.get("lgbm_estimators", 500)),
        min_child_samples=int(ctx.get("lgbm_min_child", 100)), colsample_bytree=0.8, subsample=1.0,
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
