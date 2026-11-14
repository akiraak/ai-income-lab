"""登録簿 `20261112-wiki-pageview-layer`（参考あり・枠の外。記録 dev-loop.md）:
会社の Wikipedia の記事の日次の閲覧数から作る「注目」の層 `attn_`。欠損を印にして埋める ＝ 銘柄を落とさない（決算の層 `ernf` と同じ形）。

列（2 本 ＋ 印 1 本）:
  `attn_z60`    … 閲覧数の対数 log(1 ＋ 閲覧数) の z スコア（その日より前の 60 暦日の平均と標準偏差に対して。± 10 で切る）
  `attn_r5_20`  … 直近 5 暦日の平均 ÷ 直近 20 暦日の平均（その日を含む）
  `attn_na`     … 印（1 ＝ その足の時点で値を作れない ＝ 系列が無い ／ 過去の日数が足りない ／ 標準偏差 0）。値の列は z ＝ 0 ・ 比 ＝ 1 で埋める
⚠ 時点: 足の日 − `attn_lag_days`（既定 1・0 以下は拒む）の暦日までの閲覧数だけ（その日の閲覧数は翌日以降の足にしか貼らない）。
   閲覧数は暦日（土日も）で数える。系列に無い日は 0 ではなく欠け（その日の値を作らない）。
⚠ 取得元は `ail/data/sources/wikimedia.py`（ここで import して登録する ＝ `ail/bootstrap.py` は触らない）。
⚠ config の `feature_layers` に `attn` があるときだけ組む（`build.layer_order` が後ろに足す）＝ 無い config は 1 ビットも変わらない。
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from ail.data import store
from ail.data.sources import wikimedia  # noqa: F401  （取得元 `wikimedia` を登録する）
from ail.features.exog import _daily
from ail.registry import register

PREFIX = "attn_"
SOURCE = "wikimedia"
DEFAULT_LAG_DAYS = 1
Z_WINDOW = 60          # ⚠ 事前固定（登録簿「過去 60 日に対して」）
SHORT, LONG = 5, 20    # ⚠ 事前固定（登録簿「5 日と 20 日の平均の比」）
Z_CLIP = 10.0
R_FILL = 1.0           # 比の埋め値（分からない ＝ 変化なし）
COLUMNS = ("z60", "r5_20", "na")


def load_symbol_series(symbol: str) -> pd.Series | None:
    """その銘柄の閲覧数（暦日 → 閲覧数・UTC の 0 時・昇順）。無ければ None。"""
    d = store.series_dir(SOURCE)
    sid = f"PV_{symbol}"
    if not os.path.exists(store.path_of(d, sid)):
        return None
    return _daily(sid, store.read_series(d, sid))


def daily_features(views: pd.Series) -> pd.DataFrame:
    """暦日の閲覧数 → 暦日ごとの (z60, r5_20)。⚠ どちらも その日までの値だけ（z の平均と標準偏差は その日より前の 60 日）。"""
    idx = pd.date_range(views.index.min(), views.index.max(), freq="D")
    v = views.reindex(idx)                                     # 無い日は欠け
    lv = np.log1p(v)
    prev = lv.shift(1)
    mu = prev.rolling(Z_WINDOW, min_periods=Z_WINDOW).mean()
    sd = prev.rolling(Z_WINDOW, min_periods=Z_WINDOW).std()
    z = ((lv - mu) / sd.where(sd > 0)).clip(-Z_CLIP, Z_CLIP)
    short = v.rolling(SHORT, min_periods=SHORT).mean()
    long = v.rolling(LONG, min_periods=LONG).mean()
    r = short / long.where(long > 0)
    return pd.DataFrame({"z60": z, "r5_20": r}, index=idx)


def build_one(bars: pd.DataFrame, views: pd.Series | None, lag_days: int = DEFAULT_LAG_DAYS) -> pd.DataFrame:
    """1 銘柄の足に 3 列を貼る（過去側だけ・欠損なし）。返り値は足と同じ行数・同じ並び。"""
    x = pd.DataFrame({"z60": 0.0, "r5_20": R_FILL, "na": 1.0}, index=bars.index)
    if not len(bars) or views is None or not len(views):
        return x
    asof = pd.DatetimeIndex(bars["ts"]).tz_convert("UTC").tz_localize(None).normalize() - pd.Timedelta(days=lag_days)
    f = daily_features(views).reindex(asof)
    ok = f["z60"].notna().to_numpy() & f["r5_20"].notna().to_numpy()
    x["z60"] = np.where(ok, f["z60"].to_numpy(), 0.0)
    x["r5_20"] = np.where(ok, f["r5_20"].to_numpy(), R_FILL)
    x["na"] = (~ok).astype(float)
    return x[list(COLUMNS)]


@register("feature", "attn")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    lag = int(ctx.get("attn_lag_days", DEFAULT_LAG_DAYS))
    if lag < 1:
        raise ValueError(f"⚠ attn_lag_days は 1 以上でなければならない: {lag}")
    out: dict[str, pd.DataFrame] = {}
    empty: list[str] = []
    for s, bars in panel.items():
        views = load_symbol_series(s)
        if views is None:
            empty.append(s)
        out[s] = build_one(bars, views, lag).add_prefix(PREFIX)
    if empty:
        print(f"  ⚠ attn: 閲覧数の系列が無い銘柄 {len(empty)} 本（印 1 で表に残る）: {' '.join(empty[:20])}", flush=True)
    return out
