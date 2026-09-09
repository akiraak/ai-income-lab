"""`ex_` ⚠ **外部の系列**（為替・金利・気象・地震）。⚠ **価格の外から来る最初の層である。**

⚠ **`own_` `cs_` `rel_` `ll_` は全部、同じ価格の足から作っている。** この層だけが情報源が違う。

⚠ **扱いが 3 点で他の層と違う。**

| # | 違い | ⚠ 理由 |
| ---: | --- | --- |
| 1 | ⚠ **必ず 1 日以上ずらす** | ⚠ **「その日のデータ」が「その日に手に入る」とは限らない**（発表の遅れ） |
| 2 | ⚠ **欠けた日の埋め方が系列で違う** | ⚠ **地震は「行が無い日 = 0 件の日」**。為替・金利は休場なので前の値が最新のまま |
| 2-2 | ⚠ **前の値を引き継ぐ日数に上限を置く** | ⚠ **上限が無いと、系列が止まっても古い値が永久に貼られ、定数の特徴量になる** |
| 3 | ⚠ **全銘柄で同じ値になる** | ⚠ **断面では銘柄を区別できない**（方向には効きうるが、相対の順位には効かない） |

⚠ **前の値を使うのは先読みではない。** 「その時点で公表されている最新の値」であり、未来は入らない。
⚠ **ただし地震だけは前の値を引きずってはいけない**（起きなかった日に前日の件数が入る）。

> ⚠ **偽薬（プラセボ）の枠を持つ。** 気象と地震は値動きと因果を想定していない。
> ⚠ **選別手法がそれを選んだ割合が、そのまま偽発見率の実測になる。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.data import store
from ail.registry import register

PREFIX = "ex_"
DEFAULT_SOURCES = ("ecb", "treasury", "noaa", "usgs")
DEFAULT_LAG_DAYS = 1
# ⚠ **行が無い日を 0 とみなす取得元**（地震は「起きなかった日」で、値が不明な日ではない）
DEFAULT_ZERO_FILL = ("usgs",)
DEFAULT_TRANSFORMS = ("d1", "z20")
# ⚠ **前の値を引き継いでよい日数の上限。** これを超えたら欠損にする。
# ⚠ **上限が無いと、系列が止まっても古い値が永久に貼られ続け、定数の特徴量になる**
# （2026-09-09 に踏んだ。気象の取得が 09-01 で止まっていて、09-02 以降に同じ値が並んだ）
DEFAULT_MAX_STALE_DAYS = 7


def _daily(sid: str, df: pd.DataFrame) -> pd.Series:
    """1 系列を「日付 → 値」にする。⚠ **日付は UTC の 0 時に正規化する**（足と揃える）。"""
    idx = pd.DatetimeIndex(df["ts"]).tz_convert("UTC").tz_localize(None).normalize()
    return pd.Series(df["value"].astype(float).values, index=idx, name=sid).sort_index()


def load_series(sources=DEFAULT_SOURCES, zero_fill=DEFAULT_ZERO_FILL) -> dict[str, pd.Series]:
    """`raw/<取得元>/series/` を読む。⚠ **0 埋めする取得元だけ、暦の全日に広げてから埋める。**"""
    out: dict[str, pd.Series] = {}
    for src in sources:
        d = store.series_dir(src)
        ids = store.symbols_in(d)
        if not ids:
            continue
        zero = src in tuple(zero_fill)
        for sid in ids:
            s = _daily(sid, store.read_series(d, sid))
            if zero:
                # ⚠ **行が無い日は 0 件。** ⚠ **前の値を引きずると、起きなかった日に前日の値が入る**
                s = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D")).fillna(0.0)
            out[sid] = s
    return out


def transform(s: pd.Series, kinds=DEFAULT_TRANSFORMS) -> pd.DataFrame:
    """系列そのものではなく、⚠ **定常に近い形に直したもの**を特徴量にする。

    ⚠ **水準（`lvl`）は既定に入れない。** 為替や金利の水準は非定常で、
    ⚠ **訓練期間の水準をそのまま検証期間に当てると意味が変わる。**
    """
    x = pd.DataFrame(index=s.index)
    for k in kinds:
        if k == "lvl":
            x["lvl"] = s
        elif k == "d1":
            x["d1"] = s.diff()                       # 前日からの変化
        elif k.startswith("z"):
            w = int(k[1:])
            m, sd = s.rolling(w).mean(), s.rolling(w).std()
            x[k] = (s - m) / sd.replace(0, np.nan)   # ⚠ 窓は当日で閉じる
        elif k.startswith("r"):
            w = int(k[1:])
            x[k] = s.pct_change(w)
    return x


@register("feature", "ex")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    """⚠ **銘柄ごとの足に、`lag_days` だけ前の外部系列を貼る**（as-of。過去側だけを見る）。"""
    lag = int(ctx.get("ex_lag_days", DEFAULT_LAG_DAYS))
    if lag < 1:
        # ⚠ **0 以下は許さない。** 発表の遅れを無視すると、その日のうちに知り得ない値を使う
        raise ValueError(f"⚠ ex_lag_days は 1 以上でなければならない: {lag}")
    sources = tuple(ctx.get("ex_sources", DEFAULT_SOURCES))
    zero_fill = tuple(ctx.get("ex_zero_fill", DEFAULT_ZERO_FILL))
    kinds = tuple(ctx.get("ex_transforms", DEFAULT_TRANSFORMS))
    max_stale = int(ctx.get("ex_max_stale_days", DEFAULT_MAX_STALE_DAYS))

    series = load_series(sources, zero_fill)
    if not series:
        raise SystemExit("⚠ 外部系列が 1 本も無い。先に `python3 -m cli.fetch --exog exog_daily` を回す")

    # 系列ごとに変換してから 1 枚の表に束ねる（⚠ **変換は系列自身の暦で行う**）
    wide = pd.concat({sid: transform(s, kinds) for sid, s in series.items()}, axis=1)
    wide.columns = [f"{PREFIX}{sid}_{k}" for sid, k in wide.columns]
    wide = wide.sort_index()

    out: dict[str, pd.DataFrame] = {}
    for sym, bars in panel.items():
        idx = pd.DatetimeIndex(bars["ts"]).tz_convert("UTC").tz_localize(None).normalize()
        # ⚠ **as-of（backward）**: 足の日から lag 日前の時点で、⚠ **公表されている最新の値**を取る。
        # ⚠ **未来は構造的に入らない**（`asof` は指定日以前しか見ない）
        asof = idx - pd.Timedelta(days=lag)
        union = wide.index.union(asof)
        picked = wide.reindex(union).ffill().reindex(asof)
        # ⚠ **引き継いだ値が古すぎたら欠損にする。** ⚠ **系列が止まったことを、定数の列で隠さない**
        age = pd.Series(union, index=union).where(wide.notna().any(axis=1).reindex(union, fill_value=False))
        last_seen = age.ffill().reindex(asof)
        stale = (pd.Series(asof, index=asof) - last_seen).dt.days > max_stale
        picked[stale.values] = np.nan
        picked.index = bars.index
        out[sym] = picked
    return out
