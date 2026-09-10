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
from datetime import datetime, timezone

import pandas as pd

from ail.data.sources import iem
from ail.registry import register

BASE = "https://api.weather.gov/alerts"
UA = "ai-income-lab (research; contact via repository)"


def _get(url: str, timeout: float = 120) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/geo+json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def _vtec_parts(props: dict) -> tuple[str, str, str, str, str, str] | None:
    """`/O.NEW.KOUN.TO.W.0309.190520T1235Z-.../` → (動作, wfo, 現象, 重大度, 事象番号, 期間)。

    ⚠ **VTEC を持たない警報がある**（非 VTEC の製品）。⚠ **数え方を IEM と揃えるため、それは捨てる。**
    ⚠ **動作（NEW / CON / EXT …）も返す。** ⚠ **発表の日は NEW のメッセージからしか取れない**
    （CON で数えると、前日に発表された長寿命の警報が「その日の事象」に化ける）。
    """
    raw = (props.get("parameters") or {}).get("VTEC") or []
    for v in raw:
        p = str(v).strip("/").split(".")
        if len(p) >= 7:
            return p[1], p[2], p[3], p[4], p[5], p[6]
    return None


def _utc_day(stamp: str) -> str:
    """`2026-09-07T19:05:00-06:00` → `2026-09-08`。

    ⚠ **NWS の `sent` は現地時間である。** ⚠ **素直に `[:10]` を取ると現地の日付になり、
    UTC で数える IEM と丸 1 日ずれる**（夕方の警報は現地の「今日」＝ UTC の「明日」）。
    """
    try:
        return datetime.fromisoformat(stamp).astimezone(timezone.utc).date().isoformat()
    except ValueError:
        return ""


def _begin_day(span: str) -> str:
    """VTEC の期間 `260909T1800Z-260911T0300Z` → 効力が始まる日 `2026-09-09`（UTC）。

    ⚠ **IEM の `utc_issue` は「発表した時刻」ではなく「効力が始まる時刻」である**
    【実測 2026-09-10。先出しの熱警報が 配信 sent 09-08 ／ 保管庫 issue 09-09 とずれた】。
    ⚠ **即時発効の警報は `000000T0000Z`（全部 0）**なので、そのときは空を返し `sent` に落とす。
    """
    head = span.split("-", 1)[0].strip()
    if len(head) < 7 or head.startswith("000000"):
        return ""
    try:
        return datetime.strptime(head[:7], "%y%m%dT").date().isoformat()
    except ValueError:
        return ""


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
            action, wfo, phenomena, significance, eventid, span = parts
            if significance != iem.SIGNIFICANCE:
                continue
            group = iem._GROUP_OF.get(phenomena)
            if group is None:
                continue
            # ⚠ **日付は「効力が始まる日」（VTEC の期間の先頭・UTC）**。IEM の `utc_issue` と同じ目盛り。
            # ⚠ **即時発効（期間が全部 0）のときだけ `sent` の UTC の日に落とす**
            day = _begin_day(span) or _utc_day(str(props.get("sent") or ""))
            if not day:
                continue
            # ⚠ **IEM は WFO を 3 文字で持つ**（`KOUN` → `OUN`）。⚠ **鍵を揃えないと突き合わせで全部ずれる**
            key = (day[:4], wfo[-3:], phenomena, significance, str(int(eventid)))
            e = out.get(key)
            if e is None:
                e = out[key] = {"day": "", "group": group, "states": set()}
            # ⚠ **数えるのは NEW のメッセージだけ**（CON / EXT で数えると、
            # 前日に発表された長寿命の警報が「その日の事象」に化ける）
            if action == "NEW" and (not e["day"] or day < e["day"]):
                e["day"] = day
            for ugc in (props.get("geocode") or {}).get("UGC") or []:
                if len(str(ugc)) >= 2:
                    e["states"].add(str(ugc)[:2].upper())
        nxt = (doc.get("pagination") or {}).get("next")
        if not nxt or not feats:
            break
        url = nxt
    # ⚠ **NEW が窓に無い事象は数えない**（発表は窓の外＝別の日の事象。IEM 側もその日には数えない）
    return {k: v for k, v in out.items() if v["day"]}


@register("source", "nws")
def fetch(series: list[str], start: str = "", end: str = "", **_) -> dict[str, pd.DataFrame]:
    """⚠ **`raw/` に置く用ではなく、突き合わせ用。** 期間は 7〜14 日しか遡れない。

    ⚠ **問い合わせの窓は 3 日だけ手前に広げる。** API の窓はメッセージの送信時刻で切られるが、
    ⚠ **先出しの警報（熱・冬季・熱帯）は NEW が効力開始の 1〜3 日前に送られる**ので、
    対象日だけの窓では NEW が見えず、事象ごと落ちる【実測 2026-09-10】。
    数える日は効力開始日（`events()` が付ける）のままなので、広げても二重計上にはならない。
    """
    if not (start and end):
        raise SystemExit("⚠ nws は start / end（ISO 8601）が要る。⚠ 遡れるのは 7〜14 日だけ")
    lo = (pd.Timestamp(start) - pd.Timedelta(days=3)).isoformat()
    return iem.aggregate(events(lo, end), series)
