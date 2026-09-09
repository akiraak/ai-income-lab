"""⚠ **基準線。これを超えない予測は「何も学んでいない」。**

⚠ **「常に上」は株のドリフト（上がる日のほうが多い）だけで的中率が 50% を超える。**
⚠ **これを超えない予測に価値は無い。** ただし「常に上」はほとんど回転しないので
⚠ **本当はコストを払わない**（純利の比較では、この差を必ず併記する）。

基準線は説明変数を見ないので、⚠ **`predict(te)` だけを持つ**（学習しない）。
"""

from __future__ import annotations

import numpy as np

from ail.registry import register


@register("model", "常に上（ドリフト）")
def always_up(tr, te, feats, ctx):
    return np.ones(len(te))


@register("model", "直前リターンの符号")
def last_return(tr, te, feats, ctx):
    """⚠ **モメンタムの最も素朴な形。** これに負ける「モメンタム手法」は名前だけである。"""
    col = "own_ret_1" if "own_ret_1" in te else "ret_1"
    return np.sign(te[col].values)
