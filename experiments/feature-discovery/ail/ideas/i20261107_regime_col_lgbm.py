"""登録簿 `20261107-regime-col-lgbm`（参考あり・枠の内。記録 dev-loop.md）:
R2（`i20261106_regime_ex_ridge`）と同じ外生の局面を、**局面ごとにモデルを分ける代わりに one-hot の列として 1 本の LightGBM に渡す** 検知器 R3。

- 局面: R2 の `regimes`（`ex_UST*` ／ `ex_EURTO*` の 28 列・訓練分割の日だけで標準化と k-means・K ＝ 2 ／ 4・種 0）をそのまま呼ぶ。
- 学習器の入力: own 35 列（＋ leak 対照の LEAK_ 列）＋ 局面の one-hot `regime_k0..k{K-1}`（足す・置き換えない）。ex は入れない。
  学習器は ctx の model（config の `LightGBM`・既定の水準）。較正は `calibrate.fit`（訓練分割の尻）。
- 対照「R3 局面の列なし（own のみ・LightGBM）」: 同じ表・同じ切れ目・同じ経路で局面の列だけを外す（登録簿の「局面の列なし」＝ 数える 3 本のうち 1 本）。
- 記録（登録簿の failure_modes ／ prediction の確かめ・診断だけ）: R2 の局面の記録 ＋ 局面の列の重要度（gain）と全列の中の順位。
  ⚠ 重要度は ctx の model とは別に、同じ水準の LightGBM を訓練分割で 1 回 fit して取る（予測には使わない）。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.ideas import i20261106_regime_ex_ridge as r2
from ail.models import calibrate
from ail.registry import register

KS = (2, 4)            # ⚠ 事前固定（登録簿 ＝ R2 と同じ）
CONTROL = "R3 局面の列なし（own のみ・LightGBM）"


def name_of(k: int) -> str:
    return f"R3 局面の番号を列にした LightGBM（外生で k-means・{k}個）"


def onehot(reg: np.ndarray, k: int) -> pd.DataFrame:
    return pd.DataFrame({f"regime_k{i}": (reg == i).astype(float) for i in range(k)})


def importance(Xtr: pd.DataFrame, ytr: np.ndarray, ctx: dict) -> dict[str, float]:
    """trees.lightgbm_gbdt と同じ水準（early stopping なし）で fit した gain の重要度。⚠ 診断だけ。"""
    import lightgbm as lgb

    m = lgb.LGBMRegressor(num_leaves=int(ctx.get("lgbm_leaves", 31)), learning_rate=float(ctx.get("lgbm_lr", 0.05)),
                          n_estimators=int(ctx.get("lgbm_estimators", 500)),
                          min_child_samples=int(ctx.get("lgbm_min_child", 100)), colsample_bytree=0.8, subsample=1.0,
                          random_state=int(ctx.get("seed", 0)), deterministic=True, force_row_wise=True, n_jobs=4,
                          verbose=-1, importance_type="gain")
    m.fit(Xtr, ytr)
    return dict(zip(Xtr.columns, (float(v) for v in m.feature_importances_)))


def importance_doc(imp: dict[str, float]) -> dict:
    s = pd.Series(imp).sort_values(ascending=False)
    rank = {c: int(i) + 1 for i, c in enumerate(s.index)}
    tot = float(s.sum()) or 1.0
    reg = [c for c in s.index if c.startswith("regime_k")]
    return {"局面の列の重要度の割合": round(sum(float(s[c]) for c in reg) / tot, 5),
            "局面の列の順位": {c: rank[c] for c in reg}, "列の数": int(len(s)),
            "重要度の上位5": [str(c) for c in s.index[:5]]}


def r3(k: int | None, tr, te, feats, ctx):
    cols = [c for c in feats if c.startswith("own_") or c.startswith("LEAK_")]
    Xtr = tr[cols].reset_index(drop=True).astype(float)
    Xte = te[cols].reset_index(drop=True).astype(float)
    doc: dict = {}
    if k is not None:
        rcols = r2.regime_columns(feats)
        if not rcols:
            raise SystemExit(f"⚠ {name_of(k)} は表に ex_UST* ／ ex_EURTO* の列が要る（own ＋ ex の表で回す）")
        rtr, rte, doc = r2.regimes(tr, te, k, rcols)
        Xtr = pd.concat([Xtr, onehot(rtr, k)], axis=1)
        Xte = pd.concat([Xte, onehot(rte, k)], axis=1)
    ytr = tr["y"].to_numpy(dtype=float)
    fn = ctx["model"]
    cal = calibrate.fit(fn, Xtr, ytr, ctx)
    buy = np.asarray(cal.buy_pct(fn(Xtr, ytr, Xte, ctx)), dtype=float)
    doc = {**doc, "columns": list(Xtr.columns), **cal.doc}
    if k is not None and ctx.get("r3_importance", True):
        doc.update(importance_doc(importance(Xtr, ytr, ctx)))
    return buy, doc


for _k in KS:
    register("detector", name_of(_k))(lambda tr, te, feats, ctx, _k=_k: r3(_k, tr, te, feats, ctx))
register("detector", CONTROL)(lambda tr, te, feats, ctx: r3(None, tr, te, feats, ctx))
