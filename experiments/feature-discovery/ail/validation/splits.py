"""ウォークフォワード ／ パージ ／ エンバーゴ（E8 の L1・L2）。

⚠ **時系列を無作為に切ってはいけない。** 足 i のラベルは足 i+k の終値を含むので、
⚠ **無作為に切ると訓練と検証が同じ未来を共有する。**

⚠ **断面を使うときは、同じ時刻の他銘柄が境目を跨がないように 1 本空ける**（エンバーゴ）。
"""

from __future__ import annotations

import pandas as pd

from ail.registry import register


@register("split", "walk_forward")
def walk_forward(panel: pd.DataFrame, n_folds: int, horizon_min: float,
                 embargo_bars: int = 0, bar_minutes: float = 0.0):
    """時刻順に `n_folds + 1` 等分し、(訓練, 検証) を古い順に返す。

    ⚠ **訓練は「その分割より前の全部」**（増えていく窓）。
    ⚠ **パージ**: ラベルが検証期間に食い込む訓練行を落とす（`horizon_min` 分ぶん）。
    """
    panel = panel.sort_values("ts").reset_index(drop=True)
    ts = pd.to_datetime(panel["ts"])
    edges = pd.qcut(ts.rank(method="first"), n_folds + 1, labels=False)
    for f in range(1, n_folds + 1):
        tr, te = panel[edges < f], panel[edges == f]
        if len(tr) < 500 or len(te) < 200:
            continue
        cut = pd.to_datetime(te["ts"]).min() - pd.Timedelta(
            minutes=horizon_min + embargo_bars * bar_minutes)
        tr = tr[pd.to_datetime(tr["ts"]) < cut]
        if len(tr) < 500:
            continue
        yield f, tr, te
