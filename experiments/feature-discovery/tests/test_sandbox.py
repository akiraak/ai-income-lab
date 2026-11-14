"""開発ループのサンドボックスの土台（`cli/sandbox.py`・プラン docs/plans/devloop-sandbox.md Phase 1）。

本物の git の一時リポジトリで確かめる:
  - 作る ＝ ワーキングツリー ＋ ブランチ ＋ データと DB のコピー。⚠ 本線のワーキングツリー・データ・DB は 1 バイトも変わらない
  - チェック ＝ 禁止パス・`experiments/feature-discovery/` の外・**既存のテストの変更と削除**を拾う（追加は通す）
  - サンドボックスの中の python は、サンドボックスのコードと DB を読む（本線の DB を指す環境変数は渡さない）
  - 要約 ＝ 作ったときからの差（増えた行 ＝ サンドボックスの検証数・変わった行・消えた行）
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess

import pytest

from cli import sandbox

FD = sandbox.FD


def _git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True).stdout


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _ledger_db(path, rows):
    """`ledger_rows` だけの小さな DB（rows ＝ (leak, 検証名, 検証の行か, 判定, 上乗せ bp)）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE IF NOT EXISTS ledger_rows (leak INTEGER, trial_name TEXT, model_name TEXT, is_trial INTEGER, verdict TEXT, doc TEXT)")
    c.execute("DELETE FROM ledger_rows")
    for leak, trial, is_trial, verdict, bp in rows:
        c.execute("INSERT INTO ledger_rows VALUES (?, ?, ?, ?, ?, ?)",
                  (leak, trial, trial.split("@")[0], is_trial, verdict, json.dumps({"手法名": "m", "閾値": "50", "検証方式": "閾値売買", "上乗せbp": bp, "上乗せfold": "3/5"}, ensure_ascii=False)))
    c.commit()
    c.close()


def _tree_hash(root) -> str:
    h = hashlib.sha256()
    for d, _dirs, files in sorted(os.walk(str(root))):
        if os.sep + ".git" in d or d.endswith(".git"):
            continue
        for f in sorted(files):
            p = os.path.join(d, f)
            h.update(os.path.relpath(p, str(root)).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


BASE_ROWS = [(0, "own.a.ridge.shared@50", 1, "落とす", -10.0), (0, "own.b.ridge.shared@50", 1, "保留", 20.0),
             (0, "基準 常に上@50", 0, "基準", 0.0), (1, "own.a.ridge.shared@50", 1, "落とす", 900.0)]


@pytest.fixture
def world(tmp_path):
    """一時の本線のリポジトリ（追跡: コード・テスト・config ／ 追跡しない: data・runs）とサンドボックスの親。"""
    repo = tmp_path / "repo"
    fd = repo / FD
    _write(fd / "cli" / "__init__.py", "")
    _write(fd / "cli" / "run.py", "VALUE = 'main'\n")
    _write(fd / "ail" / "__init__.py", "")
    _write(fd / "tests" / "test_old.py", "def test_old():\n    assert True\n")
    _write(fd / "config" / "experiment" / "exp_own.toml", 'name = "exp_own"\n')
    _write(fd / "config" / "experiment" / "exp_borrow.toml", 'name = "exp_borrow"\nfeatures_from = "exp_own"\n')
    _write(fd / "config" / "devloop.toml", "[build]\n")
    _write(repo / "docs" / "specs" / "experiments" / "feature-discovery" / "ledger.md", "ledger\n")
    _write(repo / "dashboard" / "app.py", "x = 1\n")
    _write(repo / "TODO.md", "# TODO\n")
    _write(repo / ".gitignore", f"/{FD}/data/\n/{FD}/runs/\n__pycache__/\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    # 追跡しないもの: 日足・テーブル・研究の DB
    for part in ("raw", "adjusted", "manifests"):
        _write(fd / "data" / part / "x.parquet", f"{part} の中身\n")
    _write(fd / "data" / "features" / "exp_own" / "d.parquet", "テーブル\n")
    _write(fd / "data" / "features" / "other" / "d.parquet", "別のテーブル\n")
    _ledger_db(fd / "runs" / "research.sqlite", BASE_ROWS)
    return repo, str(tmp_path / "sb")


def _create(world, id_="20261113-x"):
    repo, base = world
    sandbox.create(id_, repo=str(repo), base=base, period_files=False, log=lambda s: None)
    return repo, base, id_


def test_create_makes_worktree_branch_and_copies_without_touching_main(world):
    repo, base = world
    before, status = _tree_hash(repo), _git(repo, "status", "--porcelain")
    meta = sandbox.create("20261113-x", repo=str(repo), base=base, period_files=False, log=lambda s: None)
    t, fd = sandbox.tree("20261113-x", base), sandbox.fd_dir("20261113-x", base)
    assert os.path.isdir(t) and "sandbox/20261113-x" in _git(repo, "branch", "--list", "sandbox/*")
    assert _git(t, "rev-parse", "--abbrev-ref", "HEAD").strip() == "sandbox/20261113-x" and meta["base_commit"] == _git(repo, "rev-parse", "main").strip()
    # データは本物のコピー（リンクではない）・テーブルは作るときにはコピーしない・DB は種として入る
    p = os.path.join(fd, "data", "raw", "x.parquet")
    assert os.path.isfile(p) and not os.path.islink(p) and os.stat(p).st_ino != os.stat(repo / FD / "data" / "raw" / "x.parquet").st_ino
    assert os.listdir(os.path.join(fd, "data", "features")) == [] and meta["db_seeded"] and meta["base_n_trials"] == 2
    # ⚠ 本線は 1 バイトも変わらない
    assert _tree_hash(repo) == before and _git(repo, "status", "--porcelain") == status
    assert sandbox.list_ids(base) == ["20261113-x"]


def test_create_refuses_existing_sandbox_branch_and_bad_ids(world):
    repo, base, id_ = _create(world)
    with pytest.raises(sandbox.SandboxError, match="既にある"):
        sandbox.create(id_, repo=str(repo), base=base, period_files=False, log=lambda s: None)
    sandbox.remove(id_, repo=str(repo), base=base, log=lambda s: None)           # ブランチは残る
    with pytest.raises(sandbox.SandboxError, match="ブランチが既にある"):
        sandbox.create(id_, repo=str(repo), base=base, period_files=False, log=lambda s: None)
    for bad in ("../x", "A_b", "x", "a/b", ""):
        with pytest.raises(sandbox.SandboxError):
            sandbox.tree(bad, base)


def test_writes_in_the_sandbox_do_not_reach_main_data(world):
    repo, base, id_ = _create(world)
    fd = sandbox.fd_dir(id_, base)
    with open(os.path.join(fd, "data", "raw", "x.parquet"), "w", encoding="utf-8") as f:
        f.write("サンドボックスが上書きした\n")
    assert (repo / FD / "data" / "raw" / "x.parquet").read_text(encoding="utf-8") == "raw の中身\n"


def test_tables_follow_features_from_and_copy_only_what_exists(world):
    repo, base, id_ = _create(world)
    names = sandbox.tables_for(id_, ["exp_borrow", "exp_own"], base)
    assert names == ["exp_own", "exp_own_leak"]                                   # 借りる実験は持ち主のテーブル・同じ名前は 1 度
    res = sandbox.copy_tables(id_, names, repo=str(repo), base=base, log=lambda s: None)
    assert res == {"copied": ["exp_own"], "missing": ["exp_own_leak"], "kept": []}
    d = os.path.join(sandbox.fd_dir(id_, base), "data", "features", "exp_own", "d.parquet")
    assert os.path.isfile(d) and not os.path.islink(os.path.dirname(d))
    assert sandbox.copy_tables(id_, names, repo=str(repo), base=base, log=lambda s: None)["kept"] == ["exp_own"]
    with pytest.raises(sandbox.SandboxError):
        sandbox.copy_tables(id_, ["../raw"], repo=str(repo), base=base, log=lambda s: None)
    with pytest.raises(sandbox.SandboxError, match="config が読めない"):
        sandbox.tables_for(id_, ["no_such"], base)


def test_run_uses_sandbox_code_and_never_gets_main_db_env(world, monkeypatch):
    repo, base, id_ = _create(world)
    fd = sandbox.fd_dir(id_, base)
    with open(os.path.join(fd, "cli", "run.py"), "w", encoding="utf-8") as f:
        f.write("VALUE = 'sandbox'\n")
    monkeypatch.setenv("AIL_RESEARCH_DB", "/main/research.sqlite")
    monkeypatch.setenv("AIL_DATA_DIR", "/main/data")
    code = "import os, cli.run as r; print(r.VALUE, os.environ.get('AIL_RESEARCH_DB'), os.environ.get('AIL_DATA_DIR'), os.environ.get('AIL_SANDBOX'), os.getcwd())"
    rc, out, err = sandbox.run(id_, ["-c", code], repo=str(repo), base=base, timeout=60)
    assert rc == 0, err
    value, db, data, sid, cwd = out.split()
    assert (value, db, data, sid) == ("sandbox", "None", "None", id_) and os.path.realpath(cwd) == os.path.realpath(fd)
    assert (repo / FD / "cli" / "run.py").read_text(encoding="utf-8") == "VALUE = 'main'\n"


def test_check_flags_forbidden_paths_outside_and_touched_tests(world):
    repo, base, id_ = _create(world)
    t, fd = sandbox.tree(id_, base), sandbox.fd_dir(id_, base)
    # 許される変更: 共有の経路の書き換え・新しいモジュール・新しいテスト・案のメモ・生成物（数えない）
    _write(__import__("pathlib").Path(fd) / "cli" / "run.py", "VALUE = 'changed'\n")
    _write(__import__("pathlib").Path(fd) / "ail" / "validation" / "new.py", "X = 1\n")
    _write(__import__("pathlib").Path(fd) / "tests" / "test_new.py", "def test_new():\n    pass\n")
    _write(__import__("pathlib").Path(t) / "docs" / "specs" / "experiments" / "dev-loop" / "sandbox" / "20261113-x.md", "メモ\n")
    _write(__import__("pathlib").Path(t) / "docs" / "specs" / "experiments" / "feature-discovery" / "ledger.md", "再生成された\n")
    res = sandbox.check(id_, base)
    assert res["ok"] and f"{FD}/cli/run.py" in res["changed"] and not any("ledger.md" in p for p in res["changed"])
    # NG: 既存のテストの書き換え
    _write(__import__("pathlib").Path(fd) / "tests" / "test_old.py", "def test_old():\n    assert True or 'weakened'\n")
    res = sandbox.check(id_, base)
    assert not res["ok"] and res["tests_touched"] == [f"{FD}/tests/test_old.py"]
    _git(t, "checkout", "--", f"{FD}/tests/test_old.py")
    # NG: 既存のテストの削除
    os.remove(os.path.join(fd, "tests", "test_old.py"))
    assert sandbox.check(id_, base)["tests_touched"] == [f"{FD}/tests/test_old.py"]
    _git(t, "checkout", "--", f"{FD}/tests/test_old.py")
    # NG: 禁止パス（管理画面・TODO・ランナーの設定・docs のほかの場所）と feature-discovery の外
    _write(__import__("pathlib").Path(t) / "dashboard" / "app.py", "x = 2\n")
    _write(__import__("pathlib").Path(t) / "TODO.md", "# 書き換えた\n")
    _write(__import__("pathlib").Path(fd) / "config" / "devloop.toml", "[build]\nmode = 'x'\n")
    _write(__import__("pathlib").Path(t) / "docs" / "note.md", "x\n")
    _write(__import__("pathlib").Path(t) / "experiments" / "other" / "x.py", "x\n")
    res = sandbox.check(id_, base)
    assert not res["ok"]
    assert sorted(res["forbidden"]) == ["TODO.md", "dashboard/app.py", "docs/note.md", f"{FD}/config/devloop.toml"]
    assert res["outside"] == ["experiments/other/x.py"] and res["tests_touched"] == []


def test_commit_goes_to_the_sandbox_branch_only_and_skips_generated(world):
    repo, base, id_ = _create(world)
    t, fd = sandbox.tree(id_, base), sandbox.fd_dir(id_, base)
    main_head = _git(repo, "rev-parse", "main").strip()
    assert sandbox.commit(id_, "何もない", base) == ""
    _write(__import__("pathlib").Path(fd) / "cli" / "run.py", "VALUE = 'changed'\n")
    _write(__import__("pathlib").Path(t) / "docs" / "specs" / "experiments" / "feature-discovery" / "ledger.md", "再生成された\n")
    sha = sandbox.commit(id_, "sandbox: 20261113-x", base)
    assert sha and _git(repo, "rev-parse", "main").strip() == main_head
    assert _git(repo, "log", "--format=%s", "-1", "sandbox/20261113-x").strip() == "sandbox: 20261113-x"
    names = _git(repo, "diff", "--name-only", "main", "sandbox/20261113-x").split()
    assert names == [f"{FD}/cli/run.py"]                                          # 生成物は入らない
    assert sandbox.check(id_, base)["ok"]                                         # コミットした後も元のコミットとの diff で見る


def test_report_counts_only_new_trial_rows_and_flags_changed_or_gone(world):
    repo, base, id_ = _create(world)
    db = __import__("pathlib").Path(sandbox.db_path(id_, base))
    assert sandbox.report(id_, base) | {"db": ""} == {"id": id_, "db": "", "n_trials": 0, "verdicts": {}, "rows": [], "new_leak_rows": 0,
                                                       "changed": [], "gone": [], "base_n_trials": 2, "leak_ok": 0, "leak_missing": []}
    # サンドボックスで回した後の一覧: 新しい検証 2 行 ＋ 新しい基準線 ＋ 新しい leak・既存の 1 行は判定が変わり・1 行は消えた
    _ledger_db(db, [(0, "own.a.ridge.shared@50", 1, "保留", 30.0),                # 変わった
                    (0, "基準 常に上@50", 0, "基準", 0.0), (1, "own.a.ridge.shared@50", 1, "落とす", 900.0),
                    (0, "own.new.ridge.shared~dz@50", 1, "落とす", -5.0), (0, "own.new.ridge.shared~dz@55", 1, "保留", 12.0),
                    (0, "基準 新しい@50", 0, "基準", 0.0), (1, "own.new.ridge.shared~dz@50", 1, "落とす", 800.0)])
    r = sandbox.report(id_, base)
    assert r["n_trials"] == 2 and r["verdicts"] == {"落とす": 1, "保留": 1} and r["new_leak_rows"] == 1
    assert [x["trial"] for x in r["rows"]] == ["own.new.ridge.shared~dz@50", "own.new.ridge.shared~dz@55"] and r["rows"][1]["excess_bp"] == 12.0
    assert [c["key"] for c in r["changed"]] == ["0|own.a.ridge.shared@50"] and r["gone"] == ["0|own.b.ridge.shared@50"]
    # leak 対照: @50 は同じ検証名の leak の行が跳ねている・@55 は leak の行が無い ＝ フラグ
    assert r["leak_ok"] == 1 and r["leak_missing"] == ["own.new.ridge.shared~dz@55"]
    # 本線の DB は読むだけ
    assert sandbox.ledger_keys(str(repo / FD / "runs" / "research.sqlite"))["0|own.a.ridge.shared@50"]["verdict"] == "落とす"


def test_remove_keeps_the_branch_unless_asked(world):
    repo, base, id_ = _create(world)
    sandbox.remove(id_, repo=str(repo), base=base, log=lambda s: None)
    assert not os.path.exists(sandbox.tree(id_, base)) and sandbox.list_ids(base) == [] and sandbox.meta(id_, base) == {}
    assert "sandbox/20261113-x" in _git(repo, "branch", "--list", "sandbox/*") and "sandbox" not in _git(repo, "worktree", "list")
    sandbox.remove(id_, repo=str(repo), base=base, delete_branch=True, log=lambda s: None)
    assert _git(repo, "branch", "--list", "sandbox/*").strip() == ""


# ---------------------------------------------------------------- 記録（rules.md 31-7 の 5）

IDEA_DOC = {"id": "20261113-x", "date": "2026-11-13", "title": "試しの案", "kind": "モデル", "origin": {"source": "independent"}, "question": "問い",
            "structure": "構造", "n_trials_planned": 2, "key_shape": ["own.new.ridge.shared~dz"], "failure_modes": ["f"], "prediction": "差は出ない",
            "effort": "小", "measurable": "測れる: r", "touches": "共有の経路: cli/run.py", "status": "共有コード変更", "p_hold": 0.2}


def _recordable(world, rows, status="共有コード変更"):
    """サンドボックスでコードを書き、回した後の一覧（rows）を置き、レジストリに案を置く。"""
    from cli import devloop
    repo, base, id_ = _create(world)
    fd = __import__("pathlib").Path(sandbox.fd_dir(id_, base))
    _write(fd / "cli" / "run.py", "VALUE = 'sandbox'\n")
    _ledger_db(fd / "runs" / "research.sqlite", list(BASE_ROWS) + rows)
    ideas = repo / FD / "config" / "ideas"
    ideas.mkdir(parents=True, exist_ok=True)
    devloop.write_idea(str(ideas / f"{id_}.toml"), {**IDEA_DOC, "status": status})
    return repo, base, id_, ideas


def test_record_writes_result_table_and_registry_without_touching_main_code(world):
    import tomllib
    repo, base, id_, ideas = _recordable(world, [(0, "own.new.ridge.shared~dz@50", 1, "落とす", -5.0), (0, "own.new.ridge.shared~dz@55", 1, "落とす", -12.5),
                                                  (1, "own.new.ridge.shared~dz@50", 1, "落とす", 800.0)])
    main_db = _tree_hash(repo / FD / "runs")
    got = sandbox.record(id_, repo=str(repo), base=base, note="外した割合 24%", log=lambda s: None)
    assert got["n_trials"] == 2 and got["verdicts"] == "落とす 2" and got["held"] is False and got["invalid"] is False and got["pushed"] is False
    d = tomllib.loads((ideas / f"{id_}.toml").read_text(encoding="utf-8"))
    assert d["status"] == "回した" and d["ran_in"] == "sandbox" and d["sandbox"]["branch"] == f"sandbox/{id_}" and d["registered_at"]
    assert d["structure"] == "構造" and d["n_trials_planned"] == 2                              # 先に書いたものは直さない
    md = (repo / "docs" / "specs" / "experiments" / "dev-loop" / "sandbox" / f"{id_}.md").read_text(encoding="utf-8")
    assert "`own.new.ridge.shared~dz@55`" in md and "-12.5" in md and "落とす 2" in md and "外した割合 24%" in md and "予定 2" in md
    # コードはブランチにだけ入る・main のコードと DB は変わらない
    assert _git(repo, "show", f"sandbox/{id_}:{FD}/cli/run.py") == "VALUE = 'sandbox'\n"
    assert (repo / FD / "cli" / "run.py").read_text() == "VALUE = 'main'\n" and _tree_hash(repo / FD / "runs") == main_db
    changed = {line[3:] for line in _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()}
    assert changed == {f"{FD}/config/ideas/{id_}.toml", f"docs/specs/experiments/dev-loop/sandbox/{id_}.md"}
    # 2 度目（同じ案の記録の取り直し）は通る・ほかのステータスの案は拒む
    sandbox.record(id_, repo=str(repo), base=base, log=lambda s: None)


def test_record_flags_hold_and_marks_changed_rows_invalid(world):
    import tomllib
    repo, base, id_, ideas = _recordable(world, [(0, "own.new.ridge.shared~dz@50", 1, "保留", 30.0)])
    got = sandbox.record(id_, repo=str(repo), base=base, log=lambda s: None)
    assert got["held"] is True and got["verdicts"] == "保留 1"
    # 既存の行が変わった ＝ 無効（判定を出さない・回したぶんは数える）
    fd = __import__("pathlib").Path(sandbox.fd_dir(id_, base))
    _ledger_db(fd / "runs" / "research.sqlite", [(0, "own.a.ridge.shared@50", 1, "採る", 999.0), (0, "own.b.ridge.shared@50", 1, "落とす", -1.0),
                                                  (0, "own.new.ridge.shared~dz@50", 1, "採る", 500.0)])
    got = sandbox.record(id_, repo=str(repo), base=base, log=lambda s: None)
    assert got["invalid"] is True and got["held"] is False and got["verdicts"] == "無効" and got["n_trials"] == 1
    md = (repo / "docs" / "specs" / "experiments" / "dev-loop" / "sandbox" / f"{id_}.md").read_text(encoding="utf-8")
    assert "**無効**" in md and "| 無効 |" in md and "`0|own.a.ridge.shared@50`" in md
    assert tomllib.loads((ideas / f"{id_}.toml").read_text(encoding="utf-8"))["sandbox"]["invalid"] is True


def test_record_refuses_ideas_that_are_not_shared_code_changes(world):
    repo, base, id_, _ideas = _recordable(world, [], status="登録")
    with pytest.raises(sandbox.SandboxError):
        sandbox.record(id_, repo=str(repo), base=base, log=lambda s: None)
