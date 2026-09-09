"""FOMC の会合日程（連邦準備制度）。⚠ **値ではなく「暦」である**（発表日に行がある系列。値は 1）。

| 取得元 | 中身 | ⚠ 規約【実測 2026-09-09】 |
| --- | --- | --- |
| `www.federalreserve.gov` | 現行ページ（直近 6〜7 年）＋ 年ごとの歴史ページ | robots.txt 無し（404）。米政府の著作物 |

⚠ **予定された会合だけを取る。** 緊急会合（2020-03 の 2 回など）と notation vote は
⚠ **事前に公表されない**ので、「事前に知れる暦」の性質を壊す。除外して件数を表示する。

⚠ **発表日 = 会合の最終日**（声明は最終日の 14:00 ET）。月またぎ（`Jul/Aug 31-1`）は
2 つ目の月・2 つ目の日が最終日である。

⚠ **未来の予定も残す。** 暦の本質は「事前に公表されること」で、検証（event study）は過去分だけを使う。
"""

from __future__ import annotations

import re
import time
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from ail.registry import register

BASE = "https://www.federalreserve.gov/monetarypolicy"
UA = "ai-income-lab-research/0.1 (+https://github.com/akiraak/ai-income-lab)"

MONTHS = {m[:3].lower(): i + 1 for i, m in enumerate(
    ("January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"))}

# 現行ページ: 年見出しで区切り、月と日の div が会合ごとに並ぶ
RE_YEAR = re.compile(r">(20\d\d) FOMC Meetings<")
RE_MEETING = re.compile(
    r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<",
    re.DOTALL)
# 歴史ページ: <h5>January 30-31 Meeting - 2018</h5>（月またぎは Jul/Aug 31-1）
RE_H5 = re.compile(r"<h5[^>]*>([^<]+?)\s*Meeting\s*-\s*(20\d\d)\s*</h5>")
# ⚠ 予定された 2 日会合の形だけを通す（単日・(unscheduled)・(notation vote) は落とす）
RE_DAYS = re.compile(r"^(\d{1,2})-(\d{1,2})\*?$")


def _get(url: str, timeout: float = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def _end_date(month_field: str, days_field: str, year: int) -> datetime | None:
    """`(月, 日)` の欄から**最終日**を作る。予定された `d-d` の形でなければ None（呼び手が数える）。"""
    m = RE_DAYS.match(days_field.strip())
    if not m:
        return None
    end_month = month_field.split("/")[-1].strip()[:3].lower()
    if end_month not in MONTHS:
        return None
    return datetime(year, MONTHS[end_month], int(m.group(2)), tzinfo=timezone.utc)


def parse_current(html: str) -> tuple[list[datetime], int]:
    """現行ページ。返り値は（最終日の一覧, 落とした件数）。"""
    out, skipped = [], 0
    marks = list(RE_YEAR.finditer(html))
    for i, mark in enumerate(marks):
        year = int(mark.group(1))
        chunk = html[mark.end(): marks[i + 1].start() if i + 1 < len(marks) else len(html)]
        for month_field, days_field in RE_MEETING.findall(chunk):
            d = _end_date(month_field, days_field, year)
            out.append(d) if d else None
            skipped += 0 if d else 1
    return out, skipped


def parse_historical(html: str) -> tuple[list[datetime], int]:
    """歴史ページ（`fomchistorical<年>.htm`）。"""
    out, skipped = [], 0
    for heading, year in RE_H5.findall(html):
        parts = heading.strip().rsplit(" ", 1)
        d = _end_date(parts[0], parts[1], int(year)) if len(parts) == 2 else None
        out.append(d) if d else None
        skipped += 0 if d else 1
    return out, skipped


@register("source", "fomc")
def fetch(series: list[str], start: str | None = None, end: str | None = None, **_) -> dict[str, pd.DataFrame]:
    """`series` は `["decision"]`。現行ページに無い過去年は歴史ページを 1 秒間隔で足す。"""
    html = _get(f"{BASE}/fomccalendars.htm")
    dates, skipped = parse_current(html)
    have_years = {d.year for d in dates}
    if not have_years:
        raise SystemExit("⚠ 現行ページから会合が 1 件も読めない（ページの形が変わった）")

    first_year = int((start or "2018-01-01")[:4])
    for year in range(first_year, min(have_years)):
        time.sleep(1.0)
        got, sk = parse_historical(_get(f"{BASE}/fomchistorical{year}.htm"))
        dates += got
        skipped += sk

    lo = pd.Timestamp(start or "1900-01-01", tz="UTC")
    hi = pd.Timestamp(end or "2999-12-31", tz="UTC")
    kept = sorted(d for d in dates if lo <= pd.Timestamp(d) <= hi)
    if skipped:
        print(f"  ⚠ 予定された 2 日会合の形でないものを {skipped} 件落とした（緊急会合・notation vote）")

    df = pd.DataFrame({"time_ms": [int(d.timestamp() * 1000) for d in kept],
                       "value": 1.0}).drop_duplicates("time_ms").reset_index(drop=True)
    return {"FOMC_DECISION": df} if "decision" in series else {}
