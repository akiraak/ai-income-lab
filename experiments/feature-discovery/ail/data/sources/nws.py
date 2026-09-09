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


def _vtec_parts(props: dict) -> tuple[str, str, str, str] | None:
    """`/O.NEW.KOUN.TO.W.0309.190520T1235Z-.../` → (wfo, 現象, 重大度, 事象番号)。

    ⚠ **VTEC を持たない警報がある**（非 VTEC の製品）。⚠ **数え方を IEM と揃えるため、それは捨てる。**
    """
    raw = (props.get("parameters") or {}).get("VTEC") or []
    for v in raw:
        p = str(v).strip("/").split(".")
        if len(p) >= 6:
            return p[2], p[3], p[4], p[5]
    return None


def events(start: str, end: str, pages: int = 20, pause: float = 1.0) -> dict[tuple, dict]:
    """⚠ **IEM と同じ形（VTEC の鍵で畳んだ事象）にして返す。** 突き合わせはこの形で行う。

    ⚠ **時刻は URL に載せる前に符号化する。** ⚠ **`+00:00` の `+` は素で書くと空白と読まれ、400 になる。**
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
            wfo, phenomena, significance, eventid = parts
            if significance != iem.SIGNIFICANCE:
                continue
            group = iem._GROUP_OF.get(phenomena)
            if group is None:
                continue
            sent = str(props.get("sent") or "")[:10]
            if len(sent) < 10:
                continue
            # ⚠ **IEM は WFO を 3 文字で持つ**（`KOUN` → `OUN`）。⚠ **鍵を揃えないと突き合わせで全部ずれる**
            key = (sent[:4], wfo[-3:], phenomena, significance, str(int(eventid)))
            e = out.get(key)
            if e is None:
                e = out[key] = {"day": sent, "group": group, "states": set()}
            for ugc in (props.get("geocode") or {}).get("UGC") or []:
                if len(str(ugc)) >= 2:
                    e["states"].add(str(ugc)[:2].upper())
        nxt = (doc.get("pagination") or {}).get("next")
        if not nxt or not feats:
            break
        url = nxt
    return out


@register("source", "nws")
def fetch(series: list[str], start: str = "", end: str = "", **_) -> dict[str, pd.DataFrame]:
    """⚠ **`raw/` に置く用ではなく、突き合わせ用。** 期間は 7〜14 日しか遡れない。"""
    if not (start and end):
        raise SystemExit("⚠ nws は start / end（ISO 8601）が要る。⚠ 遡れるのは 7〜14 日だけ")
    return iem.aggregate(events(start, end), series)
