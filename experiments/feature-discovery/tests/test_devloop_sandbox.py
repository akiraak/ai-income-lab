"""開発ループのステージ 5 ＝ サンドボックス（`devloop.sandbox_stage`・rules.md 31-7・プラン docs/plans/devloop-sandbox.md Phase 4）。

一時のリポジトリ（本物の git）で通す。モックは `claude`（サンドボックスのワーキングツリーにファイルを書く関数）と、サンドボックスの中の python
（`env["sandbox_run"]` ＝ pytest と `cli.queue` の代わり）だけ。

- `[sandbox] enabled` が無い ／ false なら何も呼ばない
- 通った案 ＝ レジストリが「回した」・`ran_in = "sandbox"`・結果の表ができる。**main のワーキングツリーに増えるのはその 2 つだけ**（コードはブランチ）
- 通らなかった案（禁止パス ／ 既存のテストの書き換え ／ テストが落ちた ／ 1 本も回らない）＝ サンドボックスごと捨てて理由を残す。2 回で「失敗」
- 対象の順 ＝ 手間の小さい順 → id 順・`sandbox_skip` と「共有コード変更」でない案は選ばない
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tomllib

import pytest

from cli import devloop, devloop_mail, sandbox

FD = "experiments/feature-discovery"
BASE_ROWS = [(0, "own.a.ridge.shared@50", 1, "落とす", -10.0), (0, "own.b.ridge.shared@50", 1, "落とす", -20.0)]
BUILT = '書いた。\n\n```toml\n[[built]]\nid = "x"\nstatus = "作成中"\nexperiments = ["dev_new"]\n```\n'
GOOD = {f"{FD}/cli/run.py": "VALUE = 'sandbox'\n", f"{FD}/config/experiment/dev_new.toml": 'name = "dev_new"\n',
        f"{FD}/tests/test_ideas_x.py": "def test_x():\n    pass\n"}
NEW_ROWS = [(0, "own.new.ridge.shared~dz@50", 1, "落とす", -5.0), (0, "own.new.ridge.shared~dz@55", 1, "落とす", -12.5),
            (1, "own.new.ridge.shared~dz@50", 1, "落とす", 800.0)]


def _git(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True).stdout


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _ledger_db(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE IF NOT EXISTS ledger_rows (leak INTEGER, trial_name TEXT, model_name TEXT, is_trial INTEGER, verdict TEXT, doc TEXT)")
    c.execute("DELETE FROM ledger_rows")
    for leak, trial, is_trial, verdict, bp in rows:
        c.execute("INSERT INTO ledger_rows VALUES (?, ?, ?, ?, ?, ?)",
                  (leak, trial, trial.split("@")[0], is_trial, verdict, json.dumps({"手法名": "m", "閾値": "50", "検証方式": "閾値売買", "上乗せbp": bp, "上乗せfold": "3/5"}, ensure_ascii=False)))
    c.commit()
    c.close()


def _idea(root, id_, *, status="共有コード変更", effort="小", **extra):
    d = {"id": id_, "date": "2026-11-13", "title": id_, "kind": "モデル", "origin": {"source": "independent"}, "question": "q", "structure": "s",
         "n_trials_planned": 2, "key_shape": ["own.new.ridge.shared~dz"], "failure_modes": ["f"], "prediction": "p", "effort": effort,
         "measurable": "測れる: r", "touches": "共有の経路: cli/run.py", "status": status, **extra}
    devloop.write_idea(str(root / "config" / "ideas" / f"{id_}.toml"), d)


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    root = repo / FD
    for d in ("config/ideas", "config/experiment", "config/queue", "tests", "cli", "devloop/prompts"):
        (root / d).mkdir(parents=True)
    _write(root / "devloop" / "prompts" / "build_sandbox.md", "サンドボックスで作る\n")
    _write(root / "cli" / "run.py", "VALUE = 'main'\n")
    _write(root / "tests" / "test_old.py", "def test_old():\n    assert True\n")
    _write(root / "config" / "queue" / ".keep", "")
    _write(repo / "TODO.md", "# TODO\n")
    _write(repo / ".gitignore", f"/{FD}/data/\n/{FD}/runs/\n__pycache__/\n")
    _idea(root, "20261113-b", effort="中")
    _idea(root, "20261113-a")
    _idea(root, "20261113-skip", sandbox_skip="済み")
    _idea(root, "20261113-plain", status="登録", registered_at="2026-11-13 00:00 PST")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _write(root / "data" / "raw" / "x.parquet", "raw\n")
    _ledger_db(root / "runs" / "research.sqlite", BASE_ROWS)
    monkeypatch.setattr(devloop, "ROOT", str(root))
    monkeypatch.setattr(devloop, "REPO", str(repo))
    cfg = {"ideation": {"prompts": "devloop/prompts", "reference_pct": 30, "ideas_per_night": 5},
           "sandbox": {"enabled": True, "per_night": 1, "max_turns": 80, "max_budget_usd": 20.0, "time_budget_s": 3600, "retries_per_idea": 2, "push": False},
           "paths": {"ideas": str(root / "config" / "ideas"), "register": str(root / "register.md"), "log": str(root / "log")}}
    return repo, root, cfg, str(tmp_path / "sb")


def _env(world, writes=GOOD, result=BUILT, *, tests_rc=0, rows=NEW_ROWS, calls=None):
    repo, _root, _cfg, base = world
    calls = calls if calls is not None else []

    def claude(prompt, *, tools, max_turns, budget, cwd, timeout=None):
        calls.append(("claude", cwd, budget, max_turns))
        for rel, text in writes.items():
            _write(__import__("pathlib").Path(cwd) / rel, text)
        return {"result": result, "cost": 3.5, "turns": 12, "errors": [], "rc": 0, "model": "claude-fable-5-2"}

    def run(id_, args, timeout):
        calls.append(("run", id_, tuple(args)))
        if args[:2] == ["-m", "pytest"]:
            return tests_rc, "1 failed" if tests_rc else "ok", ""
        _ledger_db(__import__("pathlib").Path(sandbox.db_path(id_, base)), list(BASE_ROWS) + list(rows))
        return 0, "queue ok", ""

    return {"root": devloop.ROOT, "repo": str(repo), "sh": devloop._sh, "claude": claude, "sandbox_run": run, "sandbox_base": base,
            "sandbox_period_files": False}


def _status(repo) -> set[str]:
    return {line[3:] for line in _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()}


def _doc(root, id_) -> dict:
    return tomllib.loads((root / "config" / "ideas" / f"{id_}.toml").read_text(encoding="utf-8"))


def test_disabled_config_calls_nothing(world):
    repo, _root, cfg, _base = world
    calls: list = []
    out = devloop.sandbox_stage({**cfg, "sandbox": {**cfg["sandbox"], "enabled": False}}, _env(world, calls=calls), {}, "2026-11-14", lambda s: None)
    assert out["enabled"] is False and calls == [] and _status(repo) == set()
    out = devloop.sandbox_stage({k: v for k, v in cfg.items() if k != "sandbox"}, _env(world, calls=calls), {}, "2026-11-14", lambda s: None)
    assert out["enabled"] is False and calls == []


def test_candidates_skip_marked_ideas_and_go_by_effort_then_id(world):
    _repo, _root, cfg, _base = world
    assert [d["id"] for _p, d in devloop.sandbox_candidates(cfg, devloop.load_ideas(cfg))] == ["20261113-a", "20261113-b"]


def test_passing_idea_is_recorded_and_only_two_files_reach_main(world):
    repo, root, cfg, base = world
    calls: list = []
    out = devloop.sandbox_stage(cfg, _env(world, calls=calls), {}, "2026-11-14", lambda s: None)
    assert out["todo"] == ["20261113-a", "20261113-b"] and [r["id"] for r in out["done"]] == ["20261113-a"] and out["failed"] == []
    r = out["done"][0]
    assert r["n_trials"] == 2 and r["verdicts"] == "落とす 2" and r["held"] is False and out["held"] == [] and out["cost"] == 3.5
    d = _doc(root, "20261113-a")
    assert d["status"] == "回した" and d["ran_in"] == "sandbox" and d["sandbox"]["n_trials"] == 2 and d["structure"] == "s"
    assert _doc(root, "20261113-b")["status"] == "共有コード変更"                              # 1 晩 1 本
    # main に増えるのは レジストリの 1 本 ＋ 結果の表 だけ。コードはブランチ
    assert _status(repo) == {f"{FD}/config/ideas/20261113-a.toml", "docs/specs/experiments/dev-loop/sandbox/20261113-a.md"}
    assert (root / "cli" / "run.py").read_text() == "VALUE = 'main'\n"
    assert _git(repo, "show", f"sandbox/20261113-a:{FD}/cli/run.py") == "VALUE = 'sandbox'\n"
    # claude はサンドボックスのワーキングツリーで呼ぶ・テスト → queue の順・queue の config はランナーが書く
    assert calls[0][:3] == ("claude", sandbox.tree("20261113-a", base), 20.0)
    kinds = [c[2][:2] for c in calls if c[0] == "run"]
    assert kinds == [("-m", "pytest"), ("-m", "cli.queue")]
    t = [c[2] for c in calls if c[0] == "run"][0]
    assert "tests/test_ideas_x.py" in t and {"tests/test_predict.py", "tests/test_trading_run.py"} <= set(t)
    q = _git(repo, "show", f"sandbox/20261113-a:{FD}/config/queue/dev_sbx_20261113_a.toml")
    assert 'experiments = ["dev_new"]' in q and "leak = true" in q
    # 次の晩は次の 1 本
    out2 = devloop.sandbox_stage(cfg, _env(world), {}, "2026-11-15", lambda s: None)
    assert [r["id"] for r in out2["done"]] == ["20261113-b"]


@pytest.mark.parametrize("writes, result, tests_rc, rows, why", [
    ({**GOOD, "TODO.md": "# 書き換えた\n"}, BUILT, 0, NEW_ROWS, "書いてはいけない場所"),
    ({**GOOD, f"{FD}/tests/test_old.py": "def test_old():\n    assert True or 'weak'\n"}, BUILT, 0, NEW_ROWS, "書いてはいけない場所"),
    (GOOD, "作った（つもり）", 0, NEW_ROWS, "experiments が無い"),
    ({k: v for k, v in GOOD.items() if "tests/" not in k}, BUILT, 0, NEW_ROWS, "test_ideas_"),
    (GOOD, BUILT, 1, NEW_ROWS, "テストが落ちた"),
    (GOOD, BUILT, 0, [], "1 本も回らなかった"),
])
def test_failing_idea_is_thrown_away_with_its_sandbox(world, writes, result, tests_rc, rows, why):
    repo, root, cfg, base = world
    calls: list = []
    env = _env(world, writes, result, tests_rc=tests_rc, rows=rows, calls=calls)
    out = devloop.sandbox_stage(cfg, env, {}, "2026-11-14", lambda s: None)
    assert out["done"] == [] and len(out["failed"]) == 1 and why in out["failed"][0]["reason"]
    d = _doc(root, "20261113-a")
    assert d["status"] == "共有コード変更" and d["sandbox_attempts"] == 1 and why in d["notes"] and "ran_in" not in d
    assert _status(repo) == {f"{FD}/config/ideas/20261113-a.toml"}                               # main に増えるのは理由を書いたレジストリだけ
    assert not sandbox.exists("20261113-a", base) and "sandbox/20261113-a" not in _git(repo, "branch", "--list", "sandbox/*")
    if why == "テストが落ちた":
        assert [c[2][:2] for c in calls if c[0] == "run"] == [("-m", "pytest")]                   # 落ちたら回さない
    # 2 回目も通らなければ「失敗」・3 晩目は次の案へ
    out = devloop.sandbox_stage(cfg, env, {}, "2026-11-15", lambda s: None)
    assert out["failed"][0]["status"] == "失敗" and _doc(root, "20261113-a")["status"] == "失敗"
    assert devloop.load_ideas(cfg) and all(not b for _p, _d, b in devloop.load_ideas(cfg))
    assert [d["id"] for _p, d in devloop.sandbox_candidates(cfg, devloop.load_ideas(cfg))] == ["20261113-b"]


def test_hold_is_flagged_in_the_log_and_the_mail_subject(world, tmp_path):
    _repo, _root, cfg, _base = world
    rows = [(0, "own.new.ridge.shared~dz@50", 1, "保留", 30.0), (0, "own.new.ridge.shared~dz@55", 1, "落とす", -1.0)]
    out = devloop.sandbox_stage(cfg, _env(world, rows=rows), {}, "2026-11-14", lambda s: None)
    assert out["held"] == ["20261113-a"] and out["done"][0]["verdicts"] == "保留 1・落とす 1"
    parts = {"stages": "0,1,2,3,4,5", "cost": 3.5, "sandbox": out, "ideate": {"counts": {"登録": 3}}, "run": {"picked": []}}
    assert devloop_mail.subject(parts, "2026-11-14", "[p]").endswith("・回した 0・サンドボックス 1（⚠ 保留以上あり）・$3.50")
    assert "保留以上あり ＝ 見てください" in devloop_mail.sandbox_block(parts)
    text = devloop.write_journal(cfg, "2026-11-14", {k: v for k, v in parts.items() if k not in ("ideate", "run")}, str(tmp_path / "j.md"))
    assert "## ステージ 5 サンドボックス" in text and "⚠ **保留以上あり ＝ 人が見る**" in text and "[結果の表](../sandbox/20261113-a.md)" in text
    # 動いていない晩は件名も日誌も今までどおり
    plain = {"stages": "0,1,2,3,4,5", "cost": 1.0, "ideate": {"counts": {"登録": 3}}, "run": {"picked": []}}
    assert "サンドボックス" not in devloop_mail.subject(plain, "2026-11-14", "[p]") and devloop_mail.sandbox_block(plain) == ""
    assert "サンドボックス" not in devloop.write_journal(cfg, "2026-11-14", {k: v for k, v in plain.items() if k not in ("ideate", "run")}, str(tmp_path / "j2.md"))


def test_claude_that_never_ran_does_not_burn_an_attempt(world):
    """利用枠 ／ 起動の失敗（1 ファイルも書かず・出力も無い）は案のせいではない ＝ 試した回数に数えず、サンドボックスは捨てる。"""
    repo, root, cfg, base = world
    env = _env(world, writes={}, result="")
    inner = env["claude"]
    env["claude"] = lambda *a, **k: {**inner(*a, **k), "result": "", "errors": ["usage limit reached"], "rc": 1, "cost": 0.0}
    out = devloop.sandbox_stage(cfg, env, {}, "2026-11-14", lambda s: None)
    assert out["done"] == [] and "claude -p が動かなかった" in out["failed"][0]["reason"]
    assert "sandbox_attempts" not in _doc(root, "20261113-a") and _status(repo) == set() and not sandbox.exists("20261113-a", base)


def test_only_runs_the_named_idea(world):
    _repo, root, cfg, _base = world
    out = devloop.sandbox_stage(cfg, _env(world), {}, "2026-11-14", lambda s: None, only="20261113-b")
    assert [r["id"] for r in out["done"]] == ["20261113-b"] and _doc(root, "20261113-a")["status"] == "共有コード変更"
