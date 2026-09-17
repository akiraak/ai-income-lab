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
    ⚠ **行 rank で切る**ので、行数の違うパネル（1 銘柄と 63 銘柄）では境目がずれる。
    形式 (A)(B) を比べる閾値つき売買では `date_edges` ＋ `folds_by_dates` を使う（rules.md 13-6 の 1）。
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


def date_edges(ts, n_folds: int) -> list[pd.Timestamp]:
    """fold の切れ目を **日付で 1 回決める**（rules.md 13-6 の 1）。

    ⚠ **全銘柄共通の日付集合から決め、(A) 共通 1 本にも (B) 銘柄別にも同じ edge を配る。**
    行 rank で切ると 63 銘柄のパネルと 1 銘柄のパネルで境目がずれ、同じ fold と呼べなくなる。
    戻り値は昇順の切れ目で、fold g の検証は [edges[g], edges[g+1])。
    """
    u = (pd.Series(pd.to_datetime(pd.Series(ts)).unique())
           .sort_values().reset_index(drop=True))
    if len(u) < n_folds + 1:
        raise ValueError(f"日付が {len(u)} 個しかなく {n_folds} fold に切れない")
    grp = pd.qcut(u.rank(method="first"), n_folds + 1, labels=False)
    starts = [u[grp == g].iloc[0] for g in range(n_folds + 1)]
    return list(starts) + [u.iloc[-1] + pd.Timedelta(1, "ns")]


def folds_by_dates(panel: pd.DataFrame, edges: list[pd.Timestamp], horizon_min: float,
                   embargo_bars: int = 0, bar_minutes: float = 0.0,
                   min_train: int = 100, min_test: int = 20):
    """`date_edges` の切れ目で (訓練, 検証) を返す。パージは `walk_forward` と同じ。

    ⚠ **下限は `walk_forward` より緩い**（(B) 銘柄別は最初の fold の訓練が約 360 行/銘柄しか
    なく、500 で切ると fold が消える。薄さは消せない限界としてそのまま回す。rules.md 13-6 の 5）。
    """
    panel = panel.sort_values("ts").reset_index(drop=True)
    ts = pd.to_datetime(panel["ts"])
    for f in range(1, len(edges) - 1):
        te = panel[(ts >= edges[f]) & (ts < edges[f + 1])]
        tr = panel[ts < edges[f]]
        if len(te) < min_test or len(tr) < min_train:
            continue
        cut = pd.to_datetime(te["ts"]).min() - pd.Timedelta(
            minutes=horizon_min + embargo_bars * bar_minutes)
        tr = tr[pd.to_datetime(tr["ts"]) < cut]
        if len(tr) < min_train:
            continue
        yield f, tr, te


@register("split", "walk_forward_dates")
def walk_forward_dates(panel: pd.DataFrame, n_folds: int, horizon_min: float,
                       embargo_bars: int = 0, bar_minutes: float = 0.0):
    """日付基準の切れ目で回すウォークフォワード（config の `validation.split` 用）。"""
    yield from folds_by_dates(panel, date_edges(panel["ts"], n_folds),
                              horizon_min, embargo_bars, bar_minutes)
