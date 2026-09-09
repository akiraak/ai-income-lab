"""線形・正則化。⚠ **複雑なモデルはこれを超えないと採らない**（E8 の決定）。"""

from __future__ import annotations

from sklearn.linear_model import Ridge

from ail.registry import register


@register("model", "Ridge")
def ridge(Xtr, ytr, Xte, ctx):
    return Ridge(alpha=ctx.get("alpha", 1.0)).fit(Xtr, ytr).predict(Xte)
