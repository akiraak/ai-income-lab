"""為替（欧州中央銀行の参照相場）。⚠ **鍵が要らない。** 日次・1999 年から。

⚠ **参照相場であって取引の終値ではない。** 中央欧州時間の 14:15 ごろに 1 日 1 回決まる値で、
⚠ **日中の値動きは入っていない。**

⚠ **ユーロを軸にした相場**なので、USD/JPY のような組は 2 本から作る（`EUR/USD` と `EUR/JPY`）。

規約: [daily-data-sources.md §2](../../../../../docs/specs/experiments/daily-data-sources.md)。
⚠ **FRED の `DEX*` を使わないのは、FRED の規約が「抽出をするな」と書いているため**（§2-1）。
"""

from __future__ import annotations

import csv
import io
import time
import urllib.request

import pandas as pd

from ail.registry import register

BASE = "https://data-api.ecb.europa.eu/service/data/EXR"
UA = "ai-income-lab (research; contact via repository)"


def _get(url: str, timeout: float = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


@register("source", "ecb")
def fetch(series: list[str], **_) -> dict[str, pd.DataFrame]:
    """`series` は通貨コード（`JPY` `USD` …）。返すのは EUR 建ての 1 通貨あたりのレート。"""
    out: dict[str, pd.DataFrame] = {}
    for i, cur in enumerate(series):
        if i:
            time.sleep(1.0)                   # ⚠ 相手の負荷を上げない
        key = f"D.{cur}.EUR.SP00.A"           # 日次・対ユーロ・参照相場
        body = _get(f"{BASE}/{key}?format=csvdata&detail=dataonly")
        rows = list(csv.DictReader(io.StringIO(body)))
        if not rows:
            continue
        df = pd.DataFrame({"date": [r["TIME_PERIOD"] for r in rows],
                           "value": [r["OBS_VALUE"] for r in rows]})
        df = df[df["value"].astype(str).str.strip() != ""]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna()
        df["time_ms"] = (pd.to_datetime(df["date"], utc=True).astype("int64") // 1_000_000)
        out[f"EURTO{cur}"] = df[["time_ms", "value"]].sort_values("time_ms").reset_index(drop=True)
    return out
