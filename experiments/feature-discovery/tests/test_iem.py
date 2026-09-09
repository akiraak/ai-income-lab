"""IEM の警報の保管庫。⚠ **数えるのは行ではなく事象**で、⚠ **取れた年はその場で保管する。**"""

from __future__ import annotations

import io

import pandas as pd

from ail.data.sources import iem

HEAD = ("wfo,utc_issue,utc_expire,utc_prodissue,utc_init_expire,phenomena,gtype,significance,"
        "eventid,status,ugc,area2d,utc_updated,hvtec_nwsli,hvtec_severity,hvtec_cause,hvtec_record,"
        "is_emergency,utc_polygon_begin,utc_polygon_end,windtag,hailtag,tornadotag,damagetag,"
        "product_id,fcster,vtec_year")


def rows(lines):
    import csv
    return list(csv.DictReader(io.StringIO("\n".join([HEAD, *lines]))))


def test_county_and_polygon_rows_of_one_event_count_once():
    """⚠ **同じ事象が `C` と `P` で二重に出る**【実測 2019-05-20。429 事象中 288】。"""
    r = rows([
        "OUN,2019-05-20 12:35,2019-05-20 13:15,,,TO,C,W,309,NEW,TXC201,1,,,,,,False,,,,,,,,,2019",
        "OUN,2019-05-20 12:35,2019-05-20 13:15,,,TO,C,W,309,NEW,OKC001,1,,,,,,False,,,,,,,,,2019",
        "OUN,2019-05-20 12:35,2019-05-20 13:15,,,TO,P,W,309,NEW,,1,,,,,,False,,,,,,,,,2019",
    ])
    ev = iem._events_of(r)
    assert len(ev) == 1
    e = next(iter(ev.values()))
    assert e["group"] == "竜巻" and e["states"] == {"TX", "OK"}


def test_only_warnings_in_the_six_groups_are_counted():
    """⚠ **注意報（`Y`）や注意喚起（`A`）は数えない。** 現象も 6 群の外は落とす。"""
    r = rows([
        "OUN,2019-05-20 12:35,,,,TO,C,A,1,NEW,TXC201,1,,,,,,False,,,,,,,,,2019",   # 注意喚起
        "OUN,2019-05-20 12:35,,,,SV,C,W,2,NEW,TXC201,1,,,,,,False,,,,,,,,,2019",   # 群の外
        "OUN,2019-05-20 12:35,,,,FF,C,W,3,NEW,TXC201,1,,,,,,False,,,,,,,,,2019",   # ✅
    ])
    ev = iem._events_of(r)
    assert [e["group"] for e in ev.values()] == ["洪水"]


def test_regional_series_count_each_state_once_per_event():
    r = rows([
        "OUN,2019-05-20 12:35,,,,TO,C,W,1,NEW,TXC201,1,,,,,,False,,,,,,,,,2019",
        "OUN,2019-05-20 12:35,,,,TO,C,W,1,NEW,TXC203,1,,,,,,False,,,,,,,,,2019",   # 同じ州の別の県
        "OUN,2019-05-20 12:35,,,,TO,C,W,1,NEW,CAC001,1,,,,,,False,,,,,,,,,2019",   # 西部
    ])
    out = iem.aggregate(iem._events_of(r), ["national", "region", "region_category"])
    assert out["WW_CNT"]["value"].tolist() == [1.0]
    assert out["WW_RGN_湾岸"]["value"].tolist() == [1.0]     # ⚠ 県が 2 つでも 1
    assert out["WW_RGN_西部"]["value"].tolist() == [1.0]
    assert out["WW_竜巻_湾岸"]["value"].tolist() == [1.0]


def test_a_saved_year_round_trips(tmp_path):
    """⚠ **7 年目で応答が切れて 6 年ぶんを失った**（2026-09-09）。保管したものを読めば同じ結果になる。"""
    r = rows(["OUN,2019-05-20 12:35,,,,TO,C,W,1,NEW,TXC201,1,,,,,,False,,,,,,,,,2019"])
    ev = iem._events_of(r)
    path = str(tmp_path / "2019.jsonl")
    iem._save_events(path, ev)
    back = iem._load_events(path)
    assert back == ev
    assert iem._load_events(str(tmp_path / "none.jsonl")) is None
