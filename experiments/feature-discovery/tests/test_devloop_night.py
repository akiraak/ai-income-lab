"""開発ループの運転手 `cli.devloop night`（rules.md 31 章・プラン Phase 3）。外の世界（git・claude -p・cli.queue・pytest）は env で差し替える。

⚠ 段 0 門: HALT ／ 錠 ／ 汚れた木 ／ pull の失敗 ／ 打ち切り ② ③ で止まり、それでも段 4 と日誌は書く。
⚠ 段 1: 出力を登録簿に写し、既出（識別項目が既にある）／ 人の手（共有の経路）／ 登録（registered_at）に分ける。項目不足は 案 のまま。
⚠ 段 3: 1 晩の上限（数える検証数）に収まる分だけ queue に並べ、回し切れた案だけ 回した に。段 4: 初回は写すだけ・新顔だけ机上の config。
"""

from __future__ import annotations

import json
import os
import tomllib

import pytest

from ail import rundb
from cli import devloop

IDEA_TOML = '''```toml
[[idea]]
slug = "mkt-layer"
title = "市場の層"
kind = "データ"
question = "問い"
structure = "口"
n_trials_planned = 6
key_shape = "own-mkt.all.ridge.shared@θ ／ own-mkt.all.lgbm.shared@θ（対の相手 own.all.ridge.shared）"
failure_modes = ["型"]
prediction = "予想"
effort = "中"
measurable = "測れる: 理由"
touches = "共有の経路: ail/features"

[[idea]]
slug = "New Detector!!"
title = "新しい検知器"
kind = "モデル"
question = "問い"
structure = "口"
n_trials_planned = 3
key_shape = ["own.h9-gate.ridge.shared"]
failure_modes = ["型"]
prediction = "予想"
effort = "小"
measurable = "測れる: 理由"
touches = "新しいモジュールだけ"

[[idea]]
title = "既にある形"
kind = "モデル"
question = "問い"
structure = "口"
n_trials_planned = 3
key_shape = "own.all.ridge.shared@50"
failure_modes = ["型"]
prediction = "予想"
effort = "小"
measurable = "測れる: 理由"
touches = "新しいモジュールだけ"

[[idea]]
title = "項目が足りない"
kind = "モデル"
question = "問い"
```
'''


class FakeEnv:
    """外の世界の写し。`calls` に呼ばれた順を残す。"""

    def __init__(self, tmp, conn, *, dirty="", pull_rc=0, claude_result=IDEA_TOML, claude_errors=None, queue_ok=True):
        self.calls: list = []
        self.dirty, self.pull_rc, self.claude_result, self.claude_errors, self.queue_ok = dirty, pull_rc, claude_result, claude_errors or [], queue_ok
        self.env = {"root": str(tmp), "repo": str(tmp), "sh": self.sh, "claude": self.claude, "queue": self.queue, "tests": self.tests,
                    "conn": conn, "now": lambda: __import__("datetime").datetime(2026, 10, 26, 1, 0)}

    def sh(self, cmd, cwd=None, timeout=None):
        self.calls.append(("sh", cmd[:3]))
        if cmd[:2] == ["git", "status"]:
            return 0, self.dirty, ""
        if cmd[:2] == ["git", "pull"]:
            return self.pull_rc, "", "rejected" if self.pull_rc else ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "main\n" if "--abbrev-ref" in cmd else "abc1234\n", ""
        if cmd[:2] == ["git", "diff"]:
            return 1, "", ""                          # 変更あり
        return 0, "", ""

    def claude(self, prompt, *, tools, max_turns, budget, cwd, timeout=None):
        self.calls.append(("claude", tuple(tools), round(budget, 2)))
        return {"result": self.claude_result, "cost": 1.25, "turns": 3, "errors": list(self.claude_errors), "rc": 0}

    def queue(self, name, log):
        self.calls.append(("queue", name))
        cfgp = os.path.join(self.env["root"], "config", "queue", f"{name}.toml")
        with open(cfgp, "rb") as f:
            exps = tomllib.load(f)["experiments"]
        items = []
        for e in exps:
            items.append({"experiment": e, "leak": False, "status": "done" if self.queue_ok else "failed", "run_dir": f"2026-10-26T01-10-00_{e}"})
            items.append({"experiment": e, "leak": True, "status": "done", "run_dir": f"2026-10-26T01-11-00_{e}_leak"})
        return {"items": items}

    def tests(self, targets):
        self.calls.append(("tests", tuple(targets)))
        return 0, "ok"


def _cfg(tmp, **over):
    root = str(tmp)
    cfg = {"ideation": {"reference_pct": 30, "ideas_per_night": 5, "prompts": "prompts", "max_turns": 4, "max_budget_usd": 6.0,
                        "budget_split": {"ref": 0.7, "free": 0.3}},
           "build": {"mode": "manual", "max_turns": 10, "max_budget_usd": 5.0, "retries_per_idea": 2},
           "run": {"time_budget_s": 600, "max_failures": 1, "leak": True, "ledger": True, "max_trials_per_night": 12, "heavy_per_night": 1},
           "select": {"top": 3, "shape_new_only": True, "candidate_verdicts": ["採る"], "weekly_day": "Fri"},
           "schedule": {"time_local": "01:00", "timezone": "America/Los_Angeles", "total_hours": 3},
           "stop": {"halt_file": os.path.join(root, "DEVLOOP_HALT"), "lock_file": os.path.join(root, "devloop.lock"),
                    "consecutive_build_failures": 3, "consecutive_no_new": 14},
           "git": {"commit": True, "push": False, "branch": "main", "dirty_tree": "stop", "pull": True},
           "paths": {"ideas": os.path.join(root, "ideas"), "register": os.path.join(root, "register.md"), "log": os.path.join(root, "log")},
           "allowlist": {"write": ["config/ideas/", "config/experiment/dev_", "ail/ideas/", "tests/test_ideas_"],
                         "forbid": ["cli/run.py", "ail/validation/", "../../TODO.md"]}}
    for k, v in over.items():
        cfg[k] = {**cfg[k], **v} if isinstance(v, dict) else v
    return cfg


@pytest.fixture
def world(tmp_path, monkeypatch):
    """一時の ROOT（config/queue・config/experiment・prompts・ideas・log）と一時の DB。"""
    root = tmp_path
    for d in ("config/queue", "config/experiment", "prompts", "ideas", "log"):
        (root / d).mkdir(parents=True)
    (root / "prompts" / "contract.md").write_text("契約\n", encoding="utf-8")
    (root / "prompts" / "ideate_ref.md").write_text("参考あり {N} 本\n", encoding="utf-8")
    (root / "prompts" / "ideate_free.md").write_text("参考なし {N} 本\n", encoding="utf-8")
    (root / "prompts" / "build.md").write_text("作る\n", encoding="utf-8")
    monkeypatch.setattr(devloop, "ROOT", str(root))
    monkeypatch.setattr(devloop, "REPO", str(root))
    cfg = _cfg(root)
    monkeypatch.setattr(devloop, "load_config", lambda: cfg)
    conn = rundb.connect(str(root / "research.sqlite"))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('ledger_n_trials', '100')")
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('ledger_built_at', '2026-10-25T00-00-00')")
    doc = {"手法名": "F9-1 試し", "判定": "保留", "上乗せbp": 50.0, "上乗せfold": "3/5 ＋＋−−＋", "閾値": "50", "検証方式": "閾値売買",
           "粒度": "日足", "層": "adjusted", "較正": "std", "期間": "2018-01-31", "銘柄": "63", "集合": "daily", "モデル": "Ridge", "特徴量の層": "own", "実行一覧": []}
    conn.execute("INSERT INTO ledger_rows (leak, trial_name, model_name, is_trial, verdict, closed, first_run, last_run, runs, doc) VALUES (0, ?, ?, 1, ?, 0, '2026-10-01', '2026-10-01', '[]', ?)",
                 ("own.f9-1.ridge.shared@50", "own.f9-1.ridge.shared", "保留", json.dumps(doc, ensure_ascii=False)))
    conn.commit()
    yield root, cfg, conn
    conn.close()


def _night(world, fake, **kw):
    root, cfg, conn = world
    logs: list[str] = []
    parts = devloop.night(cfg, fake.env, date="2026-10-26", log=logs.append, **kw)
    return parts, logs


# --- 段 0 ---------------------------------------------------------------------------------------------

def test_gate_stops_on_halt_dirty_tree_pull_and_counters(world):
    root, cfg, conn = world
    fake = FakeEnv(root, conn)
    assert devloop.gate(cfg, fake.env, devloop.load_state(conn)) is None
    (root / "DEVLOOP_HALT").write_text("", encoding="utf-8")
    assert "止める手" in devloop.gate(cfg, fake.env, devloop.load_state(conn))
    (root / "DEVLOOP_HALT").unlink()
    assert "汚れている" in devloop.gate(cfg, FakeEnv(root, conn, dirty=" M TODO.md\n").env, devloop.load_state(conn))
    assert "pull" in devloop.gate(cfg, FakeEnv(root, conn, pull_rc=1).env, devloop.load_state(conn))
    st = devloop.load_state(conn)
    st["consecutive_build_failures"] = 3
    assert "打ち切り ②" in devloop.gate(cfg, fake.env, st)
    st = devloop.load_state(conn)
    st["consecutive_no_new"] = 14
    assert "打ち切り ③" in devloop.gate(cfg, fake.env, st)


def test_stopped_night_still_runs_stage4_and_writes_the_journal(world):
    root, cfg, conn = world
    (root / "DEVLOOP_HALT").write_text("", encoding="utf-8")
    fake = FakeEnv(root, conn)
    parts, logs = _night(world, fake)
    assert parts["stopped"].startswith("段 0") and "select" in parts and "ideate" not in parts
    assert not any(c[0] in ("claude", "queue") for c in fake.calls)
    text = (root / "log" / "2026-10-26.md").read_text(encoding="utf-8")
    assert "止まった: 段 0" in text and "段 4 選ぶ" in text and "初回は写すだけ" in text and "未コミット" in text
    assert not (root / "devloop.lock").exists()


def test_lock_blocks_a_second_runner(world):
    root, cfg, conn = world
    assert devloop.take_lock(str(root / "devloop.lock"))
    parts, _ = _night(world, FakeEnv(root, conn))
    assert "錠" in parts["stopped"]
    devloop.release_lock(str(root / "devloop.lock"))
    assert devloop.take_lock(str(root / "devloop.lock"))
    (root / "devloop.lock").write_text("999999 x\n", encoding="utf-8")       # 死んだ pid の錠は取り直す
    assert devloop.take_lock(str(root / "devloop.lock"))


# --- 段 1 ---------------------------------------------------------------------------------------------

def test_parse_and_key_shapes():
    ideas = devloop.parse_ideas(IDEA_TOML)
    assert len(ideas) == 4 and ideas[0]["slug"] == "mkt-layer"
    assert devloop._key_shapes(ideas[0]["key_shape"]) == ["own-mkt.all.ridge.shared", "own-mkt.all.lgbm.shared"]
    assert devloop._key_shapes(["own.h9-gate.ridge.shared"]) == ["own.h9-gate.ridge.shared"]
    assert devloop._slug(ideas[1], "x") == "new-detector" and devloop._slug({}, "fb") == "fb"
    assert devloop.parse_ideas("not toml at all = [") == []


def test_ideate_registers_and_classifies(world):
    root, cfg, conn = world
    fake = FakeEnv(root, conn)
    logs = []
    models = ["own.all.ridge.shared", "own.all.ridge.shared~p1995"]
    out = devloop.ideate(cfg, fake.env, "2026-10-26", logs.append, models)
    assert out["n_ref"] == 2 and out["n_free"] == 3 and out["cost"] == 2.5
    tools = [c[1] for c in fake.calls if c[0] == "claude"]
    assert tools == [("Read", "Grep", "Glob"), ()] and [c[2] for c in fake.calls if c[0] == "claude"] == [4.2, 1.8]
    by = {d["title"]: d for d in out["ideas"]}
    assert by["市場の層"]["status"] == "人の手" and by["新しい検知器"]["status"] == "登録" and by["新しい検知器"]["registered_at"]
    assert by["既にある形"]["status"] == "既出" and by["項目が足りない"]["status"] == "案"
    assert out["counts"] == {"案": 2, "登録": 2, "既出": 2, "人の手": 2}        # 2 束 × 4 本
    files = sorted(p.name for p in (root / "ideas").glob("*.toml"))
    assert "20261026-mkt-layer.toml" in files and "20261026-mkt-layer-2.toml" in files     # 2 束目は id を重ねない
    d = tomllib.loads((root / "ideas" / "20261026-new-detector.toml").read_text(encoding="utf-8"))
    assert d["origin"]["source"] == "reference" and d["origin"]["prompt"] == "ideate_ref" and d["key_shape"] == ["own.h9-gate.ridge.shared"]


def test_ideate_retries_once_on_error(world):
    root, cfg, conn = world
    fake = FakeEnv(root, conn, claude_result="", claude_errors=["Reached maximum budget ($4.2)"])
    out = devloop.ideate(cfg, fake.env, "2026-10-26", lambda s: None, [])
    assert sum(1 for c in fake.calls if c[0] == "claude") == 4 and out["ideas"] == [] and out["bundles"]["ref"]["tries"] == 2


# --- 段 2（人の手）・段 3・段 4 ------------------------------------------------------------------------

def _idea(root, id_, status, n, effort="小", exps=None):
    d = {"id": id_, "date": "2026-10-26", "title": id_, "kind": "モデル", "origin": {"source": "independent"}, "question": "q",
         "structure": "s", "n_trials_planned": n, "key_shape": ["x.y.z.shared"], "failure_modes": ["f"], "prediction": "p", "effort": effort,
         "measurable": "測れる: r", "touches": "新しいモジュールだけ", "status": status, "registered_at": "2026-10-25 00:00 PDT"}
    if exps:
        d["experiments"] = exps
    devloop.write_idea(str(root / "ideas" / f"{id_}.toml"), d)


def test_plan_queue_respects_the_cap_and_pending_shapes(world):
    root, cfg, conn = world
    _idea(root, "20261026-a", "作成中", 6, "中", ["dev_a"])
    _idea(root, "20261026-b", "作成中", 3, "小", ["dev_b", "dev_b2"])
    _idea(root, "20261026-c", "作成中", 9, "小", ["dev_c"])
    _idea(root, "20261026-d", "登録", 1, "小")
    exps, picked, count = devloop.plan_queue(cfg, devloop.load_ideas(cfg), {"pending_shapes": ["dev_tr_x_50"]})
    assert exps == ["dev_tr_x_x".replace("x_x", "x_50"), "dev_b", "dev_b2", "dev_a"] and picked == ["20261026-b", "20261026-a"] and count == 11


def test_run_stage_marks_ideas_and_journal_commit(world):
    root, cfg, conn = world
    for e in ("dev_a", "dev_b"):
        (root / "config" / "experiment" / f"{e}.toml").write_text(f'name = "{e}"\n', encoding="utf-8")
    _idea(root, "20261026-a", "作成中", 6, "中", ["dev_a"])
    _idea(root, "20261026-b", "作成中", 3, "小", ["dev_b"])
    fake = FakeEnv(root, conn)
    parts, logs = _night(world, fake, stages=(0, 2, 3, 4))
    assert parts["build"]["mode"] == "manual" and parts["run"]["queue"] == "dev_20261026" and parts["run"]["planned"] == 9
    q = tomllib.loads((root / "config" / "queue" / "dev_20261026.toml").read_text(encoding="utf-8"))
    assert q["experiments"] == ["dev_b", "dev_a"] and q["leak"] is True and q["max_failures"] == 1
    for id_ in ("20261026-a", "20261026-b"):
        d = tomllib.loads((root / "ideas" / f"{id_}.toml").read_text(encoding="utf-8"))
        assert d["status"] == "回した" and d["runs"] == [f"2026-10-26T01-10-00_dev_{id_[-1]}"]
    assert parts["commit"].startswith("コミット abc1234") and ("sh", ["git", "commit", "-q"]) in fake.calls
    text = (root / "log" / "2026-10-26.md").read_text(encoding="utf-8")
    assert "queue dev_20261026" in text and "コミット abc1234" in text and "段 2 作る — mode manual" in text
    st = devloop.load_state(conn)
    assert st["last_night"] == "2026-10-26" and st["last_top"] == ["own.f9-1.ridge.shared@50"] and st["nights"][-1]["date"] == "2026-10-26"
    # 2 晩目: 失敗した queue は 回した にしない・持ち越しは残る
    _idea(root, "20261026-c", "作成中", 3, "小", ["dev_c"])
    (root / "config" / "experiment" / "dev_c.toml").write_text('name = "dev_c"\n', encoding="utf-8")
    fake2 = FakeEnv(root, conn, queue_ok=False)
    parts2 = devloop.night(cfg, fake2.env, date="2026-10-27", stages=(3,), log=lambda s: None)
    assert parts2["run"]["queue"] == "dev_20261027" and parts2["run"]["failed"] == ["dev_c"]
    assert tomllib.loads((root / "ideas" / "20261026-c.toml").read_text(encoding="utf-8"))["status"] == "作成中"


def test_select_stage_first_night_records_only_then_new_entries_are_shaped(world, monkeypatch):
    root, cfg, conn = world
    fake = FakeEnv(root, conn)
    st = devloop.load_state(conn)
    out = devloop.select_stage(cfg, fake.env, st, "2026-10-26", lambda s: None)
    assert out["first"] and out["new"] == [] and st["last_top"] == ["own.f9-1.ridge.shared@50"]
    # 上位が入れ替わった晩: 新顔の机上の config を書く（trader_shape は差し替え）
    st["last_top"] = ["other@50"]
    from cli import trader_shape as ts
    monkeypatch.setattr(ts, "resolve_trial", lambda conn, name: {"experiment": "exp_x", "method": "全部使う（基準）", "theta": "50", "run": "r"})
    monkeypatch.setattr(ts, "source_text", lambda exp: 'name = "exp_x"\n')
    monkeypatch.setattr(ts, "source_path", lambda name: str(root / "config" / "experiment" / f"{name}.toml"))
    monkeypatch.setattr(ts, "shape_config", lambda text, **kw: f'name = "{kw["name"]}"\n')
    out = devloop.select_stage(cfg, fake.env, st, "2026-10-27", lambda s: None)
    assert out["new"] == ["own.f9-1.ridge.shared@50"] and out["shaped"] == ["dev_tr_exp-x_50"] and st["pending_shapes"] == ["dev_tr_exp-x_50"]
    assert (root / "config" / "experiment" / "dev_tr_exp-x_50.toml").exists()


def test_allowed_path_rules(world):
    root, cfg, conn = world
    assert devloop.allowed_path(cfg, "config/ideas/x.toml") and devloop.allowed_path(cfg, "config/experiment/dev_x.toml")
    assert devloop.allowed_path(cfg, "ail/ideas/i1.py") and devloop.allowed_path(cfg, "tests/test_ideas_x.py")
    assert not devloop.allowed_path(cfg, "config/experiment/trade_own_ridge_a.toml")
    assert not devloop.allowed_path(cfg, "cli/run.py") and not devloop.allowed_path(cfg, "ail/validation/simulate.py")
    assert not devloop.allowed_path(cfg, "../../TODO.md") and not devloop.allowed_path(cfg, "tests/test_fwd.py")


def test_dry_run_calls_nothing_outside(world):
    root, cfg, conn = world
    fake = FakeEnv(root, conn)
    parts, _ = _night(world, fake, dry_run=True)
    assert not any(c[0] in ("claude", "queue") for c in fake.calls) and "commit" not in parts and "select" in parts
    assert devloop.load_state(conn).get("last_night") is None                 # 状態も書かない
