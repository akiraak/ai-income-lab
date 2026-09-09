"""`rel_` 市場・セクターに対する相対。⚠ **「上がったか」ではなく「市場より上がったか」を見る。**

⚠ **63 系列のうち 15 が ETF で、SPY は残り 48 社を丸ごと含む**（rules.md 12 章）。
だから素のリターンは大半が「市場が動いたぶん」で、⚠ **銘柄固有の情報は残差にしか無い。**

⚠ **β は過去の窓だけで測る**（足 i までの w 本）。全期間で測ると先読みになる。
⚠ **同じ足の市場リターンを引くのは先読みではない**（足 i の終値時点で市場の終値も確定している）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.registry import register

PREFIX = "rel_"
BETA_WINDOW = 60


def _aligned(series: pd.Series, ts: pd.Series) -> pd.Series:
    """時刻の完全一致で揃える。⚠ **前方埋めをしない。**"""
    return pd.Series(series.reindex(ts.values).values, index=ts.index)


def _resid(own_r: pd.Series, mkt_r: pd.Series, w: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """転がり β と残差リターン。窓は足 i で閉じる。"""
    cov = own_r.rolling(w).cov(mkt_r)
    var = mkt_r.rolling(w).var()
    beta = cov / var.replace(0, np.nan)
    corr = own_r.rolling(w).corr(mkt_r)
    return beta, corr, own_r - beta * mkt_r


@register("feature", "rel")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    own = ctx["own"]
    market = ctx.get("market", "SPY")
    sector_of: dict[str, str] = ctx.get("sector_of", {})
    w = int(ctx.get("beta_window", BETA_WINDOW))
    out = {s: pd.DataFrame(index=df.index) for s, df in panel.items()}
    if market not in panel:
        return out

    mkt = pd.Series(own[market]["own_ret_1"].values, index=panel[market]["ts"].values)
    sec = {e: pd.Series(own[e]["own_ret_1"].values, index=panel[e]["ts"].values)
           for e in set(sector_of.values()) if e in panel}

    for s, df in panel.items():
        o, ts = out[s], df["ts"]
        r = own[s]["own_ret_1"]
        m = _aligned(mkt, ts)
        beta, corr, resid = _resid(r, m, w)
        o[f"{PREFIX}mkt_ret_1"] = m
        o[f"{PREFIX}beta_{w}"] = beta
        o[f"{PREFIX}corr_{w}"] = corr
        o[f"{PREFIX}resid_1"] = resid
        for k in (5, 20):
            o[f"{PREFIX}resid_{k}"] = resid.rolling(k).sum()
        # 素の相対強弱（β を使わない版。⚠ β の推定誤差に依らない対照として置く）
        for k in (1, 5, 20):
            o[f"{PREFIX}excess_{k}"] = own[s][f"own_ret_{k}"] - m.rolling(k).sum()

        etf = sector_of.get(s)
        if etf and etf in sec and etf != s:
            sm = _aligned(sec[etf], ts)
            sbeta, scorr, sresid = _resid(r, sm, w)
            o[f"{PREFIX}sec_ret_1"] = sm
            o[f"{PREFIX}sec_beta_{w}"] = sbeta
            o[f"{PREFIX}sec_resid_1"] = sresid
            o[f"{PREFIX}sec_resid_20"] = sresid.rolling(20).sum()
    return out
