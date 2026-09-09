"""経済政策の不確実性指数（Baker-Bloom-Davis の日次 EPU）。⚠ **鍵が要らない。**

⚠ **枠は「本命」。** 仮説は「⚠ **政策の不確実性が上がると、企業が投資を控えて株が下がる**」
（Baker, Bloom, Davis 2016 の主張）。

⚠ **全銘柄で同じ値になる**ので、[§9](../../../../../docs/specs/experiments/daily-data-sources.md) の限界を
そのまま引き継ぐ。⚠ **市場全体の方向にしか効きようがない。**

⚠ **robots.txt は無い**（404）。⚠ **学術データで、出典表示の要請がある。**
"""

from __future__ import annotations

import csv
import io
import urllib.request

import pandas as pd

from ail.registry import register

URL = "https://www.policyuncertainty.com/media/All_Daily_Policy_Data.csv"
UA = "ai-income-lab (research; contact via repository)"


@register("source", "epu")
def fetch(series: list[str], **_) -> dict[str, pd.DataFrame]:
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    body = urllib.request.urlopen(req, timeout=90).read().decode("utf-8", "replace")
    rows = [r for r in csv.DictReader(io.StringIO(body)) if (r.get("year") or "").strip()]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(dict(year=df["year"].astype(int), month=df["month"].astype(int),
                                     day=df["day"].astype(int)), errors="coerce")
    df["value"] = pd.to_numeric(df["daily_policy_index"], errors="coerce")
    df = df.dropna(subset=["date", "value"])
    df["time_ms"] = df["date"].dt.tz_localize("UTC").astype("int64") // 1_000_000
    return {"EPU_DAILY": df[["time_ms", "value"]].sort_values("time_ms").reset_index(drop=True)}
