"""⚠ **予測モデル名は台帳の鍵の別名である**（rules.md 10-2）。

⚠ **鍵が違うのに名前が同じだと、別の試行が 1 つに見える**（台帳が鍵を足してきた理由 ＝ 数え落とし と同じ事故）。
だから ⚠ **台帳の全行で「鍵 ↔ 名前」が 1 対 1 であることを確かめる。**
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

from ail import catalog, names

TOKEN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

LIVE = {"鍵": "全部使う（基準）", "モデル": "Ridge", "粒度": "日足", "地平": "1 本（1 日）",
        "特徴量の層": "own", "層": "adjusted", "期間": "2018-01-31", "銘柄": "63",
        "検証方式": "閾値売買", "形式": "共通", "較正": "std", "閾値": "50"}


@pytest.mark.parametrize("change,want", [
    # 実売買の 3 人（2026-09-19 に確定。config/traders/T1〜T3.toml）
    ({}, "own.all.ridge.a@50"),
    ({"モデル": "LightGBM", "特徴量の層": "own cs rel ex", "銘柄": "48", "閾値": "55"},
     "own-cs-rel-ex.all.lgbm.a~n48@55"),
    ({"鍵": "T3 QUANT（60日窓）", "特徴量の層": "own seq"}, "own-seq.t3-quant60.ridge.a@50"),
    # 既定から外れた列だけが `~` で後ろに付く（順は 検証方式 → 粒度 → 地平 → 層 → 期間 → 銘柄 → 較正）
    ({"較正": "旧"}, "own.all.ridge.a~cal-old@50"),
    ({"期間": "1995-02-10", "形式": "銘柄別"}, "own.all.ridge.b~p1995-02-10@50"),
    ({"検証方式": "毎日往復", "較正": "—", "閾値": "—", "期間": "—", "銘柄": "—"}, "own.all.ridge.a~rt~p-na~n-na"),
    ({"粒度": "1 分足", "地平": "390 本（1 取引日）", "層": "raw", "検証方式": "毎日往復", "較正": "—", "閾値": "—"},
     "own.all.ridge.a~rt~1m~h390~raw"),
    ({"鍵": "F3-1b", "モデル": "LightGBM+GAN増強(batch16k)"}, "own.f3-1b.lgbm-gan-16k.a@50"),
    ({"鍵": "入口D2(60)×出口D1(20)（学習）", "特徴量の層": "own trend"}, "own-trend.in-d2-out-d1.ridge.a@50"),
    ({"鍵": "乱択（基準）", "モデル": "—"}, "own.random.none.a@50"),
    ({"鍵": "ボラ上位〔T3 QUANT（60日窓）・上位3・整数株B⚠〕", "モデル": "—", "特徴量の層": "own seq"},
     "own-seq.voltop-t3-quant60-top3-shb.none.a@50"),
    ({"鍵": "全部使う（基準）〔上位10・端数〕"}, "own.all-top10-frac.ridge.a@50"),
])
def test_names_are_built_from_the_key(change, want):
    row = {**LIVE, **change}
    assert names.trial_name(row) == want
    assert names.model_name(row) == want.split("@")[0]


def test_unknown_method_stops():
    """⚠ **綴りの一覧に無い選び方・作り方は止める**（黙って別の名前に寄せると、2 つの別物が 1 つに見える）。"""
    with pytest.raises(SystemExit, match="数字の選び方・作り方"):
        names.method_slug("X9 まだ名前の無い検知器")
    with pytest.raises(SystemExit, match="学習器"):
        names.learner_slug("XGBoost")


def _registered() -> dict[str, list[str]]:
    """本物の登録簿（⚠ 新しいプロセスで読む。ほかのテストが偽の検知器を足していても混ざらない）。"""
    code = ("import json, ail.bootstrap; from ail import registry; print(json.dumps("
            "{k: registry.available(k) for k in ('model', 'detector', 'selector', 'transform')}, ensure_ascii=False))")
    out = subprocess.run([sys.executable, "-c", code], cwd=names.ROOT, capture_output=True, text=True, check=True,
                         env={**os.environ, "PYTHONPATH": names.ROOT})
    return json.loads(out.stdout)


def test_every_registered_name_has_a_distinct_spelling():
    """⚠ **登録してある選び方・作り方・学習器は全部、綴りが決まっていて、どの 2 つも重ならない。**

    ⚠ 検知器を足して綴りを書き忘れると、ここで落ちる（`@register` の足し忘れと同じ形で先に止める）。
    """
    reg = _registered()
    methods = {catalog.canonical(n)[1] for kind in ("selector", "transform", "detector") for n in reg[kind]}
    methods |= {"常に上（ドリフト）", "直前リターンの符号"}
    slugs = {m: names.method_slug(m) for m in methods}
    assert len(set(slugs.values())) == len(slugs), slugs
    assert all(TOKEN.match(s) for s in slugs.values())
    learners = {m: names.learner_slug(m) for m in [*reg["model"], "—"]}
    assert len(set(learners.values())) == len(learners), learners
    assert all(TOKEN.match(s) for s in learners.values())


def test_names_are_one_to_one_with_the_real_ledger():
    """⚠ **台帳の全行（本体 ＋ leak 対照）で、鍵が違えば名前も違う・鍵が同じなら名前も同じ。**"""
    d = catalog.ledger()
    for rows in (d["rows"], d["leak"]):
        assert rows, "runs/ が空（台帳の行が無い）"
        trial = {}
        model = {}
        for r in rows:
            key = tuple(r[k] for k in catalog.KEY)
            trial.setdefault(names.trial_name(r), set()).add(key)
            model.setdefault(names.model_name(r), set()).add(key[:-1])
        assert all(len(v) == 1 for v in trial.values()), [k for k, v in trial.items() if len(v) > 1]
        assert all(len(v) == 1 for v in model.values()), [k for k, v in model.items() if len(v) > 1]
        assert len(trial) == len({tuple(r[k] for k in catalog.KEY) for r in rows})


def test_model_key_is_the_ledger_key_without_theta():
    """⚠ **予測モデル ＝ 台帳の鍵から θ を除いたもの**（θ はトレーダーの側。live-trading.md §0-1）。"""
    assert names.MODEL_KEY + ("閾値",) == catalog.KEY
