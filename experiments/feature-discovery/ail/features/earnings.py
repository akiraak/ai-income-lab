"""`ern_` ⚠ **決算の層**（SEC EDGAR。rules.md 26 章・2026-10-19）。⚠ **銘柄ごとに値が違う**（`ex_` との違い）。

⚠ **提出日の翌日からしか貼らない**（as-of ＝ 足の日 − `ern_lag_days`。既定 1・0 以下は拒む）。発表の時刻（引け後 ／ 寄り前）は使わない。
⚠ **`ern_max_stale_days`（既定 400）より古い値は欠損**（決算を出さなくなった会社を定数の列で隠さない）。
⚠ **系列の無い銘柄は 3 列とも欠損**（dropna で消える ＝ 記録に残す）。ETF には決算が無いので対象は会社だけ（26-5）。
⚠ **config の `feature_layers` に書いたときだけ表に入る**（既定の表は 1 ビットも変わらない）。

| 列 | 定義 |
| --- | --- |
| `ern_days_since_ann` | as-of の日から、最後の決算発表（8-K Item 2.02）の提出日までの暦日数（0 ＝ 前日に提出） |
| `ern_eps_yoy` | as-of の時点で最新の、希薄化後 EPS の前年同期比（対称な変化率。取得時に計算） |
| `ern_rev_yoy` | 同じく売上 |

⚠ **層 `ernf`（2026-10-22・rules.md 28 章）＝ 同じ 3 列の欠損を印の列 3 本（`ern_na_ann` ／ `ern_na_eps` ／ `ern_na_rev`。1 ＝ 無い）に写し、
値の列は埋める**（日数 ＝ `ern_max_stale_days`・前年同期比 ＝ 0）。系列の無い銘柄・欠損の期間も表に残る ＝ 対の相手（own ／ cs の行）と銘柄・行が同じになる。
⚠ `ern` と `ernf` を 1 つの config に両方書かない（`cli/build.py` が止める）。⚠ `ern` 層は 1 ビットも変わらない。
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from ail.data import store
from ail.features.exog import _daily, asof_join
from ail.registry import register

PREFIX = "ern_"
SOURCE = "edgar"
DEFAULT_LAG_DAYS = 1
DEFAULT_MAX_STALE_DAYS = 400
COLUMNS = (f"{PREFIX}days_since_ann", f"{PREFIX}eps_yoy", f"{PREFIX}rev_yoy")
KEYS = ("ANN", "EPSYOY", "REVYOY")
FLAG_COLUMNS = (f"{PREFIX}na_ann", f"{PREFIX}na_eps", f"{PREFIX}na_rev")   # ernf の印（1 ＝ 無い）。rules.md 28-1
FLAG_FILL_YOY = 0.0                                                          # 前年同期比の埋め値（分からない ＝ 変化なし）


def load_symbol_series(symbol: str) -> dict[str, pd.Series]:
    """その銘柄の 3 系列（あるものだけ）。日付 → 値（UTC の 0 時・昇順）。"""
    d = store.series_dir(SOURCE)
    out: dict[str, pd.Series] = {}
    for key in KEYS:
        sid = f"{key}_{symbol}"
        if os.path.exists(store.path_of(d, sid)):
            out[key] = _daily(sid, store.read_series(d, sid))
    return out


def build_one(bars: pd.DataFrame, series: dict[str, pd.Series], lag_days: int = DEFAULT_LAG_DAYS,
              max_stale_days: int = DEFAULT_MAX_STALE_DAYS) -> pd.DataFrame:
    """1 銘柄の足に 3 列を貼る（as-of・過去側だけ）。返り値は足と同じ行数・同じ並び。"""
    x = pd.DataFrame(np.nan, index=bars.index, columns=list(COLUMNS), dtype=float)
    if not len(bars):
        return x
    ann = series.get("ANN")
    if ann is not None and len(ann):
        asof = pd.DatetimeIndex(bars["ts"]).tz_convert("UTC").tz_localize(None).normalize() - pd.Timedelta(days=lag_days)
        days = pd.DataFrame({"d": (ann.index - pd.Timestamp("1970-01-01")).days.astype(float)}, index=ann.index)
        picked = asof_join(days, bars["ts"], lag_days, max_stale_days)["d"].to_numpy()
        now = (asof - pd.Timestamp("1970-01-01")).days.astype(float)
        x[COLUMNS[0]] = now - picked
    for key, col in (("EPSYOY", COLUMNS[1]), ("REVYOY", COLUMNS[2])):
        s = series.get(key)
        if s is not None and len(s):
            x[col] = asof_join(pd.DataFrame({"v": s.astype(float)}, index=s.index), bars["ts"], lag_days, max_stale_days)["v"].to_numpy()
    return x


def flag_missing(x: pd.DataFrame, max_stale_days: int = DEFAULT_MAX_STALE_DAYS) -> pd.DataFrame:
    """`build_one` の 3 列の欠損を **印の列 3 本（1 ＝ 無い）に写し、値の列は埋める**（層 `ernf`。rules.md 28-1）。

    埋める値: `ern_days_since_ann` ＝ `max_stale_days`（「少なくともこれだけ古い」の上限）／ 前年同期比 ＝ 0（分からない ＝ 変化なし）。
    返り値は 6 列・同じ行数・欠損なし。⚠ **`ern` 層（欠損のまま ＝ dropna で落とす）は 1 ビットも変わらない。**
    """
    out = x.copy()
    for col, flag in zip(COLUMNS, FLAG_COLUMNS):
        out[flag] = out[col].isna().astype(float)
    out[COLUMNS[0]] = out[COLUMNS[0]].fillna(float(max_stale_days))
    for col in COLUMNS[1:]:
        out[col] = out[col].fillna(FLAG_FILL_YOY)
    return out


def _layer(panel: dict[str, pd.DataFrame], ctx: dict, flag: bool) -> dict[str, pd.DataFrame]:
    lag = int(ctx.get("ern_lag_days", DEFAULT_LAG_DAYS))
    if lag < 1:
        raise ValueError(f"⚠ ern_lag_days は 1 以上でなければならない: {lag}")
    max_stale = int(ctx.get("ern_max_stale_days", DEFAULT_MAX_STALE_DAYS))
    out: dict[str, pd.DataFrame] = {}
    empty: list[str] = []
    for s, bars in panel.items():
        series = load_symbol_series(s)
        if not series:
            empty.append(s)
        x = build_one(bars, series, lag, max_stale)
        out[s] = flag_missing(x, max_stale) if flag else x
    if empty:
        how = "3 列とも印 1 で表に残る（ernf）" if flag else "3 列とも欠損 ＝ 対象なら dropna で消える"
        print(f"  ⚠ {'ernf' if flag else 'ern'}: 決算の系列が無い銘柄 {len(empty)} 本（{how}）: {' '.join(empty[:20])}", flush=True)
    return out


@register("feature", "ern")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    """欠損は欠損のまま（26-4。対象なら dropna で落ちる）。"""
    return _layer(panel, ctx, flag=False)


@register("feature", "ernf")
def layer_flag(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    """欠損を印の列にして銘柄を落とさない（28-1。2026-10-22）。列は 3 ＋ 3 ＝ 6 本。"""
    return _layer(panel, ctx, flag=True)
