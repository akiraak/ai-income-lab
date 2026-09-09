"""NWS の警報の保管庫（Iowa Environmental Mesonet）。⚠ **鍵が要らない。** 元データは米政府の公有。

⚠ **枠は「本命」。** 仮説は「⚠ **警報は被害より先に出るので、災害の影響を早く拾える**」。

⚠ **なぜ NCEI ではなくここなのか。**

| 取得元 | 中身 | 公表の遅れ |
| --- | --- | ---: |
| NCEI Storm Events | ⚠ **起きた結果**（被害額・死者） | ⚠ **101 日**【実測 2026-09-09】 |
| ⚠ **IEM の警報の保管庫** | ⚠ **起きるかもしれない**（警報） | ⚠ **1 日** |
| NWS の警報 API | 同上（配信） | 0 日。⚠ **ただし 7〜14 日しか遡れない** |

⚠ **警報と被害額は別物である。** ⚠ **予測に使うなら警報のほうが先に出る**が、
⚠ **警報が出ても何も起きない日がある**（空振り）。⚠ **どちらが効くかは検証で決める。**

⚠ **`robots.txt` に `Crawl-delay: 120`（2 分間隔）がある。** ⚠ **日ごとに取ると 8 年で 100 時間かかる**ので、
⚠ **期間をまとめて 1 回で取る**（既定は 1 年 1 回）。⚠ **間隔は必ず空ける**（`crawl_delay`）。

⚠ **同じ事象が「県ごとの行（`C`）」と「多角形の行（`P`）」で二重に出る**【実測 2026-09-09。429 事象中 288】。
⚠ **数えるのは事象であって行ではない**（VTEC の鍵で畳む）。

⚠ **年ごとに畳んだ事象を `raw/iem/events/<年>.jsonl` に置き、次からはそれを読む。**
⚠ **保管するのは 6 群に絞った後の事象である**（全部だと 1.4M 事象・150MB になる）。
⚠ **`GROUPS` を変えたら、その年のファイルを消して取り直す**（消さないと古い絞り込みのまま読む）。
⚠ **2026-09-09 に 7 年目でサーバが応答を途中で切り（`IncompleteRead`）、6 年ぶん 20 分の取得を失った。**
⚠ **2 分間隔の取得は失敗のたびに全部やり直せるものではない**ので、取れた年はその場で保管する。
"""

from __future__ import annotations

import csv
import http.client
import io
import json
import os
import time
import urllib.error
import urllib.request

import pandas as pd

from ail.data import regions, store
from ail.registry import register

BASE = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py"
UA = "ai-income-lab (research; contact via repository)"
# ⚠ **`robots.txt` の `Crawl-delay`。** ⚠ **短くしない**（相手の指定である）
CRAWL_DELAY = 120.0

# VTEC の現象コードをまとめる。⚠ **NCEI の `CATEGORIES` と同じ 6 つに揃える**
# （⚠ **揃えないと「警報と被害額のどちらが効くか」を同じ土俵で比べられない**）
GROUPS: dict[str, tuple[str, ...]] = {
    "竜巻": ("TO",),
    "熱帯": ("HU", "TR", "SS", "TY"),                    # ハリケーン・熱帯低気圧・高潮
    "洪水": ("FF", "FA", "FL", "CF"),
    "山火事": ("FW",),                                    # Red Flag（火災気象）
    "冬季": ("WS", "BZ", "IS", "ZR", "LE", "WW"),
    # ⚠ **`EH`（Excessive Heat）は 2024 年 10 月で止まり、`XH`（Extreme Heat）に替わった**【実測 2026-09-09。
    # XH 無しで取ると熱の系列が 2024-10-24 で終わる】。⚠ **コードが変わると系列が黙って止まる**
    "熱": ("EH", "XH", "HT"),
}
_GROUP_OF = {code: name for name, codes in GROUPS.items() for code in codes}
# ⚠ **注意報（`Y`）や注意喚起（`A`）ではなく「警報（`W`）」だけを数える。**
# ⚠ **混ぜると件数の意味が変わる**（注意報は桁が 1 つ多い）
SIGNIFICANCE = "W"

PREFIX = "WW_"


def _url(a: pd.Timestamp, b: pd.Timestamp) -> str:
    return (f"{BASE}?accept=csv"
            f"&year1={a.year}&month1={a.month}&day1={a.day}&hour1=0&minute1=0"
            f"&year2={b.year}&month2={b.month}&day2={b.day}&hour2=0&minute2=0")


def _rows(url: str, timeout: float = 900):
    """⚠ **流しながら読む。** ⚠ **1 年ぶんを丸ごとメモリに載せない**（80 万行になる）。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        yield from csv.DictReader(io.TextIOWrapper(resp, encoding="utf-8", errors="replace"))


def _events_of(rows) -> dict[tuple, dict]:
    """⚠ **VTEC の鍵で畳む。** 返りは 鍵 → {`day_ms`, `group`, `states`}。

    ⚠ **`C` の行にしか州が入らない**（`P` は多角形で UGC を持たない）。
    ⚠ **どちらの行からも「いつ・何が」は取れるので、事象そのものは落とさない。**
    """
    out: dict[tuple, dict] = {}
    for r in rows:
        if (r.get("significance") or "") != SIGNIFICANCE:
            continue
        group = _GROUP_OF.get((r.get("phenomena") or "").strip())
        if group is None:
            continue
        issued = (r.get("utc_issue") or "").strip()
        if len(issued) < 10:
            continue
        key = (r.get("vtec_year"), r.get("wfo"), r.get("phenomena"),
               r.get("significance"), r.get("eventid"))
        e = out.get(key)
        if e is None:
            # ⚠ **発表の日で数える**（UTC。足と同じ目盛りに揃える）
            e = out[key] = {"day": issued[:10], "group": group, "states": set()}
        ugc = (r.get("ugc") or "").strip()
        if len(ugc) >= 2:
            e["states"].add(ugc[:2].upper())
    return out


def _to_ms(day: str) -> int:
    return int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)


def _series(counter: dict[int, float]) -> pd.DataFrame:
    d = pd.DataFrame({"time_ms": list(counter), "value": list(counter.values())})
    return d.sort_values("time_ms").reset_index(drop=True)


def count_into(events: dict[tuple, dict], series: list[str],
               acc: dict[str, dict[int, float]]) -> None:
    """畳んだ事象を日次の件数に足し込む。⚠ **1 年ぶんずつ足す**（全期間をメモリに載せない）。

    `series` は `national`（全国）／`category`（種類別）／`region`（地域別）／
    `region_category`（地域 × 種類）。
    """
    want = set(series)

    def add(sid: str, ms: int) -> None:
        acc.setdefault(sid, {})
        acc[sid][ms] = acc[sid].get(ms, 0.0) + 1.0

    for e in events.values():
        ms = _to_ms(e["day"])
        g = e["group"]
        if "national" in want:
            add(f"{PREFIX}CNT", ms)
        if "category" in want:
            add(f"{PREFIX}CNT_{g}", ms)
        # ⚠ **地域は「その事象が掛かった州」ごとに 1 回数える。**
        # ⚠ **州を持たない事象（多角形だけ）は地域の集計に入らない**（全国には入る）
        hit = {r for s in e["states"] if (r := regions.region_of(s))}
        for rgn in hit:
            if "region" in want:
                add(f"{PREFIX}RGN_{rgn}", ms)
            if "region_category" in want:
                add(f"{PREFIX}{g}_{rgn}", ms)


def aggregate(events: dict[tuple, dict], series: list[str]) -> dict[str, pd.DataFrame]:
    """畳んだ事象 → 日次の系列。⚠ **NWS の API 側もこの関数を使う**（突き合わせのため）。"""
    acc: dict[str, dict[int, float]] = {}
    count_into(events, series, acc)
    return {sid: _series(c) for sid, c in sorted(acc.items()) if c}


def _events_path(lo: pd.Timestamp, hi: pd.Timestamp) -> str:
    return os.path.join(store.DATA, "raw", "iem", "events", f"{lo.date()}_{hi.date()}.jsonl")


def _load_events(path: str) -> dict[tuple, dict] | None:
    if not os.path.exists(path):
        return None
    out: dict[tuple, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            out[tuple(d["key"])] = {"day": d["day"], "group": d["group"], "states": set(d["states"])}
    return out


def _save_events(path: str, events: dict[tuple, dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for k, e in events.items():
            f.write(json.dumps({"key": list(k), "day": e["day"], "group": e["group"],
                                "states": sorted(e["states"])}, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _fetch_chunk(lo: pd.Timestamp, hi: pd.Timestamp, crawl_delay: float,
                 tries: int = 2, depth: int = 0) -> dict[tuple, dict]:
    """1 塊を取る。⚠ **途中で切られたら間隔を空けて取り直し、それでも駄目なら半分に割る。**"""
    last: Exception | None = None
    for i in range(tries):
        if i:
            time.sleep(crawl_delay)
        try:
            return _events_of(_rows(_url(lo, hi)))
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            print(f"    ⚠ {lo.date()} 〜 {hi.date()} を取り損ねた（{type(e).__name__}）", flush=True)
    if depth >= 2 or (hi - lo).days < 32:
        raise RuntimeError(f"⚠ {lo.date()} 〜 {hi.date()} を取れなかった") from last
    mid = lo + (hi - lo) / 2
    mid = pd.Timestamp(mid.date())
    print(f"    ⚠ 半分に割る: {lo.date()} 〜 {mid.date()} ／ {mid.date()} 〜 {hi.date()}", flush=True)
    time.sleep(crawl_delay)
    a = _fetch_chunk(lo, mid, crawl_delay, tries, depth + 1)
    time.sleep(crawl_delay)
    b = _fetch_chunk(mid, hi, crawl_delay, tries, depth + 1)
    return {**a, **b}


@register("source", "iem")
def fetch(series: list[str], start: str = "2018-01-01", end: str = "2026-09-09",
          crawl_delay: float = CRAWL_DELAY, **_) -> dict[str, pd.DataFrame]:
    """期間を年で割って取る。⚠ **`crawl_delay` 秒は必ず空ける**（`robots.txt` の指定）。

    ⚠ **年をまたぐ事象は 2 回返ってくる**（有効期間で切るため）。⚠ **1 つ前の塊の鍵を覚えて落とす。**
    ⚠ **丸 1 年の塊は `raw/iem/events/` に保管し、次からは読むだけ。** 年の途中までの塊は毎回取り直す
    （伸びるので）。⚠ **取り直したい年はそのファイルを消す。**
    """
    a, b = pd.Timestamp(start), pd.Timestamp(end)
    bounds = sorted({a, *pd.date_range(a, b, freq="YS"), b})
    acc: dict[str, dict[int, float]] = {}
    prev: set[tuple] = set()
    total = 0
    requested = False
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        if lo >= hi:
            continue
        full_year = (hi.month, hi.day) == (1, 1) and (lo.month, lo.day) == (1, 1)
        path = _events_path(lo, hi)
        got = _load_events(path) if full_year else None
        how = "保管庫から"
        if got is None:
            if requested:
                # ⚠ **相手の指定した間隔。** ⚠ **縮めない**
                time.sleep(crawl_delay)
            got = _fetch_chunk(lo, hi, crawl_delay)
            requested = True
            how = "取得"
            if full_year:
                _save_events(path, got)
        got = {k: v for k, v in got.items() if k not in prev}
        count_into(got, series, acc)
        prev = set(got)
        total += len(got)
        print(f"    {lo.date()} 〜 {hi.date()}: {len(got):,} 事象（{how}。累計 {total:,}）", flush=True)
    return {sid: _series(c) for sid, c in sorted(acc.items()) if c}
