"""「開発ループ」（外部のタブ「実行」の 1 ページ ＝ `devloopview.py`。dashboard.md §20）。登録簿・日誌・設定・研究の DB を読むだけ。"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest

import devloopview
import traderview
import vibetab
from tests.test_traders_tab import paths as _trader_paths  # noqa: F401

IDEA = '''
id = "20261025-h5-pick-centered"
date = "2026-10-25"
title = "期間を選ぶゲートを土台差で"
kind = "モデル"
origin = { source = "reference", refs = [], prompt = "ideate_ref" }
question = "問い"
structure = "口"
n_trials_planned = 6
key_shape = ["own-trend.h5-gate-pick-centered.ridge.shared"]
failure_modes = ["型"]
prediction = "予想"
effort = "小"
measurable = "測れる: 理由"
touches = "共有の経路: ail/detectors"
status = "回した"
registered_at = "2026-10-25 12:31 PDT"
reference_mode = "組み合わせる"
runs = ["2026-10-25T12-49-35_dev_h5_pick_centered"]
'''
IDEA2 = '''
id = "20261025-permutation-yardstick"
date = "2026-10-25"
title = "並べ替え検定"
kind = "物差し"
origin = { source = "independent" }
question = "問い"
structure = "口"
n_trials_planned = 0
key_shape = []
failure_modes = ["型"]
prediction = "予想"
effort = "中"
measurable = "測れる: 理由"
touches = "共有の経路: ail/validation"
status = "共有コード変更"
'''
SETTINGS = '''
[ideation]
reference_pct = 30
ideas_per_night = 5
max_budget_usd = 6.0
[build]
mode = "manual"
max_budget_usd = 5.0
[run]
max_trials_per_night = 12
[schedule]
time_local = "01:00"
timezone = "America/Los_Angeles"
total_hours = 3
'''


def _doc(method, theta, edge, fold, runs):
    return json.dumps({"手法名": method, "閾値": theta, "上乗せbp": edge, "上乗せfold": fold, "実行一覧": runs, "検証方式": "閾値売買"}, ensure_ascii=False)


@pytest.fixture()
def paths(tmp_path: Path, _trader_paths: traderview.TraderPaths) -> traderview.TraderPaths:
    ideas = tmp_path / "ideas"
    ideas.mkdir()
    (ideas / "20261025-h5-pick-centered.toml").write_text(IDEA, encoding="utf-8")
    (ideas / "20261025-permutation-yardstick.toml").write_text(IDEA2, encoding="utf-8")
    log = tmp_path / "log"
    log.mkdir()
    (log / "2026-10-26.md").write_text("# 開発ループの日誌 — 2026-10-26\n\n## 段 4 選ぶ\n\n1. `x@50`\n\n## 段 1 の原文\n\n長い原文\n", encoding="utf-8")
    (log / "2026-10-25.md").write_text("# 開発ループの日誌 — 2026-10-25\n", encoding="utf-8")
    toml = tmp_path / "devloop.toml"
    toml.write_text(SETTINGS, encoding="utf-8")
    db = tmp_path / "research.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                       "CREATE TABLE ledger_rows (leak INTEGER, trial_name TEXT, model_name TEXT, is_trial INTEGER,"
                       " verdict TEXT, closed INTEGER, first_run TEXT, last_run TEXT, runs TEXT, doc TEXT);")
    run = "2026-10-25T12-49-35_dev_h5_pick_centered"
    conn.executemany("INSERT INTO ledger_rows VALUES (?, ?, ?, ?, ?, 0, '2026-10-25', '2026-10-25', '[]', ?)", [
        (0, "own-trend.h5-gate-pick-centered.ridge.shared@50", "own-trend.h5-gate-pick-centered.ridge.shared", 1, "落とす", _doc("H5c", "50", -88.3, "1/5 −＋−−−", [run])),
        (0, "own-trend.h5-gate-pick-centered.ridge.shared@55", "own-trend.h5-gate-pick-centered.ridge.shared", 1, "落とす", _doc("H5c", "55", -757.0, "0/5 −−−−−", [run])),
        (0, "own.drift.none.shared@50", "own.drift.none.shared", 0, "基準", _doc("基準 常に上（ドリフト）", "50", 0.0, "0/5", [run])),
        (1, "own-trend.h5-gate-pick-centered.ridge.shared@50", "own-trend.h5-gate-pick-centered.ridge.shared", 1, "採る", _doc("H5c", "50", 3886.9, "5/5 ＋＋＋＋＋", [run + "_leak"])),
    ])
    conn.execute("INSERT INTO meta VALUES ('ledger_built_at', '2026-10-25T12-50-45')")
    conn.commit()
    conn.close()
    return dataclasses.replace(_trader_paths, ideas_dir=ideas, devloop_toml=toml, devloop_log=log, research_db=db)


def test_page_shows_registry_results_settings_and_latest_journal(paths):
    body = devloopview.page_body(paths)
    assert "登録簿 2 本（共有コード変更 1・回した 1）" in body and "r 30%" in body and "N 5" in body and "manual" in body
    assert "20261025-h5-pick-centered" in body and "参考あり（組み合わせる）" in body and "共有の経路: ail/detectors" in body
    assert "θ50" in body and "−88.3" in body and "1/5 −＋−−−" in body and "落とす" in body and "3,886.9" not in body    # leak と基準線は出ない
    assert "日誌 2 晩" in body and "2026-10-26.md" in body and "長い原文" not in body and "段 1 の原文は日誌のファイルで" in body
    assert "採用ではない" in body and "良し悪し" in body
    for word in ("おすすめ", "一番良い", "推奨"):
        assert word not in body


def test_empty_registry_and_missing_db_are_fine(tmp_path, _trader_paths):
    p = dataclasses.replace(_trader_paths, ideas_dir=tmp_path / "none", devloop_toml=tmp_path / "none.toml", devloop_log=tmp_path / "nolog", research_db=None)
    body = devloopview.page_body(p)
    assert "登録簿はまだ無い" in body and "まだ 1 晩も回っていない" in body
    assert devloopview.body(p, "all") and devloopview.body(p, "zzz") is None
    assert devloopview.sidebar(p)["items"][0]["id"] == "all"


def test_fingerprint_moves_with_registry_and_journal(paths):
    a = devloopview.fingerprint(paths)
    (Path(paths.ideas_dir) / "20261026-x.toml").write_text('id = "20261026-x"\ntitle = "x"\nstatus = "案"\n', encoding="utf-8")
    b = devloopview.fingerprint(paths)
    assert set(b) - set(a) == {str(Path(paths.ideas_dir) / "20261026-x.toml")}


def test_experiments_sidebar_lists_devloop_after_compare(tmp_path, paths):
    items = vibetab.exp_sidebar(tmp_path)["items"]
    ids = [i["id"] for i in items]
    assert ids[:3] == ["overview", "compare", "devloop"] and items[2]["label"] == "開発ループ"


def test_sandbox_idea_shows_registry_result_not_db_rows():
    """サンドボックスで回した案（rules.md 31-7）は、研究の DB に行が無い ＝ レジストリの [sandbox] を写す。"""
    import devloopview
    cell = devloopview._result_cells({"ran_in": "sandbox", "sandbox": {"n_trials": 6, "verdicts": "落とす 6", "branch": "sandbox/x", "commit": "abc1234"}}, {})
    assert "サンドボックス" in cell and "検証 6・落とす 6" in cell and "sandbox/x @ abc1234" in cell and "保留以上あり" not in cell
    held = devloopview._result_cells({"ran_in": "sandbox", "sandbox": {"n_trials": 3, "verdicts": "保留 1・落とす 2", "held": True}}, {})
    assert "保留以上あり" in held
