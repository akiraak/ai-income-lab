"""開発ループの段 2（`[build] mode = "claude"` ＝ `claude -p` が案を作る）を、本物の git の一時リポジトリで通す。

⚠ 2026-11-13 まで、この枝（`devloop.build` の mode = "claude"）にはテストが 1 本も無かった（試運転は mode = "manual" だけ）。
全段の試運転に進む前に、偽の `claude`（ファイルを書く関数）と本物の `git status` ／ `git checkout` で次を固定する:

  - 通った案 ＝ 書いたものが残り、登録簿は「作成中」・`experiments` が入る
  - ⚠ **通らなかった案が書いたものは必ず消える**（費用の上限に当たった回 ＝ 結果が空 ／ `experiments` が無い ／ テストのファイルが無い ／
    テストが落ちた ／ allowlist の外）。残ると作りかけの `ail/ideas/i*.py` を `ail.bootstrap` が読み、晩のコミット（`git add -A`）が main に入れる
  - その案より前からあった未コミットのファイル（その晩の段 1 が書いた登録簿）は消さない
"""

from __future__ import annotations

import os
import subprocess
import tomllib

import pytest

from cli import devloop

FD = "experiments/feature-discovery"
BUILT_OK = '作った。\n\n```toml\n[[built]]\nid = "20261026-x"\nstatus = "作成中"\nexperiments = ["dev_x"]\n```\n'


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def _idea(root, id_, status="登録"):
    d = {"id": id_, "date": "2026-10-26", "title": id_, "kind": "モデル", "origin": {"source": "independent"}, "question": "q",
         "structure": "s", "n_trials_planned": 3, "key_shape": ["x.y.z.shared"], "failure_modes": ["f"], "prediction": "p", "effort": "小",
         "measurable": "測れる: r", "touches": "新しいモジュールだけ", "status": status, "registered_at": "2026-10-25 00:00 PDT"}
    devloop.write_idea(str(root / "config" / "ideas" / f"{id_}.toml"), d)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """一時のリポジトリ（本物の git）。ROOT ＝ <repo>/experiments/feature-discovery（本物と同じ深さ ＝ テストのファイルの道の判定が同じ）。"""
    root = tmp_path / FD
    for d in ("config/ideas", "config/experiment", "ail/ideas", "tests", "cli", "prompts"):
        (root / d).mkdir(parents=True)
    (root / "prompts" / "build.md").write_text("作る\n", encoding="utf-8")
    (root / "config" / "names.toml").write_text('[detector]\n"old" = "old"\n', encoding="utf-8")
    (root / "cli" / "run.py").write_text("# 共有の経路\n", encoding="utf-8")
    monkeypatch.setattr(devloop, "ROOT", str(root))
    monkeypatch.setattr(devloop, "REPO", str(tmp_path))
    _idea(root, "20261026-x")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    # その晩の段 1 が書いた、まだコミットされていない登録簿（段 2 の前からある未追跡のファイル）
    _idea(root, "20261026-tonight", status="共有コード変更")
    cfg = {"ideation": {"prompts": "prompts", "reference_pct": 30, "ideas_per_night": 5},
           "build": {"mode": "claude", "max_turns": 10, "max_budget_usd": 5.0, "retries_per_idea": 2},
           "paths": {"ideas": str(root / "config" / "ideas"), "register": str(root / "register.md"), "log": str(root / "log")},
           "allowlist": {"write": ["config/ideas/", "config/experiment/dev_", "ail/ideas/", "tests/test_ideas_", "config/names.toml"],
                         "forbid": ["cli/run.py", "ail/validation/", "../../TODO.md"]}}
    return tmp_path, root, cfg


def _env(repo_dir, root, writes: dict[str, str], result: str, *, errors=(), tests_rc=0, calls=None):
    """偽の `claude` ＝ `writes`（ROOT 基準の道 → 中身）を書いて `result` を返す。git は本物。"""
    calls = calls if calls is not None else []

    def claude(prompt, *, tools, max_turns, budget, cwd, timeout=None):
        calls.append(("claude", round(budget, 2)))
        for rel, text in writes.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return {"result": result, "cost": 1.0, "turns": 5, "errors": list(errors), "rc": 0, "model": "claude-fable-5-2"}

    def tests(targets):
        calls.append(("tests", tuple(targets)))
        return tests_rc, "1 failed" if tests_rc else "ok"

    return {"root": str(root), "repo": str(repo_dir), "sh": devloop._sh, "claude": claude, "tests": tests}


def _status(repo_dir) -> set[str]:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=str(repo_dir), capture_output=True, text=True).stdout
    return {line[3:] for line in out.splitlines()}


def _doc(root, id_="20261026-x") -> dict:
    return tomllib.loads((root / "config" / "ideas" / f"{id_}.toml").read_text(encoding="utf-8"))


FULL = {"ail/ideas/i20261026_x.py": "X = 1\n", "config/experiment/dev_x.toml": 'name = "dev_x"\n', "tests/test_ideas_20261026_x.py": "def test_x():\n    pass\n"}
TONIGHT = f"{FD}/config/ideas/20261026-tonight.toml"


def test_built_idea_keeps_its_files_and_becomes_in_progress(repo):
    repo_dir, root, cfg = repo
    calls: list = []
    out = devloop.build(cfg, _env(repo_dir, root, FULL, BUILT_OK, calls=calls), {}, "2026-10-26", lambda s: None)
    assert out["built"] == ["20261026-x"] and out["failed"] == [] and out["models"] == ["claude-fable-5-2"] and out["cost"] == 1.0
    d = _doc(root)
    assert d["status"] == "作成中" and d["experiments"] == ["dev_x"] and d["build_attempts"] == 1
    assert all((root / rel).exists() for rel in FULL)
    # 運転手が流すテスト ＝ 案のテスト ＋ 既定経路の指紋（共有の経路が 1 ビットも変わらないこと）
    t = [c for c in calls if c[0] == "tests"][0][1]
    assert t[0] == "tests/test_ideas_20261026_x.py" and {"tests/test_predict.py", "tests/test_trading_run.py"} <= set(t)


@pytest.mark.parametrize("writes, result, errors, tests_rc, why", [
    # 費用の上限に当たった回: 途中まで書いて、結果は 1 文字も返らない
    ({"ail/ideas/i20261026_x.py": "def half(:\n", "config/names.toml": "壊れた\n"}, "", ["Reached maximum budget ($5)"], 0, "experiments が無い"),
    ({"ail/ideas/i20261026_x.py": "X = 1\n"}, "作った（つもり）", [], 0, "experiments が無い"),
    ({k: v for k, v in FULL.items() if not k.startswith("tests/")}, BUILT_OK, [], 0, "test_ideas_"),
    (FULL, BUILT_OK, [], 1, "テストが落ちた"),
    ({**FULL, "cli/run.py": "# 書き換えた\n"}, BUILT_OK, [], 0, "allowlist の外"),
])
def test_failed_idea_leaves_nothing_behind(repo, writes, result, errors, tests_rc, why):
    """⚠ 通らなかった案が書いたものは、新しいファイルも・書き換えた追跡済みのファイルも、全部元に戻る。"""
    repo_dir, root, cfg = repo
    before = _status(repo_dir)
    assert before == {TONIGHT}
    logs: list[str] = []
    out = devloop.build(cfg, _env(repo_dir, root, writes, result, errors=errors, tests_rc=tests_rc), {}, "2026-10-26", logs.append)
    assert out["built"] == [] and len(out["failed"]) == 1 and why in out["failed"][0]["reason"]
    # 残るのは: 前からあった今夜の登録簿 ＋ その案の登録簿（試した回数と理由を書き足した）だけ
    assert _status(repo_dir) == {TONIGHT, f"{FD}/config/ideas/20261026-x.toml"}
    assert not (root / "ail" / "ideas" / "i20261026_x.py").exists() and not (root / "tests" / "test_ideas_20261026_x.py").exists()
    assert (root / "config" / "names.toml").read_text(encoding="utf-8") == '[detector]\n"old" = "old"\n'
    assert (root / "cli" / "run.py").read_text(encoding="utf-8") == "# 共有の経路\n"
    d = _doc(root)
    assert d["status"] == "登録" and d["build_attempts"] == 1 and why in d["notes"]        # 1 回目は登録のまま（翌晩もう 1 度）
    assert _doc(root, "20261026-tonight")["status"] == "共有コード変更"


def test_second_failure_marks_the_idea_failed_and_budget_error_is_in_the_reason(repo):
    repo_dir, root, cfg = repo
    env = _env(repo_dir, root, {"ail/ideas/i20261026_x.py": "def half(:\n"}, "", errors=["Reached maximum budget ($5)"])
    devloop.build(cfg, env, {}, "2026-10-26", lambda s: None)
    out = devloop.build(cfg, env, {}, "2026-10-27", lambda s: None)
    d = _doc(root)
    assert d["status"] == "失敗" and d["build_attempts"] == 2 and out["failed"][0]["status"] == "失敗"
    assert "Reached maximum budget" in d["notes"]                                           # なぜ空だったかが登録簿に残る
    assert not (root / "ail" / "ideas" / "i20261026_x.py").exists()
