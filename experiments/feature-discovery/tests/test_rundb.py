"""⚠ **研究の記録の正本は DB**（`runs/research.sqlite`。rules.md 10 章 ＝ 1 実行 1 記録・上書きしない）。

2026-09-21 にディレクトリから移した（利用者の裁定「2 か所には置かない。DB に入れたらファイルは削除」）。
⚠ **移しても記録が 1 ビットも変わらないこと・閉じた記録を書き換えられないこと**を確かめる。
"""

from __future__ import annotations

import json
import os
import sqlite3

import pandas as pd
import pytest

from ail import rundb, runs


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path / "runs"))
    return tmp_path


def _dir(root, name, files: dict[str, bytes]):
    d = root / "src" / name
    for rel, data in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return d


def test_import_keeps_every_byte_and_verify_says_so(db):
    """⚠ **取り込んだ中身は元のバイト列と 1 ビットも違わない**（JSON も CSV も二進も）。"""
    files = {"config.json": '{"name": "x", "k": 1}\n'.encode(), "summary.csv": "手法,純利bp\nA,1.5\n".encode(),
             "fitted/w.pt": bytes(range(256)) * 10, "log.txt": b"\xe3\x81\x82\n"}
    d = _dir(db, "2026-01-01T00-00-00_x", files)
    conn = rundb.connect(runs.db_path())
    assert rundb.ingest_dir(conn, str(d)) == 4
    assert rundb.verify_dir(conn, str(d)) == []
    for rel, data in files.items():
        assert runs.read_bytes("2026-01-01T00-00-00_x", rel) == data
    # JSON は文字列のまま（DB の中で引ける）
    assert conn.execute("SELECT json_extract(body, '$.k') FROM files WHERE path = 'config.json'").fetchone()[0] == 1
    # ディスクの側が変われば「中身が違う」
    (d / "summary.csv").write_bytes(b"x")
    assert rundb.verify_dir(conn, str(d)) == ["中身が違う: summary.csv"]


def test_closed_run_cannot_be_rewritten_or_deleted(db):
    """⚠ **閉じた実行は書き換えない・消さない**（トリガー。1 実行 1 記録）。足すのは許す（`--recheck`）。"""
    conn = rundb.connect(runs.db_path())
    rundb.ingest_dir(conn, str(_dir(db, "2026-01-01T00-00-00_x", {"summary.csv": b"a"})))
    with pytest.raises(sqlite3.DatabaseError, match="書き換えない"):
        rundb.put(conn, "2026-01-01T00-00-00_x", "summary.csv", b"b")
    conn.rollback()
    with pytest.raises(sqlite3.DatabaseError, match="消さない"):
        conn.execute("DELETE FROM files WHERE run = '2026-01-01T00-00-00_x'")
    conn.rollback()
    with pytest.raises(sqlite3.DatabaseError, match="消さない"):
        conn.execute("DELETE FROM runs WHERE name = '2026-01-01T00-00-00_x'")
    conn.rollback()
    rundb.put(conn, "2026-01-01T00-00-00_x", "checks.json", b"{}")          # 足すのは許す
    assert runs.read_bytes("2026-01-01T00-00-00_x", "summary.csv") == b"a"
    with pytest.raises(SystemExit, match="もう DB にある"):
        rundb.ingest_dir(conn, str(db / "src" / "2026-01-01T00-00-00_x"))


def test_run_writes_through_and_close_moves_the_work_dir(db):
    """⚠ **書いたその場で読める**（`cli.run` は閉じる前に台帳を数える）。`close()` で作業の置き場を入れて消す。"""
    run = runs.Run("x", {"name": "x"}, seed=0)
    assert run.name in runs.list_runs()
    run.result(pd.DataFrame({"手法": ["A"], "fold": [1]}), pd.DataFrame({"純利bp": [1.0]}, index=pd.Index(["A"], name="手法")))
    assert runs.read_csv(run.name, "summary.csv", index_col=0).loc["A", "純利bp"] == 1.0     # 閉じる前に見える
    with open(os.path.join(run.dir, "extra.csv"), "w", encoding="utf-8") as f:              # 道でしか書けないもの
        f.write("a\n1\n")
    run.log("こんにちは")
    assert run.close() == run.name
    assert not os.path.exists(run.dir)                                                       # ⚠ 2 か所に置かない
    assert runs.read_bytes(run.name, "extra.csv") == b"a\n1\n"
    assert runs.read_bytes(run.name, "log.txt") == "こんにちは\n".encode()
    assert json.loads(runs.read_bytes(run.name, "env.json"))["seed"] == 0
    conn = rundb.connect(runs.db_path())
    with pytest.raises(sqlite3.DatabaseError):
        rundb.put(conn, run.name, "summary.csv", b"x")


def test_run_csv_bytes_match_writing_to_a_file(db, tmp_path):
    """⚠ **DB に入る CSV は `to_csv(道)` と同じバイト列**（いままでの実行と見分けがつかない）。"""
    df = pd.DataFrame({"手法": ["A", "B"], "値": [1.25, float("nan")]})
    df.to_csv(tmp_path / "a.csv", index=False)
    run = runs.Run("x", {}, seed=0)
    run.per_symbol(df)
    assert runs.read_bytes(run.name, "per_symbol.csv") == (tmp_path / "a.csv").read_bytes()
    run.close()


def test_sweep_closes_a_crashed_run_and_keeps_what_it_left(db):
    """⚠ **途中で落ちた実行**（プロセスが居ない）を閉じる。作業の置き場に残ったファイルは入れて消す。"""
    conn = rundb.connect(runs.db_path())
    rundb.open_run(conn, "2026-01-01T00-00-00_crashed")
    conn.execute("UPDATE runs SET pid = 999999999 WHERE name = '2026-01-01T00-00-00_crashed'")
    conn.commit()
    work = os.path.join(runs.work_root(), "2026-01-01T00-00-00_crashed")
    os.makedirs(work)
    with open(os.path.join(work, "left.txt"), "w") as f:
        f.write("x")
    assert rundb.sweep(conn, runs.work_root()) == ["2026-01-01T00-00-00_crashed"]
    assert runs.read_bytes("2026-01-01T00-00-00_crashed", "left.txt") == b"x"
    assert not os.path.exists(work)
    assert rundb.sweep(conn, runs.work_root()) == []
    # 生きている実行（このプロセス）は閉じない
    run = runs.Run("alive", {}, seed=0)
    assert run.name not in rundb.sweep(conn, runs.work_root())
    run.close()


def test_materialized_is_a_temporary_copy(db):
    conn = rundb.connect(runs.db_path())
    rundb.ingest_dir(conn, str(_dir(db, "2026-01-01T00-00-00_x", {"fitted/a.pt": b"w", "config.json": b"{}"})))
    with runs.materialized("runs/2026-01-01T00-00-00_x/") as d:                 # `runs/<実行>/` の形でもよい
        assert open(os.path.join(d, "fitted", "a.pt"), "rb").read() == b"w"
    assert not os.path.exists(d)
    with pytest.raises(SystemExit, match="DB に無い"):
        runs.resolve("2026-01-01T00-00-00_nothing")


def test_queue_state_round_trips(db):
    conn = rundb.connect(runs.db_path())
    assert rundb.load_queue(conn, "q") is None
    rundb.save_queue(conn, "q", {"items": [{"id": "a", "status": "done"}]})
    rundb.save_queue(conn, "q", {"items": [{"id": "a", "status": "pending"}]})    # ⚠ これだけは書き換える
    assert rundb.load_queue(conn, "q") == {"items": [{"id": "a", "status": "pending"}]}


def test_db_cli_imports_verifies_and_removes_only_matching_dirs(db, capsys):
    """`cli.db`: 取り込む → 突き合わせる → ⚠ 一致した実行ディレクトリだけを消す（了承の印が無ければ消さない）。"""
    from cli import db as dbcli
    root = db / "runs"
    for name in ("2026-01-01T00-00-00_a", "2026-01-02T00-00-00_b"):
        (root / name).mkdir(parents=True)
        (root / name / "config.json").write_text("{}", encoding="utf-8")
    (root / "queue").mkdir()
    (root / "queue" / "q.json").write_text('{"items": []}', encoding="utf-8")
    assert dbcli.main(["import"]) == 0
    assert dbcli.main(["verify"]) == 0
    (root / "2026-01-02T00-00-00_b" / "config.json").write_text('{"x": 1}', encoding="utf-8")   # ⚠ 食い違いを作る
    assert dbcli.main(["verify"]) == 1
    with pytest.raises(SystemExit, match="消すと戻せない"):
        dbcli.main(["remove-dirs"])
    assert dbcli.main(["remove-dirs", "--i-verified"]) == 1
    assert not (root / "2026-01-01T00-00-00_a").exists()
    assert (root / "2026-01-02T00-00-00_b").exists()                          # ⚠ 食い違った実行は残す
    assert not (root / "queue").exists()
    assert runs.list_runs() == ["2026-01-01T00-00-00_a", "2026-01-02T00-00-00_b"]
    backup = db / "bk" / "research-2026-09-21.sqlite"
    assert dbcli.main(["backup", "--to", str(backup)]) == 0
    assert "整合性 ok" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="上書きしない"):
        dbcli.main(["backup", "--to", str(backup)])
