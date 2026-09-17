"""NWS の警報 API（`api.weather.gov`）。⚠ **鍵が要らない。** ⚠ **運用（今の値）のための経路である。**

⚠ **検証には使えない。** ⚠ **遡れるのは 7〜14 日だけ**【実測 2026-09-09】なので、
⚠ **過去は [IEM の保管庫](iem.py)から取る。**

⚠ **`robots.txt` は `Disallow: /` だが、API の規約が明示的に許可している**
（「open data, free to use for any purpose」「we do not charge any fees」）。
⚠ **Open-Meteo と同じ立て方**で、規約を上に置く。⚠ **`User-Agent` に連絡先を入れることを求められている。**

⚠ **この経路の存在理由は 1 つ。** ⚠ **「検証で見た値」と「運用で見る値」が同じであることを確かめる**
ためである（[`cli/crosscheck.py`](../../../cli/crosscheck.py)）。⚠ **確かめずに 2 経路を使うと、
検証の数字と運用の数字が別物になっていても気づけない。**
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import pandas as pd

from ail.data.sources import iem
from ail.registry import register

BASE = "https://api.weather.gov/alerts"
UA = "ai-income-lab (research; contact via repository)"


def _get(url: str, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/geo+json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def _vtec_parts(props: dict) -> tuple[str, str, str, str, pd.Timestamp | None] | None:
    """`/O.NEW.KOUN.TO.W.0309.190520T1235Z-.../` → (wfo, 現象, 重大度, 事象番号, 始まりの時刻 UTC)。

    ⚠ **VTEC を持たない警報がある**（非 VTEC の製品）。⚠ **数え方を IEM と揃えるため、それは捨てる。**
    ⚠ **続報（`CON` `EXP` など）の始まりは `000000T0000Z`（既に始まっている）なので `None` を返す。**
    """
    raw = (props.get("parameters") or {}).get("VTEC") or []
    for v in raw:
        p = str(v).strip("/").split(".")
        if len(p) >= 6:
            begin = None
            if len(p) >= 7 and not p[6].startswith("000000"):
                begin = pd.to_datetime(p[6].split("-")[0], format="%y%m%dT%H%MZ", utc=True)
            return p[2], p[3], p[4], p[5], begin
    return None


def events(start: str, end: str, pages: int = 60, pause: float = 1.0) -> dict[tuple, dict]:
    """⚠ **IEM と同じ形（VTEC の鍵で畳んだ事象）にして返す。** 突き合わせはこの形で行う。

    ⚠ **時刻は URL に載せる前に符号化する。** ⚠ **`+00:00` の `+` は素で書くと空白と読まれ、400 になる。**

    ⚠ **日は「VTEC の始まり（UTC）」で決める**（IEM の `utc_issue` と同じもの）。⚠ **2026-09-16 までは
    「API が最初に返したメッセージの `sent` の先頭 10 文字」で決めていた。** ⚠ **API は新しい順に返すので
    それは最後の続報（`EXP` `CAN`）であり、しかも `sent` は現地時刻である。** ⚠ **この 2 つで 9/14 は
    39 事象のうち 6 が隣の日へずれ、別の日の 9 事象が入ってきた**【実測 2026-09-16】。
    ⚠ **始まりの無い事象（窓の中に続報しか無い）は数えない。** ⚠ **API はメッセージを 7 日しか持たない**
    【実測 2026-09-16。23:50Z に取って最古が 7 日前の 23:52Z】ので、⚠ **`NEW` が消えた事象を `sent` で
    日付けすると、始まった日より後ろへずれる**（9/10 で 4 事象。熱の警報は 9/6 以前に出ていた）。
    """
    url = BASE + "?" + urllib.parse.urlencode({"start": start, "end": end, "limit": 500})
    out: dict[tuple, dict] = {}
    for i in range(pages):
        if i:
            time.sleep(pause)
        doc = _get(url)
        feats = doc.get("features") or []
        for f in feats:
            props = f.get("properties") or {}
            parts = _vtec_parts(props)
            if parts is None:
                continue
            wfo, phenomena, significance, eventid, begin = parts
            if significance != iem.SIGNIFICANCE:
                continue
            group = iem._GROUP_OF.get(phenomena)
            if group is None:
                continue
            sent = props.get("sent")
            if not sent:
                continue
            # ⚠ **IEM は WFO を 3 文字で持つ**（`KOUN` → `OUN`）。⚠ **鍵を揃えないと突き合わせで全部ずれる**
            key = (wfo[-3:], phenomena, significance, str(int(eventid)))
            e = out.get(key)
            if e is None:
                e = out[key] = {"group": group, "states": set(), "begins": []}
            if begin is not None:
                e["begins"].append(begin)
            for ugc in (props.get("geocode") or {}).get("UGC") or []:
                if len(str(ugc)) >= 2:
                    e["states"].add(str(ugc)[:2].upper())
        nxt = (doc.get("pagination") or {}).get("next")
        if not nxt or not feats:
            break
        url = nxt
    done: dict[tuple, dict] = {}
    for key, e in out.items():
        if not e["begins"]:
            continue
        at = min(e["begins"])
        done[(str(at.year), *key)] = {"day": str(at.date()), "group": e["group"], "states": e["states"]}
    return done


@register("source", "nws")
def fetch(series: list[str], start: str = "", end: str = "", **_) -> dict[str, pd.DataFrame]:
    """⚠ **`raw/` に置く用ではなく、突き合わせ用。** 期間は 7〜14 日しか遡れない。"""
    if not (start and end):
        raise SystemExit("⚠ nws は start / end（ISO 8601）が要る。⚠ 遡れるのは 7〜14 日だけ")
    return iem.aggregate(events(start, end), series)
