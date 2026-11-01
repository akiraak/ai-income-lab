"""登録簿 `20261025-mkt-layer`（参考なし・データ。rules.md 31 章・記録 dev-loop.md）:
63 本を束ねた **市場全体の状態の層 `mkt_`**（全銘柄に同じ値が入る列）。

列（8 本・登録簿の `structure` の写し）:
  幅      ＝ `mkt_breadth_ma20` ／ `mkt_breadth_ma60`（終値が 20 ／ 60 日移動平均より上にいる銘柄の割合）・`mkt_breadth_ma20_d5`（その 5 日差分）
  ばらつき ＝ `mkt_disp_ret_1`（同じ日の 1 日リターンの断面の標準偏差）
  等加重指数 ＝ `mkt_ew_ret_1` ／ `mkt_ew_ret_5` ／ `mkt_ew_ret_20`（同じ日の全銘柄の 1 日対数リターンの平均を 1 ／ 5 ／ 20 日足す）・`mkt_ew_vol_20`（その 20 日の標準偏差）

⚠ 時点: 足 i の値は **足 i の終値まで**で作る（`own_` ・`cs_` と同じ。ラベルは終値 i → i+1 ＝ 予測する日の前日の終値まで）。
⚠ 時刻の突き合わせは完全一致だけ（前方埋めをしない ＝ `cs_` と同じ）。銘柄が `MIN_SYMBOLS` 未満の時刻は NaN。
⚠ config の `feature_layers` に `mkt` があるときだけ組む（`build.layer_order` が `ORDER` の後ろに足す）＝ 無い config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.registry import register

PREFIX = "mkt_"
MIN_SYMBOLS = 5
COLUMNS = ("breadth_ma20", "breadth_ma60", "breadth_ma20_d5", "disp_ret_1",
           "ew_ret_1", "ew_ret_5", "ew_ret_20", "ew_vol_20")


def _wide_close(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """時刻 × 銘柄の終値。⚠ 前方埋めをしない。"""
    return pd.DataFrame({s: pd.Series(df["close"].values, index=df["ts"].values) for s, df in panel.items()}).sort_index()


def market_state(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """時刻 × 8 列（全銘柄に共通）。"""
    C = _wide_close(panel)
    # 1 日対数リターン・移動平均は **その銘柄の足の並び** で取る（休場の穴を跨いで前の足とつなぐ ＝ own_ と同じ）
    R = pd.DataFrame({s: np.log(C[s].dropna()).diff() for s in C}).reindex(C.index)
    above20 = pd.DataFrame({s: (C[s].dropna() > C[s].dropna().rolling(20).mean()).where(
        C[s].dropna().rolling(20).count() == 20) for s in C}).reindex(C.index)
    above60 = pd.DataFrame({s: (C[s].dropna() > C[s].dropna().rolling(60).mean()).where(
        C[s].dropna().rolling(60).count() == 60) for s in C}).reindex(C.index)

    n = R.notna().sum(axis=1)
    enough = n >= MIN_SYMBOLS
    ew = R.mean(axis=1).where(enough)
    out = pd.DataFrame(index=C.index)
    b20 = above20.astype(float).mean(axis=1).where(above20.notna().sum(axis=1) >= MIN_SYMBOLS)
    b60 = above60.astype(float).mean(axis=1).where(above60.notna().sum(axis=1) >= MIN_SYMBOLS)
    out["breadth_ma20"] = b20
    out["breadth_ma60"] = b60
    out["breadth_ma20_d5"] = b20 - b20.shift(5)
    out["disp_ret_1"] = R.std(axis=1).where(enough)
    out["ew_ret_1"] = ew
    out["ew_ret_5"] = ew.rolling(5).sum()
    out["ew_ret_20"] = ew.rolling(20).sum()
    out["ew_vol_20"] = ew.rolling(20).std()
    return out[list(COLUMNS)]


@register("feature", "mkt")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    M = market_state(panel)
    out = {}
    for s, df in panel.items():
        x = M.reindex(df["ts"].values)
        x.index = df.index
        out[s] = x.add_prefix(PREFIX)
    return out
