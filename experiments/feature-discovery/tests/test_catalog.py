"""⚠ **台帳を作る側そのものを検査する。**

台帳は spec の表・registry・`runs/` を突き合わせた**生成物**である。
⚠ **突き合わせが静かに壊れると、「試していない」が「効かない」に化ける。**
だから ⚠ **わざと壊した spec を食わせて、ちゃんと止まることを確かめる。**
"""

from __future__ import annotations

import textwrap

import pytest

from ail import catalog

SPEC_HEAD = """\
## 2. 手法のカタログ

### F1. フィルタ型 — モデルの外で選ぶ（2）

| ID | 手法 | 量 | 相関 | 検定 | 拠 | 見どころ |
| --- | --- | --- | --- | --- | --- | --- |
| F1-1 | 相関（Pearson） | 低 | ✕ | 無 | 強 | ⚠ **非線形を見ない** |
| F1-2 | 相互情報量 | 中 | ⚠ | 無 | 強 | 非線形を見る |

### F2. ラッパー型 — モデルを回す（1）

| ID | 手法 | 量 | 相関 | 検定 | 拠 | 見どころ |
| --- | --- | --- | --- | --- | --- | --- |
| F2-3 | Boruta | 高 | ○ | **有** | 強 | 影の特徴量と比べる |

## 3. 検証

### 3-3. ⚠ 結果

| 手法 | 選んだ本数 | 的中率 | IC | 粗利 bp | 純利 bp（c=5） |
| --- | ---: | ---: | ---: | ---: | ---: |
| F1-1 相関 | 8.0 | 0.4934 | −0.014 | **−3.37** | −8.37 |
| **全部使う（基準）** | 35.0 | 0.4958 | ＋0.006 | −1.54 | −6.54 |
| ⚠ **基準 常に上（ドリフト）** | 0 | 0.5156 | — | **＋3.16** | −1.84 |

### 3-4. 限界

| # | 限界 |
| ---: | --- |
| 1 | ⚠ **F1-1 のような行が、結果の表にも出てくる** |
"""


@pytest.fixture
def spec(tmp_path):
    p = tmp_path / "spec.md"
    p.write_text(SPEC_HEAD, encoding="utf-8")
    return str(p)


# --- カタログの読み取り -------------------------------------------------

def test_entries_reads_only_the_catalog_section(spec):
    """⚠ **§3 の結果の表に混ざる `F1-1` を拾ってはいけない**（2026-09-08 に踏んだ）。"""
    e = catalog.entries(spec)
    assert [x["ID"] for x in e] == ["F1-1", "F1-2", "F2-3"]
    assert e[0]["手法"] == "相関（Pearson）" and e[0]["系統"] == "F1"
    assert e[2]["拠"] == "強"


def test_entries_stops_when_the_count_disagrees(tmp_path):
    """⚠ **書式が変わると黙って減る。** 件数が合わなければ止まらなければ意味が無い。"""
    p = tmp_path / "spec.md"
    p.write_text(SPEC_HEAD.replace("| F1-2 | 相互情報量 | 中 | ⚠ | 無 | 強 | 非線形を見る |", ""),
                 encoding="utf-8")
    with pytest.raises(SystemExit, match="件数が見出しと合わない"):
        catalog.entries(str(p))


def test_real_spec_has_25_methods_in_5_families():
    """⚠ **本物の spec を読む。** 25 件・7/4/6/4/4 から動いたら気づく。"""
    e = catalog.entries()
    assert len(e) == 25
    assert catalog.family_counts() == {"F1": 7, "F2": 4, "F3": 6, "F4": 4, "F5": 4}
    assert len({x["ID"] for x in e}) == 25


def test_every_implemented_selector_is_in_the_catalog():
    """⚠ **registry に `F9-9` のような幽霊 ID があれば、台帳から静かに消える。**"""
    ids = {x["ID"] for x in catalog.entries()}
    assert set(catalog.implemented()) <= ids
    assert set(catalog.implemented()) == {"F1-1", "F1-2", "F1-3", "F1-5",
                                          "F2-3", "F3-1", "F3-2", "F3-3", "F3-5"}


# --- 数の読み取り -------------------------------------------------------

@pytest.mark.parametrize("cell,want", [
    ("＋0.006", 0.006),          # ⚠ 全角のプラス
    ("−1.54", -1.54),            # ⚠ U+2212（ハイフンではない）
    ("**−3.37**", -3.37),        # 強調つき
    ("⚠ **＋4.77**", 4.77),
    ("0.5156", 0.5156),
    ("—", None),                 # ⚠ 「測っていない」であって 0 ではない
    ("", None),
])
def test_numbers_survive_the_full_width_signs(cell, want):
    """⚠ **符号を取り違えると結論が反転する。**"""
    assert catalog._num(cell) == want


# --- 旧配線の表 ---------------------------------------------------------

def test_legacy_table_is_read_from_the_spec(spec):
    decl = {"id": "t", "heading": "### 3-3.", "spec": spec, "granularity": "1 分足",
            "bar_minutes": 1.0, "horizon": 390, "layer": "raw", "feature_layers": ["own"]}
    rows = catalog.legacy_trials(decl)
    assert len(rows) == 3
    r = {x["手法名"]: x for x in rows}
    assert r["F1-1 相関"]["純利bp"] == -8.37
    assert r["F1-1 相関"]["IC"] == -0.014
    assert r["全部使う（基準）"]["粗利bp"] == -1.54
    assert r["基準 常に上（ドリフト）"]["IC"] is None      # ⚠ 「—」は 0 にしない
    assert rows[0]["地平"] == "390 本（1 取引日）"
    assert rows[0]["fold"] is None                        # 生データが残っていない


def test_legacy_declaration_stops_on_a_missing_heading(spec):
    with pytest.raises(SystemExit, match="見出し"):
        catalog.legacy_trials({"id": "t", "heading": "### 9-9.", "spec": spec})


# --- 照合の鍵 -----------------------------------------------------------

@pytest.mark.parametrize("name,want", [
    ("F1-5 検定+FDR", ("F1-5", "F1-5")),
    ("F1-5 単変量検定 ＋ FDR", ("F1-5", "F1-5")),       # ⚠ 表記が違っても同じ鍵
    ("基準 常に上（ドリフト）", (None, "常に上（ドリフト）")),
    ("全部使う（基準）", (None, "全部使う（基準）")),
])
def test_canonical_joins_by_id_not_by_name(name, want):
    """⚠ **名前で照合すると、同じ試行が 2 行に割れる**（2026-09-08 に踏んだ）。"""
    assert catalog.canonical(name) == want


# --- 判定 ---------------------------------------------------------------

BASES = {"全部使う（基準）", "乱択（基準）", "常に上（ドリフト）", "直前リターンの符号"}


def row(**kw) -> dict:
    base = {"手法名": "F1-1 相関", "粒度": "日足", "層": "adjusted",
            "純利bp": -1.0, "粗利bp": 4.0, "fold": "0/5 −−−−−"}
    return {**base, **kw}


@pytest.mark.parametrize("r,verdict,inside", [
    (row(純利bp=1.0, fold="5/5 ＋＋＋＋＋"), "採る", "デフレーテッド SR"),
    (row(純利bp=1.0, fold="3/5 ＋＋−−＋"), "保留", "fold の符号"),
    (row(純利bp=-0.5, 粗利bp=4.5), "落とす", "X2"),
    (row(純利bp=-6.0, 粗利bp=-1.0), "落とす", "X9"),
    (row(粒度="日足", 層="raw"), "保留", "無効・要再測"),
    (row(粒度="1 分足", 層="raw", 純利bp=-6.2, 粗利bp=-1.2), "落とす", "X9"),
    (row(手法名="基準 常に上（ドリフト）", 層="adjusted"), "基準", "回転しない"),
    (row(手法名="全部使う（基準）", 層="adjusted"), "基準", "採否の対象ではない"),
])
def test_judge_follows_the_written_rules(r, verdict, inside):
    """⚠ **判定は数字から機械的に決める。** 手で「良さそう」とは書かない。"""
    got, why = catalog.judge(r, BASES)
    assert got == verdict
    assert inside in why


def test_daily_raw_is_held_even_when_the_number_is_good():
    """⚠ **分割調整の誤りを含む日足は、良い数字でも採らない**（§6-3）。"""
    got, why = catalog.judge(row(粒度="日足", 層="raw", 純利bp=9.9, fold="5/5 ＋＋＋＋＋"), BASES)
    assert got == "保留" and "無効" in why


def test_one_minute_raw_is_not_held():
    """⚠ **目盛りの誤りは日足にしか無い。** 1 分足まで保留にすると結論が薄まる。"""
    got, _ = catalog.judge(row(粒度="1 分足", 層="raw"), BASES)
    assert got == "落とす"


# --- 台帳の組み立て -----------------------------------------------------

def test_leak_rows_never_enter_the_ledger():
    """⚠ **的中率 99% の行が本体に混ざると、台帳全体が嘘になる。**"""
    d = catalog.ledger()
    assert all(not r["leak"] for r in d["rows"])
    assert d["leak"], "先読みの検査の実行が runs/ に無い（配線の検査が回っていない）"
    assert max(r["的中率"] for r in d["rows"]) < 0.6
    assert max(r["的中率"] for r in d["leak"]) > 0.9


def test_ledger_lists_every_catalog_method_exactly_once():
    """⚠ **25 件が「試した」か「未実施」のどちらかに必ず 1 度は出る。**"""
    d = catalog.ledger()
    tried = {r["ID"] for r in d["rows"] if r["ID"]}
    not_tried = {c["ID"] for c in d["not_tried"]}
    assert tried & not_tried == set()
    assert tried | not_tried == {c["ID"] for c in d["catalog"]}


def test_every_unimplemented_method_says_what_it_needs():
    """⚠ **未実施を空白にしない。** 「次の一手」が無い行は台帳として役に立たない。"""
    d = catalog.ledger()
    missing = [c["ID"] for c in d["not_tried"] if not c["次の一手"]]
    assert not missing, f"config/catalog_notes.toml に無い: {missing}"


def test_ledger_markdown_renders():
    from cli import ledger
    text = ledger.build()
    assert text.startswith("# 試した分析手法の台帳")
    assert "## 3. まだ試していない手法" in text
    assert text.count("```mermaid") >= 2          # ⚠ 図を最低 1 枚（CLAUDE.md）
    assert "0/5 −−−−−" in text                    # fold の符号が出ている


def test_unimplemented_rows_are_ordered_by_effort():
    """⚠ **上から読んで次の一手が決まらなければ、台帳は一覧として役に立たない。**"""
    d = catalog.ledger()
    order = [catalog.COST_ORDER.index(c["手間"]) for c in d["not_tried"]]
    assert order == sorted(order)
    assert d["not_tried"][0]["手間"] == "小"
    assert d["not_tried"][-1]["手間"] == "見送り"


def test_skipped_is_distinguished_from_not_yet_tried():
    """⚠ **「まだ試していない」と「試さないと決めた」は別の情報である。**"""
    d = catalog.ledger()
    states = {c["判定"] for c in d["not_tried"]}
    assert states == {"未実施", "⚠ 見送り"}
    for c in d["not_tried"]:
        if c["判定"] == "⚠ 見送り":
            assert "見送り" in c["次の一手"], f"{c['ID']}: 見送りの理由が書いていない"


# --- 閉じる注記（rules.md 14 章） ----------------------------------------

def _held_row(**kw):
    base = {"鍵": "F1-2", "モデル": "Ridge", "特徴量の層": "own ex", "検証方式": "毎日往復",
            "閾値": "—", "層": "adjusted", "形式": "共通", "粒度": "日足",
            "判定": "保留", "理由": "⚠ 平均だけ正"}
    return {**base, **kw}


def test_closed_note_lands_on_the_row_and_keeps_the_verdict():
    """⚠ **注記は理由列に足すだけ。判定は変えない**（rules.md 14 章・13-9 の 2）。"""
    rows = [_held_row(), _held_row(特徴量の層="own", 判定="落とす")]
    catalog._apply_closed(rows, [{"key": "F1-2", "layers": "own ex", "note": "閉じる注記"}])
    assert rows[0]["理由"].endswith("閉じる注記") and rows[0]["閉じる"] is True
    assert rows[0]["判定"] == "保留"
    assert "閉じる注記" not in (rows[1].get("理由") or "")


def test_closed_note_stops_when_nothing_matches():
    """⚠ **黙って空振りさせない。** 0 件一致は書き間違い。"""
    with pytest.raises(SystemExit):
        catalog._apply_closed([_held_row()], [{"key": "F9-9", "note": "x"}])


def test_closed_note_stops_when_ambiguous():
    """2 行以上に当たるなら照合の鍵が足りない。"""
    rows = [_held_row(), _held_row(モデル="LightGBM")]
    with pytest.raises(SystemExit):
        catalog._apply_closed(rows, [{"key": "F1-2", "layers": "own ex", "note": "x"}])


def test_closed_note_refuses_non_held_rows():
    """⚠ **閉じられるのは保留だけ。** 落とす行を閉じるのは設計ミス。"""
    with pytest.raises(SystemExit):
        catalog._apply_closed([_held_row(判定="落とす")], [{"key": "F1-2", "note": "x"}])


def test_closed_note_rejects_unknown_keys_and_missing_note():
    with pytest.raises(SystemExit):
        catalog._apply_closed([_held_row()], [{"kee": "F1-2", "note": "x"}])
    with pytest.raises(SystemExit):
        catalog._apply_closed([_held_row()], [{"key": "F1-2"}])


def test_real_closed_entries_each_hit_exactly_one_held_row():
    """実物の `[[closed]]` が実台帳にちょうど 1 行ずつ当たり、判定を変えない。"""
    d = catalog.ledger()                      # ⚠ 中で _apply_closed が走る（合わなければ止まる）
    closed = [r for r in d["rows"] if r.get("閉じる")]
    assert len(closed) == len(catalog.closed_notes()) == 16
    assert all(r["判定"] == "保留" for r in closed)


def test_ledger_mentions_closed_rows():
    from cli import ledger
    text = ledger.build()
    assert "「閉じる」の注記つき" in text
    assert text.count("閉じる（2026-09-11") >= 16
