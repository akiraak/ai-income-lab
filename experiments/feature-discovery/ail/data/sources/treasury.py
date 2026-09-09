"""米国債の日次イールドカーブ（米財務省）。⚠ **鍵が要らない。** 米政府の著作物。

⚠ **年ごとに 1 回の要求で 1 年ぶんが返る**ので、期間が長いと要求の回数が年数になる。
⚠ **列は年限**（1 か月〜30 年）。1 列を 1 系列として `raw/treasury/series/` に置く。
"""

from __future__ import annotations

import csv
import io
import time
import urllib.request

import pandas as pd

from ail.registry import register

BASE = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
        "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
        "&field_tdr_date_value={year}&page&_format=csv")
UA = "ai-income-lab (research; contact via repository)"


def _get(url: str, timeout: float = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


@register("source", "treasury")
def fetch(series: list[str], years: list[int] | None = None, **_) -> dict[str, pd.DataFrame]:
    """`series` は年限の列名（`10 Yr` など）。`years` は取る年の一覧。"""
    frames: list[pd.DataFrame] = []
    for i, y in enumerate(years or []):
        if i:
            time.sleep(1.0)
        rows = list(csv.DictReader(io.StringIO(_get(BASE.format(year=y)))))
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        return {}
    raw = pd.concat(frames, ignore_index=True)
    date_col = next(c for c in raw.columns if c.strip().lower() == "date")
    raw["time_ms"] = pd.to_datetime(raw[date_col], utc=True).astype("int64") // 1_000_000

    out: dict[str, pd.DataFrame] = {}
    for col in series:
        if col not in raw.columns:
            continue
        df = raw[["time_ms", col]].rename(columns={col: "value"})
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna().drop_duplicates("time_ms").sort_values("time_ms").reset_index(drop=True)
        out["UST" + col.replace(" ", "").upper()] = df[["time_ms", "value"]]
    return out
