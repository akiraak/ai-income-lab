"""`cs_` ⚠ **断面。同じ時刻の全銘柄を見て作る**（E8 の系統 J）。

⚠ **これが「全銘柄を使って NVDA を当てる」形の入口である。** `own_` だけの世界では、
⚠ **各行は自分の履歴しか見ない**ので、63 銘柄を並べても「1 銘柄の実験を 63 回やった」のと変わらない。

⚠ **先読みにならない理由**: 足 i の断面が使うのは、⚠ **同じ足 i で閉じた他銘柄の値**だけである。
⚠ **時刻の突き合わせは完全一致でしか行わない**（前方埋めをすると、休場の銘柄に未来の値が入る）。

⚠ **全銘柄が揃う時刻でしか意味を持たない。** 揃わない時刻は「その時刻に居る銘柄だけの断面」になるので、
⚠ **銘柄数 `cs_n` を特徴量として残し、少ない時刻を後で落とせるようにする。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.registry import register

PREFIX = "cs_"

# 断面を取る土台。⚠ **`own_` の列名で指定する**（own を先に作ってから呼ぶ）
DEFAULT_BASES = ("own_ret_1", "own_ret_5", "own_ret_20", "own_vol_20",
                 "own_vratio_5", "own_z_20", "own_dollar_20")
MIN_SYMBOLS = 5     # ⚠ これ未満の時刻の断面は意味が無いので NaN にする


def _wide(panel: dict[str, pd.DataFrame], own: dict[str, pd.DataFrame], col: str) -> pd.DataFrame:
    """時刻 × 銘柄の行列にする。⚠ **前方埋めをしない**（休場の穴は穴のまま）。"""
    return pd.DataFrame({s: pd.Series(own[s][col].values, index=panel[s]["ts"].values)
                         for s in panel if col in own[s]})


@register("feature", "cs")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    own = ctx["own"]
    bases = tuple(ctx.get("cs_bases", DEFAULT_BASES))
    out = {s: pd.DataFrame(index=df.index) for s, df in panel.items()}

    for base in bases:
        W = _wide(panel, own, base)
        if W.empty:
            continue
        n = W.notna().sum(axis=1)
        enough = n >= MIN_SYMBOLS
        # ⚠ 順位は 0〜1 に正規化する（銘柄数が時刻で変わるので、生の順位だと比べられない）
        rank = W.rank(axis=1, pct=True).where(enough, np.nan)
        mean = W.mean(axis=1).where(enough, np.nan)
        std = W.std(axis=1).where(enough, np.nan)
        z = (W.sub(mean, axis=0)).div(std.replace(0, np.nan), axis=0)
        short = base[len("own_"):]
        for s, df in panel.items():
            if s not in W:
                continue
            idx = df["ts"].values
            o = out[s]
            o[f"{PREFIX}rank_{short}"] = rank[s].reindex(idx).values
            o[f"{PREFIX}z_{short}"] = z[s].reindex(idx).values
            o[f"{PREFIX}demean_{short}"] = (W[s] - mean).reindex(idx).values

    # --- 断面そのものの姿（全銘柄に共通の列。市場の状態を表す） ---
    W = _wide(panel, own, "own_ret_1")
    if not W.empty:
        n = W.notna().sum(axis=1)
        enough = n >= MIN_SYMBOLS
        breadth = (W > 0).sum(axis=1).div(n.replace(0, np.nan)).where(enough, np.nan)
        disp = W.std(axis=1).where(enough, np.nan)
        skew = W.skew(axis=1).where(enough, np.nan)
        mean = W.mean(axis=1).where(enough, np.nan)
        for s, df in panel.items():
            idx = df["ts"].values
            o = out[s]
            o[f"{PREFIX}n"] = n.reindex(idx).values
            o[f"{PREFIX}breadth_up"] = breadth.reindex(idx).values
            o[f"{PREFIX}disp_ret_1"] = disp.reindex(idx).values
            o[f"{PREFIX}skew_ret_1"] = skew.reindex(idx).values
            o[f"{PREFIX}mean_ret_1"] = mean.reindex(idx).values
    return out
