"""⚠ **検証結果一覧の行と判定を DB（`ledger_rows`）にも入れる**（プラン db-model-facts.md の Phase 3）。

⚠ `ledger.md` と同じ生成物である（数え方・判定の規則は `ail/catalog.py` の 1 か所のまま）。ここで確かめるのは
「同じ行・同じ判定・同じ `n_trials` が入る」「入れ直しても増えない」「標準ライブラリだけで読める形」。
⚠ 本物の DB には書かない（一時ファイルの DB に入れる）。
"""

from __future__ import annotations

import json
import re

import numpy as np

from ail import catalog, names, rundb
from cli import ledger

ROW = {"鍵": "全部使う（基準）", "モデル": "Ridge", "粒度": "日足", "地平": "1 本（1 日）",
       "特徴量の層": "own", "層": "adjusted", "期間": "2018-01-31", "銘柄": "63",
       "検証方式": "閾値売買", "形式": "共通", "較正": "std", "閾値": "50",
       "ID": "", "判定": "落とす", "閉じる": None, "純利bp": np.float64("nan"), "本数": np.float64(23.4)}


def test_items_are_plain_and_carry_the_run_days():
    """名前・日付・JSON の形（numpy の数は数に・NaN は null・時刻で始まらない実行は日付に数えない）。"""
    row = {**ROW, "実行一覧": ["legacy-dai", "2026-09-13T10-00-00_x", "2026-09-10T09-00-00_x"]}
    (item,) = ledger.ledger_items({"rows": [row], "leak": []})
    assert item["trial_name"] == "own.all.ridge.shared@50" and item["model_name"] == "own.all.ridge.shared"
    assert (item["first_run"], item["last_run"]) == ("2026-09-10", "2026-09-13")
    doc = json.loads(item["doc"])
    assert doc["純利bp"] is None and doc["本数"] == 23.4 and doc["判定"] == "落とす"
    assert json.loads(item["runs"])[0] == "legacy-dai"


def test_store_puts_the_same_rows_verdicts_and_count(tmp_path):
    """⚠ **本物の検証結果一覧と同じ行・同じ判定・同じ n_trials**（`ledger.md` に書かれる数と一致）。入れ直しても増えない。"""
    d = catalog.ledger()
    conn = rundb.connect(str(tmp_path / "research.sqlite"))
    n = ledger.store(d, conn)

    shown = re.search(r"`n_trials` は (\d+)", ledger.build(d))
    assert shown and n == int(shown.group(1))
    assert conn.execute("SELECT value FROM meta WHERE key = 'ledger_n_trials'").fetchone()[0] == str(n)
    for leak, rows in ((0, d["rows"]), (1, d["leak"])):
        assert conn.execute("SELECT COUNT(*) FROM ledger_rows WHERE leak = ?", (leak,)).fetchone()[0] == len(rows)
    got = {t: (v, m, it) for t, v, m, it in conn.execute(
        "SELECT trial_name, verdict, model_name, is_trial FROM ledger_rows WHERE leak = 0")}
    for r in d["rows"]:
        assert got[names.trial_name(r)] == (r["判定"], names.model_name(r), int(bool(catalog.is_trial(r))))
    # 標準ライブラリの sqlite3 だけで、判定を JSON から引ける
    assert conn.execute("SELECT COUNT(*) FROM ledger_rows WHERE json_extract(doc, '$.判定') = verdict").fetchone()[0] \
        == len(d["rows"]) + len(d["leak"])

    ledger.store(d, conn)
    assert conn.execute("SELECT COUNT(*) FROM ledger_rows").fetchone()[0] == len(d["rows"]) + len(d["leak"])
