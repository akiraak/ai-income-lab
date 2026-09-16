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


# ⚠ **ウェーブレットの水準は事前固定**（プラン `plans/ledger-blanks-six.md` §0-2）。
# ⚠ **基底と窓を振ると多重検定が増える** — 振るなら回す前に数える（rules.md 14-9）
WAVELET, WAVELET_LEVEL = "db4", 3
# ⚠ **窓の列の接頭辞**（`ail/features/seq.py` が作る `own_seq60_r{k}`。k = 0 が足 i）
SEQ_PREFIX = "own_seq"


@register("transform", "F4-4 多項式・交互作用の展開")
def tf_poly(Xtr, Xte, ctx):
    """⚠ **2 次の項と交互作用を全部作る**（35 列 → 665 列）。定数項は作らない。

    ⚠ **「特徴量を作る」側（F4 生成型）の最初の実施である。**
    ⚠ **列は組み合わせで増える** — 35 本なら 35（1 次）＋ 35（2 乗）＋ 595（交互作用）＝ 665 本。
    ⚠ **断面（`cs_`）を足すと爆発する**ので own だけで回す（カタログの「次の一手」）。

    ⚠ **標本から学ぶものは無い**（組み合わせは列の名前だけで決まる）。⚠ **それでも `transform` の口に
    置くのは、列を作り替える手法だからである**（`selector` の契約に入らない）。
    ⚠ **入力は `cli/run.py` が標準化した表**なので、2 乗の項は「標準化後の 2 乗」である。
    """
    import pandas as pd
    from sklearn.preprocessing import PolynomialFeatures

    p = PolynomialFeatures(degree=2, include_bias=False).fit(Xtr)
    names = [n.replace(" ", "*") for n in p.get_feature_names_out(list(Xtr.columns))]
    out = (pd.DataFrame(p.transform(Xtr), columns=names, index=Xtr.index),
           pd.DataFrame(p.transform(Xte), columns=names, index=Xte.index))
    return out[0], out[1], {"degree": 2, "入力の列": int(Xtr.shape[1]), "出力の列": len(names)}


@register("transform", "F5-4 ウェーブレット・スペクトル")
def tf_wavelet(Xtr, Xte, ctx):
    """⚠ **窓（`seq` 層・過去 60 営業日）を離散ウェーブレット変換の係数に置き換える。**

    基底 `db4` ・ レベル 3 ・ 端は対称拡張（pywt の既定）。⚠ **この 1 通りだけ回す**（§0-2）。
    ⚠ **生の窓 60 列は落とす** — 残すと同じ情報を 2 度渡すことになり、「ウェーブレットの効果」ではなくなる。
    ⚠ **`own` の 35 列はそのまま残す**（比較の相手が「own だけ」なので、足した分だけを処置にする）。

    ⚠ **標本から学ぶものは無い**（1 行ずつ独立に変換する）ので先読みの余地も無いが、
    ⚠ **列を作り替えるので `transform` の口に置く**（F4-4 と同じ理屈）。
    """
    import numpy as np
    import pandas as pd
    import pywt

    win = [c for c in Xtr.columns if c.startswith(SEQ_PREFIX)]
    if not win:
        raise SystemExit(f"⚠ F5-4: 窓の列（{SEQ_PREFIX}…）が表に無い。"
                         "`feature_layers` に \"seq\" を入れた表を `features_from` で読む")
    # ⚠ **古い → 新しい**に並べ替える（列は r0 = 足 i の新しい順で並んでいる）
    order = sorted(win, key=lambda c: int(c.rsplit("_r", 1)[1]), reverse=True)
    rest = [c for c in Xtr.columns if c not in set(win)]

    def conv(X):
        w = X[order].to_numpy(dtype=float)
        parts = pywt.wavedec(w, WAVELET, level=WAVELET_LEVEL, axis=1)
        coef = np.hstack(parts)
        names = [f"wv_{WAVELET}{WAVELET_LEVEL}_{i}" for i in range(coef.shape[1])]
        return pd.concat([X[rest], pd.DataFrame(coef, columns=names, index=X.index)], axis=1)

    tr, te = conv(Xtr), conv(Xte)
    return tr, te, {"wavelet": WAVELET, "level": WAVELET_LEVEL, "窓の列": len(order),
                    "係数の列": int(tr.shape[1] - len(rest)), "残した列": len(rest)}
