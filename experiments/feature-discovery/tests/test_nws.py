"""NWS の警報 API。⚠ **日は IEM と同じく「VTEC の始まり（UTC）」で決める。**"""

from __future__ import annotations

from ail.data.sources import nws


def feature(vtec, sent, ugc=("AZC019",)):
    return {"properties": {"parameters": {"VTEC": [vtec]}, "sent": sent,
                           "geocode": {"UGC": list(ugc)}}}


def test_day_is_the_vtec_begin_in_utc_not_the_last_message(monkeypatch):
    """⚠ **API は新しい順に返す。** 先頭は最後の続報で、`sent` は現地時刻（2026-09-16 に踏んだ）。

    `TWC FF 202` は 9/13 18:57（MST）に出て 9/14 01:57Z に始まった。⚠ **IEM は 9/14 に数える。**
    """
    page = {"features": [
        feature("/O.EXP.KTWC.FF.W.0202.000000T0000Z-260914T0400Z/", "2026-09-13T20:57:00-07:00"),
        feature("/O.CON.KTWC.FF.W.0202.000000T0000Z-260914T0400Z/", "2026-09-13T20:11:00-07:00"),
        feature("/O.NEW.KTWC.FF.W.0202.260914T0157Z-260914T0400Z/", "2026-09-13T18:57:00-07:00",
                ugc=("AZC019", "NMC023")),
    ], "pagination": {}}
    monkeypatch.setattr(nws, "_get", lambda url: page)
    ev = nws.events("2026-09-12T00:00:00+00:00", "2026-09-16T00:00:00+00:00")
    assert list(ev) == [("2026", "TWC", "FF", "W", "202")]
    e = ev[("2026", "TWC", "FF", "W", "202")]
    assert e["day"] == "2026-09-14" and e["group"] == "洪水" and e["states"] == {"AZ", "NM"}


def test_a_scheduled_begin_days_ahead_counts_on_the_begin_day(monkeypatch):
    """⚠ **川の洪水は数日前に予告される**（`TBW FL 2`: 9/11 に出て、始まりは 9/14 09:00Z）。

    ⚠ **日付けは `NEW` の VTEC の始まりの日**（発表の日ではない）。⚠ **`NEW` が窓に無ければ数えない**
    （2026-09-17 のマージで titan の直し方に揃えた。`cli.crosscheck` は配信側の窓を 3 日前から取る）。
    """
    page = {"features": [
        feature("/O.EXT.KTBW.FL.W.0002.260914T0900Z-000000T0000Z/", "2026-09-12T21:33:00-04:00",
                ugc=("FLC057",)),
        feature("/O.NEW.KTBW.FL.W.0002.260914T0900Z-260915T0000Z/", "2026-09-11T21:33:00-04:00",
                ugc=("FLC057",)),
    ]}
    monkeypatch.setattr(nws, "_get", lambda url: page)
    ev = nws.events("a", "b")
    assert [e["day"] for e in ev.values()] == ["2026-09-14"]
    page["features"] = page["features"][:1]          # NEW が窓の外
    assert nws.events("a", "b") == {}


def test_an_event_with_only_follow_ups_is_not_counted(monkeypatch):
    """⚠ **API は 7 日しか持たない。** `NEW` が消えて続報だけ残った事象は、始まった日が分からないので数えない。

    `PSR XH 9` は 9/10 の窓に `CON` しか無く、`sent` で日付けすると 9/10 になった（IEM では 9/10 より前）。
    """
    page = {"features": [
        feature("/O.CON.KPSR.XH.W.0009.000000T0000Z-260912T0300Z/", "2026-09-11T00:02:00-07:00",
                ugc=("AZZ537",)),
        feature("/O.CON.KPSR.XH.W.0009.000000T0000Z-260912T0300Z/", "2026-09-10T01:12:00-07:00",
                ugc=("AZZ537",)),
    ]}
    monkeypatch.setattr(nws, "_get", lambda url: page)
    assert nws.events("a", "b") == {}
