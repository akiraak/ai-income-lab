"""`config/sources.toml`（管理画面が写す宣言）とコードの一致。

⚠ **ずらし幅の実効値は `ail/features/exog.py` / `impact.py` が持つ。** 宣言だけ直して
コードを直し忘れる（またはその逆）と、⚠ **画面が「120 日ずらしている」と言いながら
実際は 1 日しかずらしていない**、という嘘になる。ここで固定する。
"""

from __future__ import annotations

import os
import tomllib

import pytest

from ail.features import exog, impact

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "sources.toml")


@pytest.fixture(scope="module")
def decl() -> dict:
    with open(CONFIG, "rb") as f:
        return tomllib.load(f)


def test_every_exog_source_is_declared(decl):
    """⚠ **`ex_` 層が読む取得元は全部宣言されている**（画面に「不明」を出さない）。"""
    for src in exog.DEFAULT_SOURCES:
        assert src in decl, f"config/sources.toml に {src} が無い"


def test_every_impact_channel_source_is_declared(decl):
    """⚠ **`im_` 層の経路（config/exposure/*.toml）が読む取得元も全部宣言されている。**"""
    from ail import config as ail_config

    conf = ail_config.exposure("us63")
    for ch in conf.get("channel", []):
        assert ch["source"] in decl, f"config/sources.toml に {ch['source']} が無い（経路 {ch['name']}）"


def test_declared_lag_days_match_the_code(decl):
    """⚠ **宣言の lag_days ＝ コードの実効値**（既定 1、取得元ごとの上書きは DEFAULT_SOURCE_LAG_DAYS）。"""
    for module in (exog, impact):
        for src, entry in decl.items():
            if "lag_days" not in entry:
                continue   # 足（tastytrade）にはずらし幅の概念が無い
            effective = module.DEFAULT_SOURCE_LAG_DAYS.get(src, module.DEFAULT_LAG_DAYS)
            assert entry["lag_days"] == effective, (
                f"{src}: 宣言 {entry['lag_days']} 日 ≠ {module.__name__} の実効値 {effective} 日")


def test_code_overrides_are_all_declared(decl):
    """⚠ **コード側で上書きした取得元（例: ncei_storm 120 日）が宣言から漏れていない。**"""
    for module in (exog, impact):
        for src in module.DEFAULT_SOURCE_LAG_DAYS:
            assert decl.get(src, {}).get("lag_days") is not None, (
                f"{module.__name__} が {src} を上書きしているのに config/sources.toml に lag_days が無い")


def test_terms_use_the_three_categories(decl):
    """規約の判定は 3 分類（公有 ／ robots ／ 契約）に根拠つき。⚠ **「要判断」の取得元は載せない**
    （データを持っていないものは在庫の画面に出さない。daily-data-sources.md §2・§4 が正本）。"""
    for src, entry in decl.items():
        assert entry.get("terms") in ("公有", "robots", "契約"), f"{src}: terms が 3 分類でない"
        assert entry.get("terms_note"), f"{src}: 根拠（terms_note）が無い"
