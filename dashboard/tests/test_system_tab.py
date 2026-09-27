"""vibeboard の「システム説明」タブ（`dashboard/systemview.py`・`dashboard/system.toml`）の検査。仕様は dashboard.md §18。

見るもの: 本文はやさしい言葉（⚠ `detail*` ＝ 「詳しく（用語あり）」の囲みは検査の外。⚠ 囲みは畳まない ＝ `<details>` にしない）／ 設定の数字と `$` が本文に無い ／
⚠ **全体像 ＋ パートごとの詳細**（2026-09-26 の利用者の決定。ページは overview → model → trader → live → names。各モデル・各トレーダーの説明はしない ＝
トレーダーのしくみの本文に呼び名・識別名・くらべる文が無い）／ 図は主張つき・箱 12 個以内（流れ図・期間の図・入れ子の図）／
リンク先が在る ／ ページの描き方（段・図・表・型ごとの段・台帳の合計）は最小の置き場で ／ TOML と台帳しか開かない ／ 先頭のタブ ／ 経路 ／ 白地。
"""

import builtins
import json
import re
import threading
import tomllib
from pathlib import Path

import pytest

import figures
import modelview
import systemview
import traderview
import vibetab
from tests.test_traders_tab import COMPARING, FORBIDDEN, _get, _walk
from tests.test_traders_tab import paths as _trader_paths  # noqa: F401（最小の置き場の fixture を借りる）

REPO_ROOT = traderview.REPO_ROOT
REAL = Path(traderview.DASHBOARD_DIR) / "system.toml"
# 用語を使ってよい欄（画面では「詳しく」の囲みとリンク先）。ほかは全部、やさしい言葉の検査を受ける
DETAIL_KEYS = ("detail", "detail_points", "detail_table", "detail_links", "links")
# パートのページ（2026-09-26 に予測モデルのタブから戻し、同日に "trader" を足した。system.toml の [[page]]）
PART_PAGES = ("model", "trader", "live", "names")
PAGES = ("overview", *PART_PAGES)


def _real() -> dict:
    return tomllib.loads(REAL.read_text(encoding="utf-8"))


def _plain(doc: dict) -> list[tuple[str, str]]:
    out = _walk("common", doc.get("common") or {})
    for p in doc.get("page", []):
        out += _walk(p["id"], {k: v for k, v in p.items() if k not in ("id", "section")})
        for i, sec in enumerate(p.get("section") or []):
            body = {k: v for k, v in sec.items() if k not in DETAIL_KEYS}
            body["link_labels"] = [x.get("label") for x in sec.get("links") or []]
            out += _walk(f"{p['id']}[{i}]", body)
    return [(where, text) for where, text in out if not where.endswith(".kind")]      # 図の種類の鍵（folds・out）は文ではない


# ---------------------------------------------------------------- 本物の言葉の正本


def test_plain_text_uses_plain_language_and_no_config_numbers():
    texts = _plain(_real())
    hits = [(where, w) for where, text in texts for w in FORBIDDEN if w.lower() in text.lower()]
    assert not hits, f"本文はやさしい言葉で。用語は detail（詳しく）へ: {hits}"
    bad = [(where, m.group(0)) for where, text in texts
           for m in re.finditer(r"\$\s?\d|(?<!\d)(?:63|48|55|45)(?!\d)|\d\s*bp", text)]
    assert not bad, f"実売買の設定の数字・細かい成績の数字は detail へ: {bad}"


def test_the_model_output_is_called_output_score():
    """本文では「点」と裸の「スコア」を使わず「出力スコア」（test_traders_tab.py の同名のテストと同じ決まり）。"""
    texts = _plain(_real())
    assert not [(where, text) for where, text in texts if "点" in text]
    assert not [(where, text) for where, text in texts if re.search(r"(?<!出力)スコア", text)]


def test_pages_are_overview_then_parts():
    """全体像 → パートごとの詳細（2026-09-26 の利用者の決定）。しくみのページは models.toml に無い。"""
    pages = [p["id"] for p in _real()["page"]]
    assert pages == list(PAGES)
    mdoc = tomllib.loads((Path(traderview.DASHBOARD_DIR) / "models.toml").read_text(encoding="utf-8"))
    assert not mdoc.get("page")
    for p in _real()["page"]:
        assert p.get("lead") and all(s.get("title") for s in p["section"]), p["id"]
    # id はモデルのページの id と重ねない（URL の #system/<id>）・一覧の id でもない
    model_ids = {m["id"] for m in mdoc["model"]}
    assert not set(pages) & model_ids and modelview.LIST_ID not in pages and len(set(pages)) == len(pages)


def test_part_pages_are_deep():
    """予測モデルのしくみ ／ トレーダーのしくみ ／ 実際の売買 は手厚く（前書き・図が 1 つ以上・どの段にも「詳しく」か型ごとのしくみ）＋ 名前と識別名は全段に「詳しく」。"""
    pages = {p["id"]: p for p in _real()["page"]}
    for i in ("model", "trader", "live"):
        assert pages[i].get("lead") and sum(1 for sec in pages[i]["section"] if sec.get("figure")) >= 1, i
        for sec in pages[i]["section"]:
            assert sec.get("title") and (sec.get("detail") or sec.get("models")), (i, sec.get("title"))
    assert pages["names"].get("lead") and all(sec.get("detail") for sec in pages["names"]["section"])
    # 予測モデルのしくみ ＝ 作る（5 段・型ごとのしくみ）→ 確かめる（期間の図・検証結果一覧の合計）の 1 ページ（2026-09-26「1 と 2 はどちらもモデルの話」）
    model = pages["model"]["section"]
    assert any(sec.get("models") is True for sec in model) and any(sec.get("ledger") is True for sec in model)
    kinds = [sec["figure"].get("kind", "flow") for sec in model if sec.get("figure")]
    assert "flow" in kinds and "folds" in kinds
    # 実際の売買 ＝ モデルの話の段 ＋ 守っていること（概要から移した）
    titles = [sec["title"] for sec in pages["live"]["section"]]
    assert titles[0].startswith("トレーダー ＝ モデル ＋ 売買基準値 ＋ 予算") and titles[-1] == "守っていること"
    assert "1 日の流れ" in titles and "出力スコアから注文へ" in titles and "出力スコアの読み方" in titles


def test_trader_page_is_about_settings_not_people():
    """トレーダーのしくみ（2026-09-26。Step 4）＝ 共通のしくみと設定項目の意味だけ。本文（詳しくの外）に 呼び名・識別名 T1〜・
    ほかの人とくらべる言い方が無い（各トレーダーの説明はしない ＝ 1 人ずつはトレーダーのタブ）／ 4 つの集合は入れ子の図 ＋ 表 ／ 設定項目は表 ＋ 詳しくの表。"""
    page = next(p for p in _real()["page"] if p["id"] == "trader")
    texts = [(w, t) for w, t in _plain({"page": [page]}) if not w.startswith("common")]
    hits = [(where, pat) for where, text in texts for pat in COMPARING if re.search(pat, text)]
    assert not hits, f"トレーダーのしくみの本文に人の名前・くらべる文を書かない: {hits}"
    assert not [(w, t) for w, t in texts if re.search(r"\bT\d\b", t)], "識別名は詳しくにだけ"
    secs = {s["title"]: s for s in page["section"]}
    sets = next(s for t, s in secs.items() if "4 つの集合" in t)
    assert sets["figure"].get("kind") == "nest" and sets.get("table") and len(sets["figure"]["sets"]) == 4
    assert [x["t"] for x in sets["figure"]["sets"]] == ["材料に入れるもの", "学ぶ株", "出力スコアを出す株", "売買する株"]   # dashboard.md §18-2 の 4 つの名前
    items = next(s for t, s in secs.items() if "設定項目" in t)
    assert items.get("table") and items.get("detail_table")
    heads = [s["title"] for s in page["section"]]
    assert heads[0].startswith("トレーダー ＝ モデル ＋ 売買基準値 ＋ 予算") and any("規模" in h for h in heads) and any("停止" in h for h in heads)
    assert any("新しい人" in h for h in heads) and heads[-1].startswith("設定項目")


def test_figures_have_a_claim_and_few_nodes():
    """1 図 1 主張・箱は 12 個以内（CLAUDE.md の図の原則）。models.toml の型ごとの図も同じ。"""
    mdoc = tomllib.loads((Path(traderview.DASHBOARD_DIR) / "models.toml").read_text(encoding="utf-8"))
    figs = [(p["id"], s["figure"]) for doc in (_real(), mdoc) for p in doc.get("page", []) for s in p.get("section") or [] if s.get("figure")]
    assert figs
    for where, fig in figs:
        assert fig.get("claim"), where
        if fig.get("kind", "flow") == "flow":
            assert 2 <= len(fig["steps"]) <= systemview.MAX_NODES and all(x.get("t") for x in fig["steps"]), where
        elif fig.get("kind") == "nest":
            assert 2 <= len(fig["sets"]) <= systemview.MAX_NODES and all(x.get("t") for x in fig["sets"]), where
    models = mdoc["model"]
    paths = traderview.TraderPaths.default()
    live = modelview.load(paths)["users"]
    for m in models:
        if m["id"] in live:
            assert 2 <= len(m.get("flow") or []) <= systemview.MAX_NODES, m["id"]      # いま使っている型には図がある


def test_links_point_at_real_things():
    ids = {p["id"] for p in _real()["page"]}
    model_ids = {i["id"] for i in modelview.sidebar(traderview.TraderPaths.default())["items"]}
    for p in _real()["page"]:
        for s in p.get("section") or []:
            for link in [*(s.get("links") or []), *(s.get("detail_links") or [])]:
                assert link.get("label"), (p["id"], link)
                if link.get("doc"):
                    assert (REPO_ROOT / link["doc"]).is_file(), link
                else:
                    assert link.get("tab") in systemview.TAB_URLS, link
                    if link["tab"] == "system":
                        assert link.get("item") in ids, link
                    if link["tab"] == "models" and link.get("item"):
                        assert link["item"] in model_ids, link


def test_real_overview_is_the_entrance_to_the_parts():
    paths = traderview.TraderPaths.default()
    assert [i["id"] for i in systemview.sidebar(paths)["items"]] == list(PAGES)
    overview = systemview.body(paths, "overview")
    assert "<svg" in overview and "class='big'" not in overview and "<table" not in overview     # 全体は簡単に（結論の数字・表は置かない ＝ 利用者の指示）
    for page in PART_PAGES:                                                                  # 各パートへの入口
        assert f"<a href='/#system/{page}' target='_top'>" in overview, page
    assert "class='legend'" in overview and "予測モデル" in overview                        # 流れの図はパートで塗り分け・凡例つき
    assert "<a href='/#models/all' target='_top'>" in overview and "<a href='/#traders' target='_top'>" in overview
    assert "/#models/build" not in overview and "/#models/live" not in overview


def test_real_part_pages_render():
    """作る ＝ 型ごとのしくみは [[model]] から ／ 確かめる ＝ 検証結果一覧の合計は ledger.md から ／ 名前 ＝ 予測モデル名 ／ モデルのページからここへ飛べる。"""
    paths = traderview.TraderPaths.default()
    data = modelview.load(paths)
    model = systemview.body(paths, "model")
    for m in data["models"]:
        if m["id"] in data["users"]:
            assert m["label"] in model and f"/#models/{m['id']}" in model                   # 型ごとのしくみは [[model]] から
    totals = systemview.ledger_totals(paths)
    assert totals and f"<b>{totals['rows']}</b>" in model                                    # 検証結果一覧の合計は ledger.md から
    live = systemview.body(paths, "live")
    assert "守っていること</h2>" in live and "売買履歴" in live and "帳面" not in live          # O8: 言葉の表（帳面 → 売買履歴）
    assert "<a href='/#system/trader' target='_top'>" in live and "<a href='/#system/trader' target='_top'>" in model   # 実際の売買・4 つの集合 → トレーダーのしくみ
    trader = systemview.body(paths, "trader")
    assert trader.count("<g class='set'>") == 4 and "<table" in trader and "class='legend'" in trader   # 4 つの集合の入れ子の図 ＋ 表
    assert "<a href='/#traders' target='_top'>" in trader and "<a href='/#system/live' target='_top'>" in trader
    assert "own-seq.t3-quant60.ridge.shared" in systemview.body(paths, "names")              # 予測モデル名（rules.md 10-2）
    assert "ほかのページ" in model and "<a href='/#system/live' target='_top'>" in model
    first = modelview.body(paths, data["models"][0]["id"])
    assert "<a href='/#system/model' target='_top'>" in first and "/#models/build" not in first   # モデルのページから共通の流れへ
    assert [i["id"] for i in modelview.sidebar(paths)["items"]][:1] == [modelview.LIST_ID]
    assert not {"build", "verify"} & {i["id"] for i in modelview.sidebar(paths)["items"]}   # 予測モデルのタブにしくみの束は無い


def test_system_tab_is_first_in_vibeboard_config():
    tabs = json.loads((REPO_ROOT / "vibeboard.config.json").read_text(encoding="utf-8"))["customTabs"]
    assert tabs[0]["name"] == "system" and tabs[0]["baseUrl"].endswith(":3015/system")
    assert [t["name"] for t in tabs[:3]] == ["system", "models", "traders"]             # 並び ＝ システム説明 → 予測モデル → トレーダー（利用者の指示 2026-09-20）
    assert tabs[0]["label"] == "システム説明" and _real()["common"]["detail_label"].startswith("詳しく")     # 利用者の指示 2026-09-20
    assert not any(":3012" in t["baseUrl"] for t in tabs)                              # 管理画面を baseUrl にしない


# ---------------------------------------------------------------- 最小の置き場

SYSTEM = '''
[common]
detail_label = "詳しく"
other_pages = "ほかのページ"
no_live_models = "モデルは居ない"
ledger_rows = "試した形"
ledger_adopt = "採る"
ledger_hold = "保留"
ledger_drop = "落とす"
ledger_note = "台帳の日 {date}"

[[page]]
id = "one"
label = "ページ 1"
sub = "ひとこと"
title = "ページ 1 の題"
lead = "前書き <1>"

[[page.section]]
title = "段 A"
text = ["本文 A"]
points = ["点 A"]
after = ["図のあとの文"]
note = "注意の文"
links = [{ label = "次へ", tab = "system", item = "two" }, { label = "人のタブ", tab = "traders" }, { label = "知らないタブ", tab = "nope" }]
detail = ["用語の文 Ridge"]
detail_points = ["用語の点"]
detail_links = [{ label = "記録", doc = "docs/specs/experiments/x.md" }]

[page.section.figure]
claim = "図の主張"
per_row = 2
steps = [{ t = "箱 1", s = "小さい字" }, { t = "箱 2" }, { t = "箱 <3>", kind = "out" }]

[page.section.table]
head = ["列 1", "列 2"]
rows = [["a", "b"]]

[page.section.detail_table]
head = ["用語の列"]
rows = [["用語の表"]]

[[page.section]]
title = "段 B"
models = true
ledger = true

[[page.section]]
title = "段 C"

[page.section.figure]
kind = "folds"
claim = "期間の図"
n = 3
row = "試し"
learn = "学ぶ"
test = "答え合わせ"

[[page.section]]
title = "段 D"

[page.section.figure]
kind = "nest"
claim = "入れ子の図"
parts = [{ id = "p", label = "パート <P>" }, { id = "q", label = "パート Q" }, { id = "r", label = "使わないパート" }]
sets = [{ t = "外 <1>", s = "小さい字", part = "p" }, { t = "中", part = "p" }, { t = "内", kind = "out", part = "q" }]

[[page]]
id = "two"
label = "ページ 2"

[[page]]
id = "Bad Id"
label = "鍵がおかしい"
'''

LEDGER = "# 台帳\\n\\n生成日 2026-01-02 ／ 入口\\n\\n⚠ **試行は 1,234 行（手法）＋ 9 行（基準線）。** ⚠ **「採る」は 0 件。** 落とす 1,000 行 ／ 保留 234 行。\\n".replace("\\n", "\n")


@pytest.fixture()
def paths(_trader_paths, tmp_path):
    system = tmp_path / "system.toml"
    system.write_text(SYSTEM, encoding="utf-8")
    ledger = tmp_path / "ledger.md"
    ledger.write_text(LEDGER, encoding="utf-8")
    # 型ごとの図: 最小の置き場の models.toml の 1 本目（TA・TB が使う exp_a）に箱を足す
    text = _trader_paths.models.read_text(encoding="utf-8")
    _trader_paths.models.write_text(text.replace('how = "点の出し方 A"', 'how = "点の出し方 A"\nflow = [{ t = "入れる" }, { t = "出す" }]'), encoding="utf-8")
    return traderview.TraderPaths(words=_trader_paths.words, models=_trader_paths.models, traders_dir=_trader_paths.traders_dir,
                                  universe_dir=_trader_paths.universe_dir, system=system, ledger=ledger)


def test_sidebar_and_unknown_pages(paths):
    assert systemview.sidebar(paths)["items"] == [{"id": "one", "label": "ページ 1", "sub": "ひとこと"},
                                                  {"id": "two", "label": "ページ 2", "sub": ""}]
    for gone in ("Bad Id", "nope", "overview"):
        assert systemview.body(paths, gone) is None


def test_section_layout_plain_then_folded_detail(paths):
    body = systemview.body(paths, "one")
    heads = re.findall(r"<h2><span class='no'>(\d)</span>([^<]+)</h2>", body)
    assert heads == [("1", "段 A"), ("2", "段 B"), ("3", "段 C"), ("4", "段 D")]
    assert "<h1>ページ 1 の題</h1>" in body and "前書き &lt;1&gt;" in body
    a = body.split("段 A</h2>")[1].split("<h2>")[0]
    # 本文 → 図（主張が直前）→ 図のあとの文 → 表 → 注意 → リンク → 「詳しく」の囲み（⚠ 畳まない・開いたままだけ ＝ 利用者の指示 2026-09-20）
    order = ["本文 A", "点 A", "<p class='claim'>図の主張</p><div class='fig'><svg", "図のあとの文", "<th>列 1</th>", "注意の文",
             "<a href='/#system/two' target='_top'>次へ</a>", "<div class='more'><div class='more-h'>詳しく</div>"]
    assert [a.index(x) for x in order] == sorted(a.index(x) for x in order)
    assert "<a href='/#traders' target='_top'>人のタブ</a>" in a and "知らないタブ" not in a
    detail = a.split("<div class='more'>")[1]
    assert all(x in detail for x in ("用語の文 Ridge", "用語の点", "用語の表", "<a href='/#specs/experiments/x.md' target='_top'>記録</a>"))
    assert "用語" not in a.split("<div class='more'>")[0]
    assert "<details" not in body and "<summary" not in body                 # 畳む部品を使わない
    assert "<a href='/#system/two' target='_top'>ページ 2</a>" in body.split("ほかのページ")[1]


def test_flow_figure_nodes_and_escape(paths):
    svg = systemview.flow_svg([{"t": "箱 1", "s": "小さい字"}, {"t": "箱 2"}, {"t": "箱 <3>", "kind": "out"}], per_row=2)
    assert svg.count("<g class='node'>") == 3 and "箱 &lt;3&gt;" in svg and "<3>" not in svg
    assert svg.count("marker-end") == 2                                      # 箱 3 つ ＝ 矢印 2 本（折り返しの 1 本をふくむ）
    many = systemview.flow_svg([{"t": f"箱 {i}"} for i in range(20)])
    assert many.count("<g class='node'>") == systemview.MAX_NODES            # 12 個を超えたぶんは描かない
    assert systemview.flow_svg([]) == "" and systemview.figure_html({"steps": []}) == ""
    long = systemview.flow_svg([{"t": "とても長い見出しの箱がここにあります", "s": "小さい字もとても長くて 1 行には入りきらない長さです"}])
    assert long.count("<text") >= 4                                          # 箱の中で折り返す
    # パートで塗り分け（2026-09-26）: `part` の箱はパートの色・凡例は使ったパートだけ・文字は TOML から
    parts = [{"id": "p", "label": "パート <P>"}, {"id": "q", "label": "使わないパート"}]
    colored = systemview.flow_svg([{"t": "箱 1", "part": "p"}, {"t": "箱 2"}, {"t": "箱 3", "kind": "out", "part": "p"}], parts=parts)
    assert colored.count("class='legend'") == 1 and "パート &lt;P&gt;" in colored and "使わないパート" not in colored
    assert colored.count(f"fill='{figures.PART_FILLS[0][0]}'") == 3           # 箱 1・箱 3 ＋ 凡例。箱 2 は今までどおり
    assert "class='legend'" not in svg                                        # parts が無ければ凡例も無い


def test_folds_figure(paths):
    body = systemview.body(paths, "one")
    c = body.split("段 C</h2>")[1]
    assert "<p class='claim'>期間の図</p>" in c and c.count("class='learn'") == 3 and c.count("class='test'") == 3
    widths = [float(w) for w in re.findall(r"class='learn' x='\d+' y='\d+' width='([\d.]+)'", c)]
    assert widths == sorted(widths) and widths[0] < widths[-1]               # 学ぶ期間はだんだん長くなる


def test_nest_figure(paths):
    """入れ子の図（2026-09-26。4 つの集合の絞り込み）: 外から内へ四角が小さくなる・箱の文字は TOML・パートの塗りと凡例は流れ図と同じ・12 個まで。"""
    body = systemview.body(paths, "one")
    d = body.split("段 D</h2>")[1]
    assert "<p class='claim'>入れ子の図</p><div class='fig'><svg" in d and d.count("<g class='set'>") == 3
    assert "外 &lt;1&gt;" in d and "<1>" not in d and "小さい字" in d
    rects = [(int(x), int(y), int(w), int(h)) for x, y, w, h in re.findall(r"<g class='set'><rect x='(\d+)' y='(\d+)' width='(\d+)' height='(\d+)'", d)]
    assert [r[0] for r in rects] == sorted(r[0] for r in rects) and [r[2] for r in rects] == sorted((r[2] for r in rects), reverse=True)   # 内側ほど右・細い
    assert all(a[1] < b[1] and a[1] + a[3] > b[1] + b[3] for a, b in zip(rects, rects[1:]))                                                # 内側は外側の中に収まる
    assert d.count("class='legend'") == 2 and "パート &lt;P&gt;" in d and "パート Q" in d and "使わないパート" not in d
    assert d.count(f"fill='{figures.PART_FILLS[0][0]}'") == 3 and d.count(f"fill='{figures.PART_FILLS[1][0]}'") == 2   # 外・中 ＋ 凡例 ／ 内 ＋ 凡例
    assert figures.nest_svg({"sets": [{"t": f"箱 {i}"} for i in range(20)]}).count("<g class='set'>") == figures.MAX_NODES
    assert figures.nest_svg({"sets": []}) == "" and figures.figure_html({"kind": "nest", "sets": []}) == ""
    assert "class='legend'" not in figures.nest_svg({"sets": [{"t": "外"}, {"t": "内"}]})                   # parts が無ければ凡例も無い


def test_model_types_come_from_models_toml(paths):
    b = systemview.body(paths, "one").split("段 B</h2>")[1].split("<h2>")[0]
    assert "&lt;b&gt;形&lt;/b&gt;を読む型" in b and "モデル A のひとこと" in b and "点の出し方 A" in b
    assert b.count("<g class='node'>") == 2 and "<a href='/#models/a-type' target='_top'>モデルのページ</a>" in b
    assert "モデル B のひとこと" not in b                                     # id の無いモデルは「予測モデル」タブに居ないので出さない
    (paths.traders_dir / "TA.toml").unlink()
    (paths.traders_dir / "candidates" / "notional" / "TB.toml").unlink()
    assert "モデルは居ない" in systemview.body(paths, "one")


def test_ledger_totals_are_read_not_written(paths):
    assert systemview.ledger_totals(paths) == {"date": "2026-01-02", "rows": "1,234", "adopt": "0", "drop": "1,000", "hold": "234"}
    b = systemview.body(paths, "one")
    assert "<b>1,234</b><span>試した形</span>" in b and "<b>234</b><span>保留</span>" in b and "台帳の日 2026-01-02" in b
    paths.ledger.write_text("# 形の変わった台帳\n", encoding="utf-8")
    assert systemview.ledger_totals(paths) is None and "試した形" not in systemview.body(paths, "one")     # 読めなければ出さない
    paths.ledger.unlink()
    assert systemview.ledger_totals(paths) is None


def test_light_background_is_fixed():
    assert "color-scheme: light;" in systemview.CSS and "background: #ffffff" in systemview.CSS
    assert "prefers-color-scheme" not in systemview.CSS


def test_only_opens_toml_and_ledger(paths, monkeypatch):
    opened: list[str] = []
    real_open = builtins.open

    def spy(file, *a, **kw):
        opened.append(str(file))
        return real_open(file, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    for item in ("one", "two"):
        systemview.body(paths, item)
    systemview.sidebar(paths)
    assert opened and all(p.endswith(".toml") or p == str(paths.ledger) for p in opened), opened
    assert not [p for p in opened if re.search(r"(^|/)(out|state|sim|runs)(/|$)|\.env|jsonl", p)], opened


# ---------------------------------------------------------------- 経路


@pytest.fixture()
def server(tmp_path, paths):
    runs = tmp_path / "runs"
    runs.mkdir()
    srv = vibetab.ThreadingHTTPServer(
        ("127.0.0.1", 0), vibetab.make_handler(runs, vibetab.ExpPaths(tmp_path / "exp"), trader_paths=paths))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_http_routes(server):
    status, body, _ = _get(f"{server}/system/api/sidebar")
    assert status == 200 and [i["id"] for i in json.loads(body)["items"]] == ["one", "two"]
    status, body, headers = _get(f"{server}/system/view?item=one")
    assert status == 200 and "段 A" in body and "color-scheme: light;" in body
    assert headers.get("Content-Security-Policy") == "frame-ancestors 'self'"
    assert _get(f"{server}/system/view?item=nope")[0] == 404
    assert _get(f"{server}/system")[0] == 200


def test_fingerprint_sees_system_words_ledger_and_models(paths):
    fp = systemview.fingerprint(paths)
    assert all(str(f) in fp for f in (paths.system, paths.ledger, paths.models))
