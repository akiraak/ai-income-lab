"""地震（USGS）。⚠ **鍵が要らない。** 米政府の著作物。

⚠ **「偽薬（プラセボ）」の枠。** 値動きと因果は想定していない。

⚠ **1 件 1 行で返るので、日ごとの件数と最大マグニチュードに畳んでから置く。**
⚠ **要求が重いので、期間を年で割って取る。**
"""

from __future__ import annotations

import json
import time
import urllib.request

import pandas as pd

from ail.registry import register

BASE = ("https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
        "&starttime={start}&endtime={end}&minmagnitude={mag}")
UA = "ai-income-lab (research; contact via repository)"


def _get(url: str, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


@register("source", "usgs")
def fetch(series: list[str], start: str = "2018-01-01", end: str = "2026-09-01",
          minmagnitude: float = 5.0, **_) -> dict[str, pd.DataFrame]:
    """`series` は `count`（日ごとの件数）と `maxmag`（その日の最大 M）。"""
    years = range(int(start[:4]), int(end[:4]) + 1)
    events = []
    for i, y in enumerate(years):
        if i:
            time.sleep(1.0)
        a = max(f"{y}-01-01", start)
        b = min(f"{y}-12-31", end)
        if a > b:
            continue
        doc = _get(BASE.format(start=a, end=b, mag=minmagnitude))
        for f in doc.get("features", []):
            p = f.get("properties") or {}
            if p.get("time") is not None:
                events.append({"ms": int(p["time"]), "mag": p.get("mag")})
    if not events:
        return {}
    ev = pd.DataFrame(events)
    # ⚠ **その日の 00:00 UTC に丸める**（時刻の意味を「日」に揃える）
    ev["day_ms"] = (ev["ms"] // 86_400_000) * 86_400_000
    g = ev.groupby("day_ms")
    out: dict[str, pd.DataFrame] = {}
    if "count" in series:
        d = g.size().reset_index(name="value").rename(columns={"day_ms": "time_ms"})
        out["EQ_COUNT"] = d.sort_values("time_ms").reset_index(drop=True)
    if "maxmag" in series:
        d = g["mag"].max().reset_index(name="value").rename(columns={"day_ms": "time_ms"})
        out["EQ_MAXMAG"] = d.dropna().sort_values("time_ms").reset_index(drop=True)
    return out
