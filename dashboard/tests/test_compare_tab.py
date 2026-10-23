"""「モデルとトレーダーの比較」（外部のタブ「実行」の 1 ページ ＝ `compareview.py`・言葉と対応は `compare.toml`。dashboard.md §19）。

⚠ 数字は全部 研究の DB の `ledger_rows` から。小さな DB で画面を、本物の DB（ある機械だけ）で対応の実在を見る。
"""

from __future__ import annotations

import dataclasses
import json
import re
import sqlite3
import threading
from pathlib import Path

import pytest

import compareview
import traderview
import vibetab
from tests.test_models_tab import REAL_DB
from tests.test_traders_tab import _get, _trader, paths as _trader_paths  # noqa: F401（最小の置き場の fixture を借りる）

COMPARE = '''
[common]
title = "くらべる表"
lead = "DB から引いたもの"
caution = ["注意 1", "注意 2"]
section_traders = "1. 人"
section_models = "2. モデル"
section_orphans = "3. 人のいない形"
section_howto = "読み方"
howto = ["読み方 1"]
col_trader = "人"
col_base = "素の形"
col_tr5 = "5 本"
col_trall = "全銘柄"
col_counts = "試した数"
status_settled = "確定"
status_candidate = "候補"
missing = "—"
no_mapping = "対応なし"
not_in_config = "設定に居ない"
closed_mark = "閉"
no_db = "DB が無い"
no_rows_model = "行なし"

[[trader]]
id = "TA"
forms = [{ base = "own.all.ridge.shared@55", shape = "tr5" }]
record = "docs/specs/experiments/x.md"

[[trader]]
id = "TB"
forms = [
  { label = "形 1", base = "own.b.ridge.shared@50", shape = "tr5-600" },
  { label = "形 2", base = "own.b2.ridge.shared@50", shape = "tr5" },
]

[[trader]]
id = "TZ"
forms = [{ base = "own.z.ridge.shared@50", shape = "tr5" }]

[[model]]
id = "a-type"
base = ["own.all.ridge.shared"]
'''

NAMES = '''
[trader]
"5本・$300" = "tr5"
"5本・$600" = "tr5-600"
"全銘柄・全部を買える予算" = "trall"
'''


def _doc(method: str, theta: str, edge, fold: str, run: str = "2026-10-02T00-00-00_x", reason: str = "") -> str:
    d = {"手法名": method, "閾値": theta, "上乗せfold": fold, "実行": run, "理由": reason, "検証方式": "閾値売買"}
    if edge is not None:
        d["上乗せbp"] = edge
    return json.dumps(d, ensure_ascii=False)


def _db(path: Path, rows: list[tuple]) -> Path:
    """実験側 `ail/rundb.py` の `ledger_rows` と同じ列（⚠ 列の順も同じ）。rows ＝ (leak, trial_name, model_name, is_trial, verdict, closed, doc)。"""
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                       "CREATE TABLE ledger_rows (leak INTEGER, trial_name TEXT, model_name TEXT, is_trial INTEGER,"
                       " verdict TEXT, closed INTEGER, first_run TEXT, last_run TEXT, runs TEXT, doc TEXT);")
    conn.executemany("INSERT INTO ledger_rows VALUES (?, ?, ?, ?, ?, ?, '2026-10-02', '2026-10-02', '[]', ?)", rows)
    conn.execute("INSERT INTO meta VALUES ('ledger_built_at', '2026-10-22T10-00-00')")
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def paths(tmp_path: Path, _trader_paths: traderview.TraderPaths) -> traderview.TraderPaths:
    compare = tmp_path / "compare.toml"
    compare.write_text(COMPARE, encoding="utf-8")
    names = tmp_path / "names.toml"
    names.write_text(NAMES, encoding="utf-8")
    # 予測モデルのタブと同じ形の経緯（試した数は DB から数える）
    models = _trader_paths.models
    models.write_text(models.read_text(encoding="utf-8").replace(
        'how = "点の出し方 A"', 'how = "点の出し方 A"\nhistory = [{ when = "2026-09-13", what = "3 通り", names = ["own.all.ridge.shared"], drop = 3 },'
        ' { when = "2026-10-02", what = "人の形", names = ["own.all-tr*.ridge.shared"], hold = 1, aside = "ちがう決まり" }]'), encoding="utf-8")
    rows = [
        (0, "own.all.ridge.shared@55", "own.all.ridge.shared", 1, "落とす", 0, _doc("全部使う（基準）", "55", -540.2, "1/5 −＋−−−")),
        (0, "own.all.ridge.shared@50", "own.all.ridge.shared", 1, "落とす", 0, _doc("全部使う（基準）", "50", -270.9, "2/5 −＋−−＋")),
        (0, "own.all.ridge.shared@60", "own.all.ridge.shared", 1, "落とす", 0, _doc("全部使う（基準）", "60", -943.7, "1/5 ＋−−−−")),
        (0, "own.all-tr5.ridge.shared@55", "own.all-tr5.ridge.shared", 1, "保留", 1,
         _doc("全部使う（基準）〔トレーダー・5本・$300〕", "55", 278.6, "3/5 −＋＋−＋", reason="⚠ **閉じる**（[棚卸し](x.md)）")),
        # TA の全銘柄の行は無い（「—」）。TB の形 1 は 3 つとも、形 2 は素の形だけ
        (0, "own.b.ridge.shared@50", "own.b.ridge.shared", 1, "落とす", 0, _doc("B", "50", -1.5, "0/5 −−−−−")),
        (0, "own.b-tr5-600.ridge.shared@50", "own.b-tr5-600.ridge.shared", 1, "保留", 0, _doc("B〔トレーダー・5本・$600〕", "50", 68.8, "3/5 ＋−＋−＋")),
        (0, "own.b-trall.ridge.shared@50", "own.b-trall.ridge.shared", 1, "落とす", 0, _doc("B〔トレーダー・全銘柄・全部を買える予算〕", "50", -249.7, "0/5 −−−−−")),
        (0, "own.b2.ridge.shared@50", "own.b2.ridge.shared", 1, "落とす", 0, _doc("B2", "50", -9.0, "0/5 −−−−−")),
        # 人のいない形（素 ＋ 5 本 ＋ 全銘柄）と、基準線（is_trial 0）・leak の行（出ない）
        (0, "own.f1-7.ridge.shared@50", "own.f1-7.ridge.shared", 1, "保留", 1, _doc("F1-7 IC の安定性", "50", 728.5, "3/5 ＋＋−−＋")),
        (0, "own.f1-7-tr5.ridge.shared@50", "own.f1-7-tr5.ridge.shared", 1, "保留", 1, _doc("F1-7 IC の安定性〔トレーダー・5本・$300〕", "50", 500.4, "3/5 −＋＋−＋")),
        (0, "own.f1-7-trall.ridge.shared@50", "own.f1-7-trall.ridge.shared", 1, "保留", 1, _doc("F1-7 IC の安定性〔トレーダー・全銘柄・全部を買える予算〕", "50", 422.6, "3/5 ＋＋−−＋")),
        (0, "own.hold-tr5.none.shared@50", "own.hold-tr5.none.shared", 0, "基準", 0, _doc("基準 持ち続ける〔トレーダー・5本・$300〕", "50", 0.0, "0/5 00000")),
        (1, "own.all.ridge.shared@50", "own.all.ridge.shared", 1, "落とす", 0, _doc("全部使う（基準）", "50", 9999.0, "5/5 ＋＋＋＋＋")),
    ]
    return dataclasses.replace(_trader_paths, compare=compare, names=names, research_db=_db(tmp_path / "research.sqlite", rows))


def _cells(body: str, who: str) -> list[str]:
    row = next(r for r in re.findall(r"<tr>(.*?)</tr>", body, flags=re.S) if who in r)
    return [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]


def test_trader_rows_come_from_config_and_db(paths):
    body = compareview.page_body(paths)
    assert "くらべる表" in body and "注意 1" in body and "読み方 1" in body and "2026-10-22T10-00-00" in body
    # TA ＝ 設定の人（θ 55）: 素の形 ／ 5 本（閉じる注記つき）／ 全銘柄は行なし
    ta = _cells(body, "ナマエ（TA）")
    assert ta[1] == "確定" and ta[3] == "55" and ta[4] == "$300 ／ 3"
    assert "−540.2" in ta[5] and "落とす" in ta[5]
    assert "5本・$300" in ta[6] and "+278.6" in ta[6] and "3/5 −＋＋−＋" in ta[6] and "保留閉" in ta[6]
    assert ta[7] == "—"
    assert "title='⚠ 閉じる（棚卸し）'" in body            # 理由の Markdown の飾りを落として印の上に
    assert "title='行なし: own.all-trall.ridge.shared@55'" in body
    # TB ＝ 候補・形が 2 つ ＝ 2 行（形の名前つき）。tr5-600 の綴りで 5 本の行を引く
    rows = [r for r in re.findall(r"<tr>(.*?)</tr>", body, flags=re.S) if "href='/#traders/TB'" in r]
    assert len(rows) == 2 and "形 1" in rows[0] and "形 2" in rows[1]
    tb1 = _cells(body, "形 1")
    assert tb1[1] == "候補" and "+68.8" in tb1[6] and "5本・$600" in tb1[6] and "−249.7" in tb1[7]
    tb2 = _cells(body, "形 2")
    assert "−9.0" in tb2[5] and tb2[6] == "—" and tb2[7] == "—"
    # TD ＝ 設定に居るが対応が無い ／ TZ ＝ 対応はあるが設定に居ない。試験用（test_x）は出ない
    td = _cells(body, ">TD<")
    assert td[5] == td[6] == td[7] == "対応なし"
    tz = _cells(body, ">TZ<")
    assert tz[1] == "設定に居ない" and tz[5] == "—"
    assert "test_x" not in body
    # 記録へのリンク・人とモデルのページへのリンク
    assert "/#specs/experiments/x.md" in body and "href='/#traders/TA'" in body and "href='/#models/a-type'" in body


def test_model_rows_show_thetas_counts_and_best(paths):
    body = compareview.page_body(paths)
    row = _cells(body, "own.all.ridge.shared</code>")
    # モデル ／ 正式な名前 ／ 使う人 ／ 形 ／ θ 50 ／ 55 ／ 60 ／ 試した数 ／ 最大
    assert row[0] == "形を読む型" and "exp_a 手法 A" in row[1]
    assert "ナマエ（TA）" in row[2] and "TB" in row[2] and "TD" in row[2]
    assert "−270.9" in row[4] and "−540.2" in row[5] and "−943.7" in row[6]
    # 試した数は old ／ aside を除くパターン（素の 3 行だけ。leak・基準線は数えない）
    assert row[7].startswith("3 行") and "落とす 3" in row[7]
    # 上乗せが最大の形は素の形の中から（人の形の +278.6 は混ぜない）
    assert "−270.9" in row[8] and "+278.6" not in row[8]
    # base の無いモデル（exp_b）は θ の欄が「—」で、経緯も無いので試した数も「—」
    b = _cells(body, "href='/#models/exp_b'")
    assert b[4] == b[5] == b[6] == "—" and b[7] == "—"


def test_orphan_forms_are_listed_once(paths):
    body = compareview.page_body(paths)
    sec = body.split("3. 人のいない形")[1].split("</table>")[0]      # 3 の表だけ
    assert "F1-7 IC の安定性" in sec and "+728.5" in sec and "+500.4" in sec and "+422.6" in sec
    # 1 行 1 条件（5 本 ／ 全銘柄 の 2 行。素の形は両方の行に出る）
    assert sec.count("<tr>") == 3 and sec.count("+728.5") == 2 and "5本・$300" in sec and "全銘柄・全部を買える予算" in sec
    # 人の表で使った形（TA の own.all・TB の own.b）と基準線の行は出ない
    assert "own.all-tr5" not in sec and "own.b-tr5-600" not in sec and "持ち続ける〔" not in sec


def test_without_db_the_page_still_renders(paths, tmp_path):
    body = compareview.page_body(dataclasses.replace(paths, research_db=tmp_path / "nai.sqlite"))
    assert "DB が無い" in body and "ナマエ（TA）" in body
    cells = _cells(body, "ナマエ（TA）")
    assert cells[5] == cells[6] == cells[7] == "—"
    # names.toml が無くても compare.toml の綴りで動く
    body = compareview.page_body(dataclasses.replace(paths, names=tmp_path / "nai.toml"))
    assert "+68.8" in body


def test_shape_names_are_derived_from_the_base():
    assert compareview.with_shape("own.all.ridge.shared@50", "tr5") == "own.all-tr5.ridge.shared@50"
    assert compareview.with_shape("own-cs-rel-ex.mix3-mean.lgbm.shared~n48@50", "trall") == "own-cs-rel-ex.mix3-mean-trall.lgbm.shared~n48@50"
    assert compareview.with_shape("own-trend.roles-b10-e5.ridge.shared@50", "tr5-600") == "own-trend.roles-b10-e5-tr5-600.ridge.shared@50"
    shapes = ["tr5-600", "trall", "tr5"]
    assert compareview.strip_shape("own-trend.roles-b10-e5-tr5-600.ridge.shared@50", shapes) == ("own-trend.roles-b10-e5.ridge.shared@50", "tr5-600")
    assert compareview.strip_shape("own.all-tr5.ridge.shared@50", shapes) == ("own.all.ridge.shared@50", "tr5")
    assert compareview.strip_shape("own.all.ridge.shared@50", shapes) == ("own.all.ridge.shared@50", None)
    assert compareview.strip_shape("short", shapes) == ("short", None)


def test_sidebar_body_and_fingerprint(paths):
    assert [i["id"] for i in compareview.sidebar(paths)["items"]] == ["all"]
    assert compareview.body(paths, "all") and compareview.body(paths, "") and compareview.body(paths, "nope") is None
    before = compareview.fingerprint(paths)
    assert before["ledger_rows"] == 20261022100000.0 and str(paths.compare) in before
    paths.compare.write_text(COMPARE + "\n# 変えた\n", encoding="utf-8")
    import os
    os.utime(paths.compare, (1_900_000_000, 1_900_000_000))
    assert compareview.fingerprint(paths) != before


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
    """実行タブの目次の 2 番目と、接頭辞 `compare` の両方で同じ中身。"""
    status, body, _ = _get(f"{server}/experiments/api/sidebar")
    assert status == 200 and [i["id"] for i in json.loads(body)["items"]][:2] == ["overview", "compare"]
    status, exp_body, headers = _get(f"{server}/experiments/view?item=compare")
    assert status == 200 and "くらべる表" in exp_body and headers.get("Content-Security-Policy") == "frame-ancestors 'self'"
    status, body, _ = _get(f"{server}/compare/api/sidebar")
    assert status == 200 and [i["id"] for i in json.loads(body)["items"]] == ["all"]
    status, cmp_body, _ = _get(f"{server}/compare/view?item=all")
    assert status == 200 and cmp_body == exp_body
    assert _get(f"{server}/compare/view?item=nope")[0] == 404
    assert _get(f"{server}/compare")[0] == 200


# ---------------------------------------------------------------- 本物（DB のある機械だけ）


def _real_paths() -> traderview.TraderPaths:
    return dataclasses.replace(traderview.TraderPaths.default(), research_db=REAL_DB)


def test_real_mapping_covers_every_trader_in_config():
    """compare.toml の人の集合 ＝ 実売買の設定の人の集合（直下 ＋ 候補。試験用を除く）。人を足す・外すときはここも直す。"""
    paths = _real_paths()
    declared = [str(t.get("id")) for t in compareview.load_words(paths)["trader"]]
    listed = [k for k, _f, _ok in traderview.trader_files(paths)]
    assert sorted(declared) == sorted(listed), (sorted(set(declared) ^ set(listed)))
    assert len(declared) == len(set(declared))
    for t in compareview.load_words(paths)["trader"]:
        assert t.get("forms"), t["id"]
        for f in t["forms"]:
            assert re.fullmatch(r"[^@]+@\d+", str(f.get("base") or "")), (t["id"], f)
            assert f.get("shape") in ("tr5", "tr5-600"), (t["id"], f)
    ids = {str(m.get("id")) for m in traderview.load_words(paths)["models"]}
    for m in compareview.load_words(paths)["model"]:
        assert m["id"] in ids, m["id"]


def test_real_mapping_rows_exist_in_the_db():
    """宣言した素の形と、機械で作った 5 本 ／ 全銘柄の名前が DB に 1 行ずつある。モデルの base は θ 50 の行がある。"""
    paths = _real_paths()
    rows = compareview.ledger(paths)
    if rows is None:
        pytest.skip("研究の DB（ledger_rows）が無い機械")
    words = compareview.load_words(paths)
    for t in words["trader"]:
        for f in t["forms"]:
            base = str(f["base"])
            for name in (base, compareview.with_shape(base, str(f["shape"])), compareview.with_shape(base, compareview.TRALL)):
                assert name in rows, (t["id"], name)
            assert compareview.SHAPED.search(rows[compareview.with_shape(base, str(f["shape"]))].method)
            assert not compareview.SHAPED.search(rows[base].method)
    for m in words["model"]:
        for b in m.get("base") or []:
            assert f"{b}@50" in rows, (m["id"], b)
    # 綴りは names.toml の [trader] と同じ（ここで手で決め直さない）
    assert set(compareview.shapes(paths, words)) >= {"tr5", "tr5-600", "trall"}
    body = compareview.page_body(paths)
    assert ">対応なし</td>" not in body and "設定に居ない</span>" not in body
