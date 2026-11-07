"""「棚卸し」（外部のタブ「実行」の 1 ページ ＝ `inventoryview.py`。dashboard.md §21）。研究の DB と下書きを読むだけ。"""

from __future__ import annotations

import ast
import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest

import inventoryview
import traderview
import vibetab
from tests.test_traders_tab import paths as _trader_paths  # noqa: F401

REPO = Path(__file__).resolve().parents[2]


def _doc(method, theta, edge, fold, runs, key="K", period="2018-01-31", **extra):
    return json.dumps({"手法名": method, "閾値": theta, "上乗せbp": edge, "上乗せfold": fold, "実行一覧": runs,
                       "検証方式": "閾値売買", "鍵": key, "モデル": "Ridge", "期間": period, **extra}, ensure_ascii=False)


DRAFT = '''
[[closed]]
key = "K"
threshold = "50"
period = "2018-06-27"
note = "⚠ **閉じる（2026-11-07・軸を閉じた）**: 追試で再現せず（[x](../x.md)）"

[[closed]]
key = "OLD"
threshold = "50"
note = "⚠ **閉じる（2026-10-22・代表済み）**: もう閉じた行"
'''


@pytest.fixture()
def paths(tmp_path: Path, _trader_paths: traderview.TraderPaths) -> traderview.TraderPaths:
    exp = tmp_path / "experiment"
    exp.mkdir()
    (exp / "dev_roles_c3stop_b10e5.toml").write_text("# ⚠ **開発ループの登録簿 `20261101-c3-as-stop-role`（参考あり）**\nname = \"x\"\n", encoding="utf-8")
    inv = tmp_path / "specs"
    (inv / "assets").mkdir(parents=True)
    (inv / "assets" / "ledger-hold-inventory-2026-11-closed-draft.toml").write_text(DRAFT, encoding="utf-8")
    (inv / "ledger-hold-inventory-2026-11.md").write_text("# 棚卸し\n", encoding="utf-8")
    db = tmp_path / "research.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                       "CREATE TABLE ledger_rows (leak INTEGER, trial_name TEXT, model_name TEXT, is_trial INTEGER,"
                       " verdict TEXT, closed INTEGER, first_run TEXT, last_run TEXT, runs TEXT, doc TEXT);")
    rows = [
        (0, "a.sel.ridge.shared@50", 1, "保留", 0, _doc("M選抜", "50", 930.5, "4/5 ＋＋＋＋−", ["2026-10-22T10-00-00_sel600_midcap_cs"], period="2018-06-27", 並べ替え分位="57/100/48/63/96", 並べ替えp=0.026)),
        (0, "a.roles.ridge.shared@50", 1, "保留", 0, _doc("R8", "50", 237.0, "1/5 −0＋00", ["2026-11-01T13-02-20_lag1_trader_roles_t14"])),
        (0, "a.c3.ridge.shared@50", 1, "保留", 0, _doc("R19", "50", 305.0, "3/5 ＋＋−−＋", ["2026-11-05T15-01-00_dev_roles_c3stop_b10e5"])),
        (0, "a.old.ridge.shared@50", 1, "保留", 1, _doc("旧", "50", 12.0, "2/5", ["2026-09-10T00-00-00_old"], key="OLD")),
        (0, "a.drop.ridge.shared@50", 1, "落とす", 0, _doc("落", "50", -500.0, "0/5", ["2026-09-10T00-00-00_x"])),
        (0, "a.adopt.ridge.shared@50", 1, "採る", 0, _doc("採", "50", 1003.0, "5/5", ["2026-09-10T00-00-00_x"])),
        (1, "a.sel.ridge.shared@50", 1, "保留", 0, _doc("M選抜", "50", 20000.0, "5/5", ["2026-10-22T10-00-00_sel600_midcap_cs_leak"])),
        (0, "own.drift.none.shared@50", 0, "基準", 0, _doc("基準 常に上（ドリフト）", "50", 0.0, "0/5", ["2026-10-22T10-00-00_sel600_midcap_cs"])),
    ]
    conn.executemany("INSERT INTO ledger_rows VALUES (?, ?, ?, ?, ?, ?, '', '', '[]', ?)",
                     [(lk, n, n.rsplit("@", 1)[0], t, v, c, d) for lk, n, t, v, c, d in rows])
    conn.execute("INSERT INTO meta VALUES ('ledger_built_at', '2026-11-07T15-00-00')")
    conn.commit()
    conn.close()
    return dataclasses.replace(_trader_paths, research_db=db, experiment_dir=exp, inventory_dir=inv)


def test_only_open_held_rows_with_groups_and_proposals(paths):
    rows, c = inventoryview.open_rows(paths)
    assert c == {"held": 4, "closed": 1, "open": 3, "close": 1, "drafts": 2}       # 閉じた・落とす・採る・leak・基準線は出ない
    by = {x["row"].method: x for x in rows}
    assert by["M選抜"]["group"] == "sel600_midcap_cs" and by["M選抜"]["proposal"] == "閉じる" and by["M選抜"]["kind"] == "軸を閉じた"
    assert by["R8"]["group"] == "lag1_trader_roles" and by["R8"]["proposal"] == "残す"          # 末尾の _t14 を落とす
    assert by["R19"]["group"] == "登録簿 20261101-c3-as-stop-role" and by["R19"]["proposal"] == "残す"


def test_page_shows_counts_tables_perm_columns_and_documents(paths):
    body = inventoryview.page_body(paths)
    assert "<b>4</b><span>保留</span>" in body and "<b>3</b><span>注記なし</span>" in body and "<b>1</b><span>案: 閉じる</span>" in body
    assert "区分 3（注記なし 3 行）" in body and "閉じる 1（軸を閉じた）" in body and "残す 1" in body
    assert "57/100/48/63/96" in body and "0.026" in body and "+930.5" in body
    assert "20,000" not in body and "旧" not in body and "落とす" not in body.split("行の一覧")[1]
    assert "ledger-hold-inventory-2026-11.md" in body and "ledger-hold-inventory-2026-11-closed-draft.toml</code>（2 件）" in body
    assert "2026-11-07T15-00-00" in body
    assert "**" not in body.split("行の一覧")[1]                                    # 注記の markdown は title で素の文に


def test_no_db_is_a_message_not_a_crash(paths):
    p = dataclasses.replace(paths, research_db=paths.research_db.parent / "none.sqlite")
    assert inventoryview.open_rows(p) is None
    assert "読めない" in inventoryview.page_body(p)


def test_closed_fields_and_group_rules_are_the_research_sides_copy():
    src = (REPO / "experiments" / "feature-discovery" / "ail" / "catalog.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    research = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                    if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_CLOSED_FIELDS" for t in n.targets))
    assert inventoryview.CLOSED_FIELDS == research
    sc = (REPO / "experiments" / "feature-discovery" / "cli" / "select_candidates.py").read_text(encoding="utf-8")
    for frag in ('startswith("dev_tr_")', 'startswith("dev_")', r're.sub(r"_t\d+b?$", "", experiment)', "机上の持ち越し（31-3 の 3）"):
        assert frag in sc                                                              # 研究側の区分の決まりが変わったら写しも直す
    assert inventoryview.group_of("trader_roles_t7b", Path("/nonexistent")) == "trader_roles"
    assert inventoryview.group_of("dev_tr_x", Path("/nonexistent")) == "机上の持ち越し（31-3 の 3）"
    assert inventoryview.group_of("dev_zzz", Path("/nonexistent")) == "開発ループ（id 不明）dev_zzz"


def test_sidebar_and_routes(paths):
    items = vibetab.exp_sidebar.__code__.co_names
    assert "inventoryview" in items
    assert inventoryview.body(paths, "all") == inventoryview.page_body(paths) and inventoryview.body(paths, "x") is None
    assert inventoryview.sidebar(paths)["items"][0]["id"] == "all"
    fp = inventoryview.fingerprint(paths)
    assert any(k.endswith("closed-draft.toml") for k in fp) and any(k.endswith("2026-11.md") for k in fp)
