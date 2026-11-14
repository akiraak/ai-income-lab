"""登録簿 `20261108-edgar-8k-event-flags`（参考あり・枠の内。記録 dev-loop.md）:
決算以外も含む臨時の開示（SEC の 8-K）の印の層 `evt_`。欠損を印にして埋める ＝ 銘柄を落とさない（決算の層 `ernf` と同じ形）。

列（5 本 ＋ 印 1 本）:
  `evt_8k_d1`         … 前の足より後に使えるようになった 8-K があるか（0 ／ 1）
  `evt_8k_502_d1`     … 同じく、項目 5.02（役員の異動）を含む 8-K
  `evt_8k_202_d1`     … 同じく、項目 2.02（決算の発表）を含む 8-K
  `evt_8k_cnt20`      … 直近 20 本の足の間に使えるようになった 8-K の件数
  `evt_days_since_8k` … いちばん新しい 8-K からの日数（暦日。無ければ 400 で埋める ＝ 上限も 400）
  `evt_na_8k`         … 印（1 ＝ その足までに 8-K が 1 件も無い ／ 系列が無い）
⚠ 時点: 足の日 − `evt_lag_days`（既定 1・0 以下は拒む）までに **提出された** 8-K だけ（提出日。翌日以降にしか貼らない）。
   「前の足より後に使えるようになった」＝ 提出日が（前の足の日 − 遅れ, この足の日 − 遅れ］＝ 金〜日の提出は月曜の足に出る。
⚠ 取得元は `ail/data/sources/edgar_8k.py`（ここで import して登録する ＝ `ail/bootstrap.py` は触らない）。
⚠ config の `feature_layers` に `evt` があるときだけ組む（`build.layer_order` が後ろに足す）＝ 無い config は 1 ビットも変わらない。
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from ail.data import store
from ail.data.sources import edgar_8k  # noqa: F401  （取得元 `edgar_8k` を登録する）
from ail.features.exog import _daily
from ail.registry import register

PREFIX = "evt_"
SOURCE = "edgar_8k"
DEFAULT_LAG_DAYS = 1
MAX_DAYS = 400.0       # ⚠ 事前固定: 日数の上限と、無いときの埋め値（決算の層の `ern_max_stale_days` と同じ）
COUNT_BARS = 20        # ⚠ 事前固定（登録簿「直近 20 日の件数」）
COLUMNS = ("8k_d1", "8k_502_d1", "8k_202_d1", "8k_cnt20", "days_since_8k", "na_8k")
KEYS = {"K8": "8k_d1", "K8I502": "8k_502_d1", "K8I202": "8k_202_d1"}


def load_symbol_series(symbol: str) -> dict[str, pd.Series]:
    """その銘柄の 3 系列（あるものだけ）。提出日 → 件数（UTC の 0 時・昇順）。"""
    d = store.series_dir(SOURCE)
    out: dict[str, pd.Series] = {}
    for key in KEYS:
        sid = f"{key}_{symbol}"
        if os.path.exists(store.path_of(d, sid)):
            out[key] = _daily(sid, store.read_series(d, sid))
    return out


def _cum_at(s: pd.Series | None, asof: pd.DatetimeIndex) -> np.ndarray:
    """asof の各日までの件数の累計（その日を含む）。系列が無ければ 0。"""
    if s is None or not len(s):
        return np.zeros(len(asof))
    idx = pd.DatetimeIndex(s.index).tz_localize(None) if getattr(s.index, "tz", None) is not None else pd.DatetimeIndex(s.index)
    cum = np.cumsum(s.to_numpy(dtype=float))
    pos = np.searchsorted(idx.values, asof.values, side="right")
    return np.where(pos > 0, cum[np.maximum(pos - 1, 0)], 0.0)


def build_one(bars: pd.DataFrame, series: dict[str, pd.Series], lag_days: int = DEFAULT_LAG_DAYS) -> pd.DataFrame:
    """1 銘柄の足に 6 列を貼る（過去側だけ・欠損なし）。返り値は足と同じ行数・同じ並び。"""
    x = pd.DataFrame(0.0, index=bars.index, columns=list(COLUMNS), dtype=float)
    if not len(bars):
        return x
    asof = pd.DatetimeIndex(bars["ts"]).tz_convert("UTC").tz_localize(None).normalize() - pd.Timedelta(days=lag_days)
    for key, col in KEYS.items():
        c = _cum_at(series.get(key), asof)
        x[col] = (np.diff(c, prepend=c[0]) > 0).astype(float)        # ⚠ 最初の足は「前の足」が無い ＝ 0
        if key == "K8":
            x["8k_cnt20"] = c - np.r_[np.full(min(COUNT_BARS, len(c)), c[0]), c[:-COUNT_BARS]][:len(c)]
    all8 = series.get("K8")
    last = np.full(len(asof), np.datetime64("NaT", "ns"))
    if all8 is not None and len(all8):
        idx = pd.DatetimeIndex(all8.index).tz_localize(None) if getattr(all8.index, "tz", None) is not None else pd.DatetimeIndex(all8.index)
        pos = np.searchsorted(idx.values, asof.values, side="right")
        last = np.where(pos > 0, idx.values[np.maximum(pos - 1, 0)], np.datetime64("NaT", "ns"))
    days = (asof.values - last) / np.timedelta64(1, "D")
    none = ~np.isfinite(days)
    x["na_8k"] = none.astype(float)
    x["days_since_8k"] = np.where(none, MAX_DAYS, np.minimum(days, MAX_DAYS))
    return x


@register("feature", "evt")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    lag = int(ctx.get("evt_lag_days", DEFAULT_LAG_DAYS))
    if lag < 1:
        raise ValueError(f"⚠ evt_lag_days は 1 以上でなければならない: {lag}")
    out: dict[str, pd.DataFrame] = {}
    empty: list[str] = []
    for s, bars in panel.items():
        series = load_symbol_series(s)
        if not series:
            empty.append(s)
        out[s] = build_one(bars, series, lag).add_prefix(PREFIX)
    if empty:
        print(f"  ⚠ evt: 8-K の系列が無い銘柄 {len(empty)} 本（印 1 で表に残る）: {' '.join(empty[:20])}", flush=True)
    return out
