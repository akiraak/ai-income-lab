"""⚠ **標本から学ぶ変換。ここで作ったものを `data/derived/` に置いてはいけない**（rules.md 3 章の B）。

⚠ **訓練分割の内側で fit し、検証分割には transform だけを当てる。**
⚠ **事前に全期間で fit してキャッシュすると先読みになる**（rules.md 3 章）。

⚠ **fit した係数は `runs/<実行>/fitted/` に残すが、次の実行では読み込まない。**
読み込むと、その係数を通じて分割をまたいで情報が漏れる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler


class FittedTransform:
    """⚠ **fit と transform を必ず分ける入れもの。** `fit_transform` を検証分割に当てないため。"""

    def __init__(self, kind: str, **kwargs):
        self.kind = kind
        self.kwargs = kwargs
        self._impl = None
        self.columns: list[str] = []

    def fit(self, X: pd.DataFrame) -> "FittedTransform":
        self.columns = list(X.columns)
        self._impl = {"standardize": StandardScaler,
                      "pca": PCA}[self.kind](**self.kwargs).fit(X)
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        if self._impl is None:
            raise RuntimeError("⚠ fit していない。検証分割に fit_transform を当ててはいけない")
        out = self._impl.transform(X[self.columns])
        cols = (self.columns if self.kind == "standardize"
                else [f"pc{i}" for i in range(out.shape[1])])
        return pd.DataFrame(out, columns=cols, index=X.index)

    def coefficients(self) -> dict:
        """⚠ **`runs/<実行>/fitted/` に残す中身。** 再現の確認にだけ使う。"""
        if self.kind == "standardize":
            return {"mean": np.asarray(self._impl.mean_).tolist(),
                    "scale": np.asarray(self._impl.scale_).tolist(),
                    "columns": self.columns}
        return {"explained_variance_ratio": self._impl.explained_variance_ratio_.tolist(),
                "columns": self.columns}


def frac_diff_order(x: pd.Series, candidates=np.arange(0.1, 1.01, 0.1),
                    p_target: float = 0.05) -> float:
    """⚠ **分数差分の次数 d を標本から選ぶ = 推定である。** 訓練分割の内側でだけ呼ぶこと。

    定常になる（ADF の p 値が `p_target` を下回る）最小の d を返す。
    """
    from statsmodels.tsa.stattools import adfuller   # 任意依存。使うときだけ入れる
    from ail.data.transforms.series import frac_diff
    for d in candidates:
        s = frac_diff(x, float(d)).dropna()
        if len(s) > 50 and adfuller(s, maxlag=1, regression="c")[1] < p_target:
            return float(d)
    return float(candidates[-1])
