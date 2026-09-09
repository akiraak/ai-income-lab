"""`ll_` ⚠ **リードラグ。他銘柄の遅れた値を説明変数にする**（E8 の J3）。

⚠ **必ず 1 本以上ずらす。** 同じ足の他銘柄は `cs_` `rel_` の担当で、ここは
⚠ **「A が動いた次の足で B が動く」を探す層**である。ずらし忘れると
⚠ **同時刻の情報を「予測」と呼ぶことになる**（先読みではないが、意味が変わる）。

⚠ **組み合わせが N² で増えるので多重検定の温床**（63 銘柄 × ラグ 4 本で 252 列）。
⚠ **FDR・デフレーテッド SR と対で使わないと、必ず「効く組み合わせ」が見つかってしまう。**
"""

from __future__ import annotations

import pandas as pd

from ail.registry import register

PREFIX = "ll_"
DEFAULT_LAGS = (1, 2, 3, 5)


def _name(sym: str) -> str:
    """`BRK/B` は列名にできないので `BRK-B` にする（rules.md 4 章）。"""
    return sym.replace("/", "-")


@register("feature", "ll")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    own = ctx["own"]
    leaders = ctx.get("ll_leaders")
    if leaders in (None, "etf"):
        leaders = [s for s in ctx.get("etf", []) if s in panel]
    elif leaders == "all":
        leaders = sorted(panel)
    lags = tuple(ctx.get("ll_lags", DEFAULT_LAGS))

    wide = {ld: pd.Series(own[ld]["own_ret_1"].values, index=panel[ld]["ts"].values)
            for ld in leaders if ld in own}
    out = {}
    for s, df in panel.items():
        cols = {}
        for ld, series in wide.items():
            if ld == s:
                continue           # ⚠ 自分自身は own_ の担当。ここに入れると二重になる
            a = pd.Series(series.reindex(df["ts"].values).values, index=df.index)
            for k in lags:
                cols[f"{PREFIX}{_name(ld)}_ret1_lag{k}"] = a.shift(k)   # ⚠ 必ず過去へずらす
        out[s] = pd.DataFrame(cols, index=df.index)
    return out
