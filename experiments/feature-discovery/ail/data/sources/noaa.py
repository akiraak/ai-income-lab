"""気象（NOAA NCEI の GHCN-Daily）。⚠ **鍵が要らない。** 米政府の著作物で制限が無い。

⚠ **Open-Meteo を採らなかったのは CC BY-NC（非商用のみ）だから**
（[daily-data-sources.md §2-2](../../../../../docs/specs/experiments/daily-data-sources.md)）。

⚠ **これは「偽薬（プラセボ）」の枠である。** 値動きと因果は想定していない。
⚠ **選別手法がこれを選ぶ割合が、そのまま偽発見率の実測になる。**

⚠ **値は 10 分の 1 度・10 分の 1 mm 単位**（GHCN-Daily の仕様）。ここでは生の整数のまま置く。
"""

from __future__ import annotations

import csv
import io
import time
import urllib.request

import pandas as pd

from ail.registry import register

BASE = ("https://www.ncei.noaa.gov/access/services/data/v1?dataset=daily-summaries"
        "&stations={station}&startDate={start}&endDate={end}&dataTypes={types}&format=csv")
UA = "ai-income-lab (research; contact via repository)"


def _get(url: str, timeout: float = 90) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


@register("source", "noaa")
def fetch(series: list[str], stations: list[str] | None = None,
          start: str = "2018-01-01", end: str = "2026-09-01", **_) -> dict[str, pd.DataFrame]:
    """`series` は要素（`TMAX` `TMIN` `PRCP`）。`stations` は観測所 ID。"""
    out: dict[str, pd.DataFrame] = {}
    for i, st in enumerate(stations or []):
        if i:
            time.sleep(1.0)
        body = _get(BASE.format(station=st, start=start, end=end, types=",".join(series)))
        rows = list(csv.DictReader(io.StringIO(body)))
        if not rows:
            continue
        raw = pd.DataFrame(rows)
        raw["time_ms"] = pd.to_datetime(raw["DATE"], utc=True).astype("int64") // 1_000_000
        for col in series:
            if col not in raw.columns:
                continue
            df = raw[["time_ms", col]].rename(columns={col: "value"})
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.dropna().drop_duplicates("time_ms").sort_values("time_ms").reset_index(drop=True)
            out[f"WX_{st}_{col}"] = df[["time_ms", "value"]]
    return out
