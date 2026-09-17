"""F5 表現学習型（4 件）。PCA ／ オートエンコーダ ／ 行列プロファイル ／ ウェーブレット。

⚠ **ここの手法は `selector` ではなく `transform` である**（プラン `plans/selectors-small-four.md` §1-1）。
⚠ **選別の契約は「`X` にある列の名前を返す」**（`cli/run.py` が `Xtr[cols]` を取る）だが、
⚠ **表現学習は新しい列を作る**ので、その契約に入らない。⚠ **検証分割 `Xte` も選別には渡らない**ので、
transform を当てる先が手に入らない。だから種類を 1 つ分けた。

変換の契約: `fn(Xtr, Xte, ctx) -> (Xtr, Xte, 係数)`。
⚠ **fit は訓練分割だけ・検証分割には transform だけを当てる**（rules.md 3 章 B）。
⚠ **係数は `runs/<実行>/fitted/` に残すが、次の実行では読み込まない。**
"""

from __future__ import annotations

from ail.data.transforms.fitted import FittedTransform
from ail.registry import register


@register("transform", "F5-1 PCA")
def tf_pca(Xtr, Xte, ctx):
    """⚠ **訓練分割の内側で主成分を取り、検証分割には transform だけを当てる。**

    ⚠ **成分の数は k と揃える**（既定 16。プラン §1 で事前固定）。⚠ **モデルに渡る列の数を
    「全部使う」と同じにしておかないと、比べているのが直交化の効果か列数の効果か分からなくなる。**
    ⚠ **水準を増やすとそのぶん `n_trials` が増える**ので、増やすなら回す前に決める（rules.md 14-9）。
    """
    n = min(int(ctx.get("pca_components", ctx.get("k", 16))), Xtr.shape[1], len(Xtr))
    t = FittedTransform("pca", n_components=n, random_state=ctx.get("seed", 0)).fit(Xtr)
    return t.transform(Xtr), t.transform(Xte), t.coefficients()
