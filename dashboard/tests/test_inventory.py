"""データの画面（/data）。

⚠ **この画面の約束は 1 つ — 実験側が書いたものを写すだけで、数え直さない・分類し直さない。**
⚠ **行数は manifest の totals、枠と仮説は config の宣言、ずらし幅と規約は sources.toml。**
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import inventory as inv
from app.main import create_app

# 2018-01-01 / 2026-09-01 (UTC) の ms
MS_2018 = 1514764800000
MS_2026 = 1788220800000

SOURCES_TOML = """
[tastytrade]
label = "tastytrade API（価格の足）"
terms = "契約"
terms_note = "口座保有者向けの API"

[ncei_storm]
label = "NCEI Storm Events（災害）"
lag_days = 120
publish_note = "公表が 101 日遅れる【実測】"
terms = "公有"
terms_note = "米政府の著作物"

[noaa]
label = "NOAA GHCN-Daily（気象）"
lag_days = 1
terms = "公有"
terms_note = "米政府の著作物"
"""

DATASET_TOML = """
name = "impact_daily"
period = "d"

[[series]]
source = "ncei_storm"
role = "本命"
hypothesis = "災害は保険の支払いと操業停止を通じて業績に効く"
"""

UNIVERSE_TOML = """
name = "us63"
description = "米国上場 2 銘柄（テスト）"
selected_on = "2026-09-08"
survivorship_bias = true

[groups]
etf = ["AAA"]
company = ["BBB"]
"""

EXPOSURE_TOML = """
name = "us63"
selected_on = "2026-09-09"
hindsight = true

[[channel]]
name = "ins"
source = "ncei_storm"
kind = "scalar"
series = "SE_DAMAGE"
weights = "ins"
hypothesis = "全国の被害額は保険の支払いになる"

[weights.geo]
XLE = { "湾岸" = 0.60, "西部" = 0.15 }

[weights.ins]
"BRK/B" = 1.00
XLF = 0.40
"""


def manifest(layer="raw", period="d", source="tastytrade", dataset="daily",
             rows=999, symbols=2, role=None, **extra) -> dict:
    m = {"layer": layer, "period": period, "written_at": "2026-09-09T00:00:00-0700",
         "symbols": symbols, "source": source, "dataset": dataset,
         "totals": {"rows": rows, "ohlc_inconsistent": 3, "scale_break": 0},
         "series": {"AAA": {"rows": 5, "oldest_ms": MS_2018, "newest_ms": MS_2026},
                    "BBB": {"rows": 5, "oldest_ms": MS_2018 + 86400000, "newest_ms": MS_2026}}}
    m.update(extra)
    if role:
        m["role"] = role
    return m


def build_exp_dir(exp_dir: Path, *, dataset_toml=DATASET_TOML) -> Path:
    (exp_dir / "data" / "manifests").mkdir(parents=True)
    (exp_dir / "config" / "dataset").mkdir(parents=True)
    (exp_dir / "config" / "exposure").mkdir(parents=True)
    (exp_dir / "config" / "universe").mkdir(parents=True)
    (exp_dir / "config" / "sources.toml").write_text(SOURCES_TOML, encoding="utf-8")
    (exp_dir / "config" / "universe" / "us63.toml").write_text(UNIVERSE_TOML, encoding="utf-8")
    (exp_dir / "config" / "dataset" / "impact_daily.toml").write_text(dataset_toml, encoding="utf-8")
    (exp_dir / "config" / "exposure" / "us63.toml").write_text(EXPOSURE_TOML, encoding="utf-8")

    def put(name, m):
        (exp_dir / "data" / "manifests" / name).write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")

    put("raw_d.json", manifest())
    put("adjusted_d.json", manifest(layer="adjusted", rows=990, repaired_breaks=4,
                                    kept_as_real_move=9, rows_rescaled=123, dividend_adjusted=False))
    # ⚠ manifest の枠（取得時の写し）は、わざと config の宣言（本命）と食い違わせてある
    put("raw_ncei_storm_series.json", manifest(layer="raw_ncei_storm", period="series",
                                               source="ncei_storm", dataset="impact_daily",
                                               rows=100, symbols=13, role="偽薬"))
    put("raw_noaa_series.json", manifest(layer="raw_noaa", period="series", source="noaa",
                                         dataset="exog_daily", rows=50, symbols=9, role="偽薬"))

    feat = exp_dir / "data" / "features" / "own_only_h1"
    feat.mkdir(parents=True)
    (feat / "d.meta.json").write_text(json.dumps({"layer": "adjusted", "experiment": "own_only_h1",
                                                  "period": "d", "leak": False, "rows": 130134,
                                                  "features": 59, "built_at": "2026-09-09T14-19-31"}))
    leak = exp_dir / "data" / "features" / "own_only_h1_leak"
    leak.mkdir(parents=True)
    (leak / "d.meta.json").write_text(json.dumps({"layer": "adjusted", "experiment": "own_only_h1_leak",
                                                  "period": "d", "leak": True, "rows": 10,
                                                  "features": 59, "built_at": "2026-09-09T14-19-31"}))
    return exp_dir


# --- 写すだけであること ---------------------------------------------------

def test_rows_come_from_the_manifest_totals_not_recounted(settings):
    """⚠ **totals.rows（999）と系列の合計（10）をわざとずらしてある。** 画面に出るのは totals。"""
    build_exp_dir(settings.exp_dir)
    d = inv.index(settings)
    raw = [m for m in d["bars"] if m["layer"] == "raw"][0]
    assert raw["rows"] == 999
    assert raw["symbols"] == 2
    assert raw["oldest"] == "2018-01-01" and raw["newest"] == "2026-09-01"
    assert "ohlc_inconsistent 3" in raw["issues"] and len(raw["issues"]) == 1   # 0 の項目は出さない


def test_symbol_kinds_are_mirrored_from_the_universe(settings):
    """⚠ **種別（会社株 ／ ETF）は universe の宣言を写すだけ。** 銘柄名から推測しない。
    ⚠ **宣言に無い銘柄は「分類なし」で見せる**（黙ってどちらかに寄せない）。"""
    build_exp_dir(settings.exp_dir)
    m = settings.exp_dir / "data" / "manifests" / "raw_d.json"
    data = json.loads(m.read_text(encoding="utf-8"))
    data["series"]["CCC"] = {"rows": 1, "oldest_ms": MS_2018, "newest_ms": MS_2026}
    m.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    raw = [b for b in inv.index(settings)["bars"] if b["layer"] == "raw"][0]
    assert dict(raw["kinds"]) == {"ETF": 1, "会社株": 1, "分類なし": 1}
    assert {s["name"]: s["kind"] for s in raw["series"]} == {
        "AAA": "ETF", "BBB": "会社株", "CCC": "分類なし"}


def test_universes_are_listed_with_their_limits(settings):
    build_exp_dir(settings.exp_dir)
    u = inv.index(settings)["universes"][0]
    assert u["name"] == "us63" and u["survivorship_bias"] is True
    assert u["groups"] == {"etf": ["AAA"], "company": ["BBB"]}


def test_role_and_hypothesis_are_mirrored_from_config(settings):
    """⚠ **枠は config の宣言が正。** manifest と食い違ったら黙って選ばず、⚠ を立てて見せる。"""
    build_exp_dir(settings.exp_dir)
    d = inv.index(settings)
    storm = [e for e in d["external"] if e["source"] == "ncei_storm"][0]
    assert storm["role"] == "本命"                      # config の宣言
    assert storm["role_mismatch"] is True               # manifest（偽薬）との食い違いを隠さない
    assert "保険" in storm["hypothesis"]
    noaa = [e for e in d["external"] if e["source"] == "noaa"][0]
    assert noaa["role"] == "偽薬"                       # 宣言が無い dataset は manifest の写し
    assert noaa["role_mismatch"] is False and noaa["hypothesis"] is None


def test_lag_and_terms_join_by_source(settings):
    """ずらし幅と規約は sources.toml から取得元で引く。"""
    build_exp_dir(settings.exp_dir)
    d = inv.index(settings)
    storm = [e for e in d["external"] if e["source"] == "ncei_storm"][0]
    assert storm["lag_days"] == 120 and storm["terms"] == "公有"
    assert [s for s in d["sources"] if s["source"] == "ncei_storm"][0]["lag_days"] == 120


def test_features_meta_and_leak_split(settings):
    build_exp_dir(settings.exp_dir)
    d = inv.index(settings)
    assert [f["experiment"] for f in d["features"]] == ["own_only_h1"]
    assert d["features"][0]["rows"] == 130134 and d["features"][0]["features"] == 59
    assert [f["experiment"] for f in d["leak_features"]] == ["own_only_h1_leak"]


def test_exposure_is_listed_with_hindsight(settings):
    """割り当ては経路と重みをそのまま出す。⚠ **後知恵の印も宣言の写し。**"""
    build_exp_dir(settings.exp_dir)
    x = inv.index(settings)["exposures"][0]
    assert x["hindsight"] is True and x["selected_on"] == "2026-09-09"
    assert x["channels"][0]["name"] == "ins" and "保険" in x["channels"][0]["hypothesis"]
    geo = x["weights"]["geo"][0]
    assert geo["symbol"] == "XLE" and geo["total"] == 0.75 and "湾岸 0.6" in geo["detail"]
    ins = x["weights"]["ins"]
    assert [e["symbol"] for e in ins] == ["BRK/B", "XLF"]   # 重みの降順
    assert x["symbols"] == ["BRK/B", "XLE", "XLF"]


# --- 無くても落ちない -----------------------------------------------------

def test_missing_exp_dir_is_ok(settings):
    """⚠ **g3plus には実験ディレクトリを COPY しない。** 無くても 200。"""
    d = inv.index(settings)
    assert d["empty"] is True
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as c:
        r = c.get("/data")
        assert r.status_code == 200 and "実験のデータが無い" in r.text
        assert c.get("/api/data").status_code == 200


def test_broken_files_are_skipped(settings):
    build_exp_dir(settings.exp_dir)
    (settings.exp_dir / "data" / "manifests" / "broken.json").write_text("{oops", encoding="utf-8")
    (settings.exp_dir / "config" / "sources.toml").write_text("= broken", encoding="utf-8")
    d = inv.index(settings)
    assert len(d["bars"]) == 2 and d["sources"] == []
    storm = [e for e in d["external"] if e["source"] == "ncei_storm"][0]
    assert storm["lag_days"] is None                     # 宣言が読めないときは「—」で出す（0 で埋めない）


def test_page_renders_the_inventory(settings):
    build_exp_dir(settings.exp_dir)
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as c:
        text = c.get("/data").text
        assert "調整後" in text and "NCEI Storm Events" in text
        assert "120 日" in text                          # ずらし幅
        assert "【推測】" in text and "後知恵" in text     # 割り当ての限界を画面に明示
        assert "config と manifest で枠が食い違う" in text
