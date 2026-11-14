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
    # 自分の錠だけなら汚れていない（night が門の前に取る。2026-11-01）
    assert devloop.gate(cfg, FakeEnv(root, conn, dirty="?? experiments/feature-discovery/devloop.lock\n").env, devloop.load_state(conn)) is None
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


@pytest.mark.parametrize("raw, want", [
    # 穴 ②（2026-11-10）: θ の括弧書きの中の ／ で割って '55' '60）' がパターンに残っていた
    ("own.x.ridge.shared@θ（θ ＝ 50 ／ 55 ／ 60）", ["own.x.ridge.shared"]),
    ("own.x.ridge.shared@θ（θ ∈ 50 ／ 55 ／ 60）", ["own.x.ridge.shared"]),
    ("own.x.ridge.shared@θ (θ = 50, 55, 60)", ["own.x.ridge.shared"]),
    ("own.x.ridge.shared@θ（50 ／ 55 ／ 60。names.toml の [method] に 1 行足す）", ["own.x.ridge.shared"]),
    ("own.a.ridge.shared@θ ／ own.b.ridge.shared@θ（θ 50 ／ 55 ／ 60）", ["own.a.ridge.shared", "own.b.ridge.shared"]),
    ("own.a.ridge.shared@{50,55,60}（単体 3）／ own.b.ridge.shared@50", ["own.a.ridge.shared", "own.b.ridge.shared"]),
    ("own.a.ridge.shared@50|55|60 ／ own.a.lgbm.shared@50|55|60（対の相手 ＝ own.t1.ridge.shared@θ）",
     ["own.a.ridge.shared", "own.a.lgbm.shared"]),
    ("own.a.ridge.shared~night-leg@θ ／ own.a.ridge.shared~day-leg@θ", ["own.a.ridge.shared~night-leg", "own.a.ridge.shared~day-leg"]),
    ("own.x.ridge.shared@θ（各組（R19〜R22）の θ ／ 1 水準）", ["own.x.ridge.shared"]),                    # 入れ子
    ("own.x.ridge.shared@θ（θ ＝ 50 ／ 55 ／ 60", ["own.x.ridge.shared"]),                              # 閉じていない括弧
    ("own.x.ridge.shared ／ 50 ／ 55|60 ／ own.x.ridge.shared", ["own.x.ridge.shared"]),                  # 水準だけ ・ 同じ形の重ね
    (["own.x.ridge.shared@θ（θ ＝ 50 ／ 55 ／ 60）", "own.y.lgbm.shared"], ["own.x.ridge.shared", "own.y.lgbm.shared"]),
    ("物差しのみ（新しい識別項目なし）: 元の行 own.own35.ridge.shared@θ に null 分位を添える", []),
    ("", []), (None, []), ([], []),
])
def test_key_shapes_drop_theta_notes_before_splitting(raw, want):
    assert devloop._key_shapes(raw) == want


def test_key_shapes_keep_fragments_that_are_not_theta_levels():
    # ⚠ 捨てるのは θ の水準だけ。読めない断片は「当たらないパターン」として残す（捨てると残りが全部当たって新しい案が既出になる）
    ks = devloop._key_shapes("own.<a ／ b>.ridge.shared@50 ／ own.all.ridge.shared@50")
    assert ks == ["own.<a", "b>.ridge.shared", "own.all.ridge.shared"]
    assert [devloop.duplicates([p], ["own.all.ridge.shared"])[0] for p in ks] == [0, 0, 1]


def _raw_idea(key_shape, title="t", touches="新しいモジュールだけ"):
    return {"slug": "x", "title": title, "kind": "モデル", "question": "問い", "structure": "口", "n_trials_planned": 3,
            "key_shape": key_shape, "failure_modes": ["型"], "prediction": "予想", "effort": "小", "measurable": "測れる: 理由",
            "touches": touches}


def test_register_marks_known_shape_with_theta_note_as_duplicate(world):
    # 穴 ② の回帰: 検証結果一覧に既にある名前 ＋ θ の括弧書き → 既出。新しい名前 ・ 片方だけ新しい案は既出にしない
    root, cfg, conn = world
    models = ["own.all.ridge.shared", "own.all.ridge.shared~p1995", "own.all.lgbm.shared"]
    raws = [_raw_idea("own.all.ridge.shared@θ（θ ＝ 50 ／ 55 ／ 60）", "既にある形"),
            _raw_idea("own.all.ridge.shared@θ ／ own.all.lgbm.shared@θ（θ 50 ／ 55 ／ 60）", "2 本とも既にある"),
            _raw_idea("own.new-thing.ridge.shared@θ（θ ＝ 50 ／ 55 ／ 60）", "新しい形"),
            _raw_idea("own.new-thing.ridge.shared@θ（対の相手 own.all.ridge.shared@θ）", "対の相手だけ既にある"),
            _raw_idea("own.all.ridge.shared@θ ／ own.new-thing.ridge.shared@θ（θ 50 ／ 55 ／ 60）", "片方だけ新しい")]
    out = devloop.register_ideas(cfg, raws, source="independent", meta={}, date="2026-11-13", models=models)
    by = {d["title"]: d for d in out}
    assert [by[t]["status"] for t in ("既にある形", "2 本とも既にある", "新しい形", "対の相手だけ既にある", "片方だけ新しい")] == \
        ["既出", "既出", "登録", "登録", "登録"]
    assert by["既にある形"]["key_shape"] == ["own.all.ridge.shared"] and "55" not in by["既にある形"]["key_shape"]
    assert "原文: own.all.ridge.shared@θ（θ ＝ 50 ／ 55 ／ 60）" in by["既にある形"]["notes"]      # 原文は残す


# --- 似た過去の案（穴 ①・案 A ＝ 表示だけ。2026-11-13 利用者「Aで進めて」）---------------------------------

def _doc(id_, date, title, question, status="回した"):
    return {"id": id_, "date": date, "title": title, "question": question, "status": status}


def test_similar_map_orders_by_closeness_and_only_looks_back():
    me = _doc("20261108-me", "2026-11-08", "値動きの似た銘柄の集まりを見つけ、集まりの中の出遅れを買う", "集まりの中で遅れた銘柄は戻るか", "登録")
    near = _doc("20261106-near", "2026-11-06", "相関で銘柄の集まりを見つけ、集まりの中で遅れた銘柄を買う", "集まりの中で遅れた銘柄は戻るか")
    mid = _doc("20261104-mid", "2026-11-04", "出来高で銘柄の集まりを作る", "跳ねの翌日に遅れた銘柄を買うと勝つか", "人の手")
    none = _doc("20261101-none", "2026-11-01", "ABC", "XYZ")
    same_night = _doc("20261108-twin", "2026-11-08", me["title"], me["question"], "登録")           # 同じ晩の案は相手にしない
    later = _doc("20261109-later", "2026-11-09", me["title"], me["question"], "登録")              # 後の案も相手にしない
    docs = [later, none, mid, same_night, near, me]
    got = devloop.similar_map(docs)
    assert [r["id"] for r in got["20261108-me"]] == ["20261106-near", "20261104-mid"]               # 近い順 ・ 自分と近さ 0 は出さない
    assert got["20261108-me"][0]["score"] > got["20261108-me"][1]["score"] > 0
    assert got["20261108-me"][0] == {"id": "20261106-near", "title": near["title"], "status": "回した", "score": got["20261108-me"][0]["score"]}
    assert got["20261101-none"] == [] and got["20261106-near"] == [{"id": "20261104-mid", "title": mid["title"], "status": "人の手",
                                                                    "score": got["20261106-near"][0]["score"]}]
    assert [r["id"] for r in got["20261109-later"]][:2] == ["20261108-me", "20261108-twin"]         # 同じ近さは id の順
    assert got["20261109-later"][0]["score"] == 1.0
    assert [r["id"] for r in devloop.similar_map(docs, top=1)["20261108-me"]] == ["20261106-near"]
    assert set(devloop.similar_map(docs, only={"20261108-me"})) == {"20261108-me"}
    assert devloop.similar_map(docs, top=0) == {}
    # 全角 ／ 半角 ・ 空白 ・ 大文字小文字の違いは同じ文字として数える
    a, b = _doc("b", "2026-11-02", "ＬｉｇｈｔＧＢＭ の 局面", "q"), _doc("a", "2026-11-01", "lightgbmの局面", "q")
    assert devloop.similar_map([a, b])["b"][0]["score"] == 1.0
    sp = devloop.similar_spread(docs)
    assert sp is not None and 0.0 <= sp[0] <= sp[1] <= 1.0 and sp[2] == 14                         # 6 本 ＝ 15 対 − 同じ晩の 1 対
    assert devloop.similar_spread([me, same_night]) is None


def test_ideate_shows_similar_past_ideas_and_never_changes_status(world):
    root, cfg, conn = world
    past = {"id": "20261020-old-detector", "date": "2026-10-20", "title": "新しい検知器の前の版", "kind": "モデル", "origin": {"source": "independent"},
            "question": "問い", "structure": "s", "n_trials_planned": 3, "key_shape": ["own.h8-gate.ridge.shared"], "failure_modes": ["f"],
            "prediction": "p", "effort": "小", "measurable": "測れる: r", "touches": "新しいモジュールだけ", "status": "回した",
            "registered_at": "2026-10-20 00:00 PDT"}
    devloop.write_idea(str(root / "ideas" / "20261020-old-detector.toml"), past)
    before = (root / "ideas" / "20261020-old-detector.toml").read_bytes()
    fake = FakeEnv(root, conn)
    out = devloop.ideate(cfg, fake.env, "2026-10-26", lambda s: None, ["own.all.ridge.shared"])
    by = {d["title"]: d for d in out["ideas"]}
    sim = out["similar"]
    assert set(sim) == {d["id"] for d in out["ideas"]}                                           # この晩の案だけを引く
    assert [r["id"] for r in sim[by["新しい検知器"]["id"]]] == ["20261020-old-detector"]          # 同じ晩の 2 束目は相手にしない
    assert sim[by["新しい検知器"]["id"]][0]["status"] == "回した"
    assert out["counts"] == {"案": 2, "登録": 2, "既出": 2, "人の手": 2}                          # ⚠ 状態は今までと同じ（表示だけ）
    assert by["新しい検知器"]["status"] == "登録" and "similar" not in by["新しい検知器"]
    d = tomllib.loads((root / "ideas" / f"{by['新しい検知器']['id']}.toml").read_text(encoding="utf-8"))
    assert "similar" not in d and "似た" not in d.get("notes", "")                               # 登録簿には書かない
    assert (root / "ideas" / "20261020-old-detector.toml").read_bytes() == before
    text = devloop.write_journal(cfg, "2026-10-26", {"stages": "0,1", "ideate": out}, str(root / "log" / "j.md"))
    row = f"| `{by['新しい検知器']['id']}` | {sim[by['新しい検知器']['id']][0]['score']:.2f} | `20261020-old-detector` | 回した | 新しい検知器の前の版 |"
    assert "### 似た過去の案" in text and row in text and "表示だけ" in text
    reg = devloop.build_register(cfg, devloop.load_ideas(cfg), [], 1)
    assert "- 似た過去の案（近さ・表示だけ）: " in reg and "`20261020-old-detector` 新しい検知器の前の版（回した）" in reg
    assert "目盛りの読み方" in reg and "状態は変えない" in reg
    cfg["ideation"]["similar_top"] = 0                                                           # 0 ＝ 出さない
    assert "似た過去の案" not in devloop.build_register(cfg, devloop.load_ideas(cfg), [], 1)
    out0 = devloop.ideate(cfg, FakeEnv(root, conn).env, "2026-10-27", lambda s: None, [])
    assert out0["similar"] == {}
    assert "似た過去の案" not in devloop.write_journal(cfg, "2026-10-27", {"stages": "0,1", "ideate": out0}, str(root / "log" / "j2.md"))


def test_ideate_survives_a_broken_similarity(world, monkeypatch):
    root, cfg, conn = world
    monkeypatch.setattr(devloop, "similar_map", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("壊れた")))
    logs: list[str] = []
    out = devloop.ideate(cfg, FakeEnv(root, conn).env, "2026-10-26", logs.append, [])
    assert out["similar"] == {} and len(out["ideas"]) == 8 and any("似た過去の案を引けなかった" in m for m in logs)


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


# --- 週 1 回の候補の棚（rules.md 31-3 の 4・2026-11-01）--------------------------------------------------------

def _add_row(conn, trial, model, verdict, method, runs):
    doc = {"手法名": method, "判定": verdict, "上乗せbp": 120.0, "上乗せfold": "5/5 ＋＋＋＋＋", "閾値": "50", "検証方式": "閾値売買",
           "実行一覧": runs}
    conn.execute("INSERT INTO ledger_rows (leak, trial_name, model_name, is_trial, verdict, closed, first_run, last_run, runs, doc) VALUES (0, ?, ?, 1, ?, 0, '2026-10-31', '2026-10-31', ?, ?)",
                 (trial, model, verdict, json.dumps(runs), json.dumps(doc, ensure_ascii=False)))
    conn.commit()


def test_weekly_stage_drafts_single_method_and_leaves_mix_to_hand(world, tmp_path):
    root, cfg, conn = world
    (root / "config" / "experiment" / "trader_x.toml").write_text('name = "trader_x"\n[trading]\nstyle = "threshold"\n', encoding="utf-8")
    (root / "config" / "experiment" / "trader_m.toml").write_text(
        'name = "trader_m"\n[trading]\nstyle = "threshold"\n[[trading.mix]]\nname = "R99 組"\n', encoding="utf-8")
    _add_row(conn, "own.f9-1-tr5-600.ridge.shared@50", "own.f9-1-tr5-600.ridge.shared", "採る",
             "F9-1 試し〔トレーダー・5本・$600〕", ["2026-10-31T01-10-00_trader_x"])
    _add_row(conn, "own.r99-tr5-600.ridge.shared@50", "own.r99-tr5-600.ridge.shared", "採る",
             "R99 組〔トレーダー・5本・$600〕", ["2026-10-31T01-11-00_trader_m"])
    _add_row(conn, "own.f9-2-tr5-600.ridge.shared@50", "own.f9-2-tr5-600.ridge.shared", "保留",          # 保留は載らない
             "F9-2〔トレーダー・5本・$600〕", ["2026-10-31T01-12-00_trader_x"])
    _add_row(conn, "own.f9-1-trall.ridge.shared@50", "own.f9-1-trall.ridge.shared", "採る",               # 2b は載らない
             "F9-1 試し〔トレーダー・全銘柄・全部を買える予算〕", ["2026-10-31T01-10-00_trader_x"])
    drafts = tmp_path / "drafts"
    logs: list[str] = []
    out = devloop.weekly_stage(cfg, {"conn": conn}, "2026-10-31", logs.append, drafts_dir=str(drafts))
    assert sorted(r[0] for r in out["rows"]) == ["own.f9-1-tr5-600.ridge.shared@50", "own.r99-tr5-600.ridge.shared@50"]
    assert out["drafts"] == ["dev_own-f9-1-tr5-600-ridge-shared_50"]
    assert [n for n, _ in out["manual"]] == ["own.r99-tr5-600.ridge.shared@50"]
    text = (drafts / "dev_own-f9-1-tr5-600-ridge-shared_50.toml").read_text(encoding="utf-8")
    import tomllib
    d = tomllib.loads(text)
    assert d["budget_usd"] == 600.0 and d["threshold"] == 50.0 and d["models"] == [
        {"kind": "experiment", "name": "trader_x", "method": "F9-1 試し"}]
    # 2 度目は書き換えない（利用者が手で直した下書きを壊さない）
    (drafts / "dev_own-f9-1-tr5-600-ridge-shared_50.toml").write_text(text + "# 手で直した\n", encoding="utf-8")
    devloop.weekly_stage(cfg, {"conn": conn}, "2026-11-07", logs.append, drafts_dir=str(drafts))
    assert (drafts / "dev_own-f9-1-tr5-600-ridge-shared_50.toml").read_text(encoding="utf-8").endswith("# 手で直した\n")


def test_weekly_runs_only_on_saturday_and_is_in_the_journal(world, tmp_path):
    root, cfg, conn = world
    assert devloop.is_weekly_night("2026-10-31") and not devloop.is_weekly_night("2026-10-26")
    fake = FakeEnv(root, conn)
    fake.env["drafts_dir"] = str(tmp_path / "drafts")
    logs: list[str] = []
    parts = devloop.night(cfg, fake.env, date="2026-10-31", stages=(4,), log=logs.append)
    assert parts["weekly"]["rows"] == []
    text = (root / "log" / "2026-10-31.md").read_text(encoding="utf-8")
    assert "週 1 回の候補の棚" in text
    parts = devloop.night(cfg, fake.env, date="2026-10-26", stages=(4,), log=logs.append)
    assert "weekly" not in parts


# --- 月 1 回の棚卸しの材料（プラン 4-4・2026-11-01）---------------------------------------------------------

def test_inventory_groups_open_holds_by_idea_id(world):
    from cli import select_candidates as sc
    root, cfg, conn = world
    exp_dir = root / "config" / "experiment"
    (exp_dir / "dev_foo.toml").write_text("# ⚠ 開発ループの登録簿 `20261101-foo`（参考なし）\nname = \"dev_foo\"\n", encoding="utf-8")
    _add_row(conn, "own.foo.ridge.shared@50", "own.foo.ridge.shared", "保留", "Foo", ["2026-11-01T01-00-00_dev_foo"])
    _add_row(conn, "own.r1-tr5-600.ridge.shared@50", "own.r1-tr5-600.ridge.shared", "保留", "R1〔トレーダー・5本・$600〕", ["2026-10-18T10-00-00_trader_roles_t7"])
    _add_row(conn, "own.r2-tr5-600.ridge.shared@50", "own.r2-tr5-600.ridge.shared", "保留", "R2〔トレーダー・5本・$600〕", ["2026-10-18T10-01-00_trader_roles_t8b"])
    _add_row(conn, "own.x.ridge.shared@50", "own.x.ridge.shared", "落とす", "X", ["2026-10-18T10-02-00_trader_roles_t9"])   # 落とすは載らない
    conn.execute("UPDATE ledger_rows SET closed = 1 WHERE trial_name = 'own.f9-1.ridge.shared@50'")                       # 閉じた保留は載らない
    conn.commit()
    groups = sc.inventory(conn, experiment_dir=str(exp_dir))
    assert {g["group"]: g["n"] for g in groups} == {"trader_roles": 2, "登録簿 20261101-foo": 1}
    assert groups[0]["group"] == "trader_roles"                      # 行の多い順
    text = sc.render_inventory(groups, 100)
    assert "3 行 ／ 2 区分" in text and "閉じて" in text


# ---------- 31-6（2026-11-01 利用者「未知なものへのトライが増えたらいいな」→「推す案で」）: 枠・確率つきの予想・差なしで分かること・当たり方

IDEA_V2 = '''```toml
[[idea]]
slug = "vol-target"
title = "値動きの大きさを当てる"
kind = "検証方式"
question = "問い"
structure = "口"
n_trials_planned = 3
key_shape = ["own.volpred.ridge.shared"]
failure_modes = ["型"]
prediction = "予想"
effort = "中"
measurable = "測れる: 理由"
touches = "新しいモジュールだけ"
frame = "外"
p_hold = 0.35
learn_if_null = "分かること"

[[idea]]
slug = "no-prob"
title = "確率が無い"
kind = "モデル"
question = "問い"
structure = "口"
n_trials_planned = 3
key_shape = ["own.noprob.ridge.shared"]
failure_modes = ["型"]
prediction = "予想"
effort = "小"
measurable = "測れる: 理由"
touches = "新しいモジュールだけ"
frame = "内"
```'''


def test_contract2_requires_frame_probability_and_learning_and_counts_outside(world):
    root, cfg, conn = world
    cfg["ideation"]["contract"] = 2
    cfg["ideation"]["outside_frame_min"] = 2
    fake = FakeEnv(root, conn, claude_result=IDEA_V2)
    logs = []
    out = devloop.ideate(cfg, fake.env, "2026-11-02", logs.append, [])
    by = {d["title"]: d for d in out["ideas"]}
    assert by["値動きの大きさを当てる"]["status"] == "登録" and by["値動きの大きさを当てる"]["p_hold"] == 0.35
    assert by["確率が無い"]["status"] == "案" and "p_hold" in by["確率が無い"]["notes"]
    assert out["outside"] == 2 and "outside_short" not in out                # 2 束 × 外 1 本
    cfg["ideation"]["outside_frame_min"] = 3
    out = devloop.ideate(cfg, fake.env, "2026-11-03", logs.append, [])
    assert out["outside_short"] == "枠の外 2 本 ＜ 下限 3 本" and any("枠の外" in m for m in logs)


def test_contract3_requires_needed_effect_and_older_ideas_stay_valid(world):
    # 31-6 の 5（2026-11-13）: 契約 3 の案は「保留以上に要る効果の大きさ」を回す前に書く。契約 2 までの登録簿には求めない
    root, cfg, conn = world
    cfg["ideation"]["contract"] = 3
    with_effect = IDEA_V2.replace('learn_if_null = ', 'needed_effect = "行ごとの信号で 20 bp/日 級（検出力の実測では届かない見込み）"\nlearn_if_null = ', 1)
    assert with_effect.count("needed_effect") == 1
    out = devloop.ideate(cfg, FakeEnv(root, conn, claude_result=with_effect).env, "2026-11-14", lambda s: None, [])
    st = [d["status"] for d in out["ideas"] if d.get("needed_effect")]
    assert st and set(st) == {"登録"}                                          # 書いた案は通る
    out2 = devloop.ideate(cfg, FakeEnv(root, conn, claude_result=IDEA_V2).env, "2026-11-15", lambda s: None, [])
    missing = [d for d in out2["ideas"] if d["title"] == "値動きの大きさを当てる"]
    assert missing and all(d["status"] == "案" and "needed_effect" in d["notes"] for d in missing)   # 書いていない案は 案 のまま
    saved = tomllib.loads(open(root / "ideas" / f"{[d for d in out['ideas'] if d.get('needed_effect')][0]['id']}.toml", "rb").read().decode())
    assert saved["needed_effect"].startswith("行ごとの信号") and saved["origin"]["contract"] == 3
    reg = devloop.build_register(cfg, devloop.load_ideas(cfg), [], 1)
    assert "保留以上に要る効果の大きさ（先に書いた）: 行ごとの信号で 20 bp/日 級" in reg
    old = {"id": "x", "date": "2026-11-02", "title": "t", "kind": "モデル", "origin": {"source": "independent", "contract": 2}, "question": "q",
           "structure": "s", "n_trials_planned": 3, "key_shape": ["a.b.c.shared"], "failure_modes": ["f"], "prediction": "p", "effort": "小",
           "measurable": "測れる: r", "touches": "新しいモジュールだけ", "status": "案", "frame": "内", "p_hold": 0.2, "learn_if_null": "分かること"}
    assert devloop.validate(old, "x.toml") == []                               # 契約 2 は needed_effect が無くても通る
    old["origin"]["contract"] = 3
    assert any("needed_effect" in b for b in devloop.validate(old, "x.toml"))


def test_calibration_counts_hits_and_warns_when_too_safe():
    ideas = [("p", {"id": f"i{k}", "status": "回した", "p_hold": 0.1, "runs": [f"r{k}"]}, []) for k in range(5)]
    outcomes = {f"r{k}": {"落とす"} for k in range(5)}
    cal = devloop.calibration(ideas, outcomes)
    assert cal["n"] == 5 and cal["acc"] == 1.0 and abs(cal["brier"] - 0.01) < 1e-9
    text = devloop.build_register({"ideation": {"reference_pct": 30, "ideas_per_night": 5}}, ideas, [], 1, outcomes)
    assert "探り方が安全すぎる" in text
    outcomes["r0"] = {"保留"}
    cal = devloop.calibration(ideas, outcomes)
    assert cal["acc"] == 0.8
    assert devloop.calibration([("p", {"id": "x", "status": "回した", "runs": ["r0"]}, [])], outcomes)["n"] == 0   # 確率の無い案は数えない
