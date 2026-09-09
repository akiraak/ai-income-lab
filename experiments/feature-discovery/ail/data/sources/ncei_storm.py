"""激しい気象の事象（NOAA NCEI Storm Events）。⚠ **鍵が要らない。** 米政府の著作物。

⚠ **枠は「本命」。** 仮説は「⚠ **災害は保険の支払いと操業停止を通じて業績に効く**」。
⚠ **[偽薬（気象・地震）とは別枠である](../../../../../docs/specs/experiments/daily-data-sources.md)。**

⚠ **SPC（`www.spc.noaa.gov`）からは取らない。** ⚠ **`robots.txt` が `Disallow: /` で全自動アクセスを拒否**
しているため（Stooq と同じ扱い）。⚠ **NCEI は同じ事象の公式の保管庫で、`/pub/` は禁止に含まれない。**

⚠ **地域と種類を残して集計する。** ⚠ **全銘柄で同じ値にすると、[§9](../../../../../docs/specs/experiments/daily-data-sources.md) と
同じ結果になる**（市場全体の方向にしか効かない）。⚠ **銘柄への割り当ては次の層の仕事。**
"""

from __future__ import annotations

import csv
import gzip
import io
import re
import time
import urllib.request

import pandas as pd

from ail.data import regions
from ail.registry import register

BASE = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
UA = "ai-income-lab (research; contact via repository)"

# ⚠ **被害額は「1.00K」「75.00M」の書式。** 数に直さないと足せない
_MULT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}

# 種類をまとめる（⚠ **細かすぎると系列が増えて多重検定が厳しくなる**）
CATEGORIES = {
    "竜巻": ("Tornado",),
    "雹": ("Hail",),
    "洪水": ("Flash Flood", "Flood", "Coastal Flood"),
    "山火事": ("Wildfire",),
    "冬季": ("Winter Storm", "Winter Weather", "Blizzard", "Ice Storm", "Heavy Snow"),
    "熱": ("Heat", "Excessive Heat"),
}
# ⚠ **業種に効きそうな地域でまとめる**（割り当ての土台。次の層で銘柄に結び付ける）
# ⚠ **地域の定義は `ail/data/regions.py` が正本。** ⚠ **IEM の警報と同じ州でなければ割り当てが噛み合わない**
REGIONS = {name: regions.full_names(name) for name in regions.REGIONS}


def _damage(v: str) -> float:
    """`75.00M` → 75,000,000。⚠ **空欄は 0**（被害が無かった、または記録が無い）。"""
    v = (v or "").strip().upper()
    if not v:
        return 0.0
    m = re.fullmatch(r"([0-9.]+)\s*([KMBT]?)", v)
    if not m:
        return 0.0
    try:
        return float(m.group(1)) * _MULT.get(m.group(2), 1.0)
    except ValueError:
        return 0.0


def _get(url: str, timeout: float = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _events(years: list[int]) -> pd.DataFrame:
    listing = _get(BASE, timeout=90).decode("utf-8", "replace")
    files = {y: f for f, y in sorted(set(
        re.findall(r"(StormEvents_details-ftp_v1\.0_d(\d{4})_c\d+\.csv\.gz)", listing)))}
    rows = []
    for i, y in enumerate(years):
        key = str(y)
        if key not in files:
            continue
        if i:
            time.sleep(1.0)
        raw = gzip.decompress(_get(BASE + files[key])).decode("utf-8", "replace")
        for r in csv.DictReader(io.StringIO(raw)):
            ym, day = r.get("BEGIN_YEARMONTH"), r.get("BEGIN_DAY")
            if not (ym and day):
                continue
            rows.append({"date": f"{ym[:4]}-{ym[4:6]}-{int(day):02d}",
                         "type": (r.get("EVENT_TYPE") or "").strip(),
                         "state": (r.get("STATE") or "").strip().upper(),
                         "damage": _damage(r.get("DAMAGE_PROPERTY", "")),
                         "deaths": float(r.get("DEATHS_DIRECT") or 0)})
        print(f"    {y}: 累計 {len(rows):,} 件", flush=True)
    df = pd.DataFrame(rows)
    df["time_ms"] = pd.to_datetime(df["date"], utc=True, errors="coerce").astype("int64") // 1_000_000
    return df.dropna(subset=["time_ms"])


def _series(g: pd.Series) -> pd.DataFrame:
    d = g.reset_index()
    d.columns = ["time_ms", "value"]
    return d.sort_values("time_ms").reset_index(drop=True)


@register("source", "ncei_storm")
def fetch(series: list[str], years: list[int] | None = None, **_) -> dict[str, pd.DataFrame]:
    """`series` は `all`（全国）／`category`（種類別）／`region`（地域別の被害額）。"""
    ev = _events(list(years or []))
    if ev.empty:
        return {}
    out: dict[str, pd.DataFrame] = {}
    if "all" in series:
        out["SE_COUNT"] = _series(ev.groupby("time_ms").size())
        out["SE_DAMAGE"] = _series(ev.groupby("time_ms")["damage"].sum())
        out["SE_DEATHS"] = _series(ev.groupby("time_ms")["deaths"].sum())
    if "category" in series:
        for name, types in CATEGORIES.items():
            sub = ev[ev["type"].isin(types)]
            if len(sub):
                out[f"SE_CNT_{name}"] = _series(sub.groupby("time_ms").size())
    if "region" in series:
        for name, states in REGIONS.items():
            sub = ev[ev["state"].isin(states)]
            if len(sub):
                out[f"SE_DMG_{name}"] = _series(sub.groupby("time_ms")["damage"].sum())
    return out
