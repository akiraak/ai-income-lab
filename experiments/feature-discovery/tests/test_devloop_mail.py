"""開発ループの報告メール と 門の「自分の置き場」（プラン docs/plans/devloop-mail.md・2026-11-03）。

⚠ 本物のメールは飛ばさない: 送り手は差し替える（env["mail"]）か、AIL_DEVLOOP_MAIL_OUTBOX でファイルに書く。秘密は一時置き場の偽物。
⚠ 門: コミットの後に書き直した日誌・止まった晩の日誌 ／ register.md ／ 下書きを「汚れた木」に数えない（数えると 2 晩目から毎晩止まる）。
"""

from __future__ import annotations

import os
import subprocess

import pytest

from cli import devloop
from cli import devloop_mail as dm
from tests.test_devloop_night import FakeEnv, _night, world  # noqa: F401  （world は fixture）

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
FAKE_PW = "pw-not-real-123"


def _secrets(tmp, **over) -> str:
    vals = {"AIL_MAIL_HOST": "smtp.example.invalid", "AIL_MAIL_PORT": "587", "AIL_MAIL_USER": "me@example.invalid",
            "AIL_MAIL_PASSWORD": FAKE_PW, "AIL_MAIL_TO": "me@example.invalid", **over}
    p = os.path.join(str(tmp), "mail.env")
    with open(p, "w", encoding="utf-8") as f:
        f.write("# 試験\n" + "".join(f"{k}={v}\n" for k, v in vals.items() if v is not None))
    os.chmod(p, 0o600)
    return p


class Outbox:
    def __init__(self, fail: int = 0):
        self.sent: list[tuple[str, str]] = []
        self.html: list[str | None] = []
        self.fail = fail

    def __call__(self, secrets, subj, text, timeout=30, html=None):
        if self.fail:
            self.fail -= 1
            raise OSError("一時的な失敗")
        self.sent.append((subj, text))
        self.html.append(html)


def _mail_world(world, tmp_path, **mail):
    root, cfg, conn = world
    cfg["mail"] = {"enabled": True, "send": "always", "secrets": _secrets(tmp_path), "subject_prefix": "[t]", "max_body_chars": 20000, **mail}
    return root, cfg, conn


# ---------------------------------------------------------------- 門（Phase 1）


def test_gate_ignores_own_outputs_but_not_other_files(world):
    root, cfg, conn = world
    st = devloop.load_state(conn)
    own = ("?? log/2026-10-26.md\n M log/2026-10-25.md\n M register.md\n")
    assert devloop.gate(cfg, FakeEnv(root, conn, dirty=own).env, st) is None
    env = FakeEnv(root, conn, dirty="?? drafts/\n").env
    env["drafts_dir"] = str(root / "drafts")
    assert devloop.gate(cfg, env, st) is None
    why = devloop.gate(cfg, FakeEnv(root, conn, dirty=own + " M TODO.md\n").env, st)
    assert why and "TODO.md" in why and "log/" not in why
    # ⚠ 置き場の名前で始まるだけの別のファイルは数える
    assert devloop.gate(cfg, FakeEnv(root, conn, dirty="?? log-notes.md\n").env, st)


def test_is_own_never_matches_everything():
    assert not devloop._is_own("TODO.md", ["", ".", "../x"])
    assert devloop._is_own("docs/x/log/a.md", ["docs/x/log"]) and devloop._is_own("docs/x/log/", ["docs/x/log"])


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_two_nights_with_real_git_second_night_passes_the_gate(world):
    """本物の git: 1 晩目はコミットの後に日誌を書き直す → 2 晩目の門は通る（直す前は「汚れている」で止まった）。"""
    root, cfg, conn = world
    cfg["git"] = {**cfg["git"], "pull": False, "push": False}
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text("research.sqlite*\ndevloop.lock\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    env = FakeEnv(root, conn).env
    env["sh"] = devloop._sh                                  # git だけ本物（claude ・queue は呼ばない段 0・4）
    p1 = devloop.night(cfg, env, date="2026-10-26", stages=(0, 4), log=lambda *_: None)
    assert not p1.get("stopped") and p1["commit"].startswith("コミット")
    rc, out, _ = devloop._sh(["git", "status", "--porcelain"], cwd=str(root))
    assert "log/2026-10-26.md" in out                        # 書き直した日誌が残っている（＝ 直す前はここで止まった）
    p2 = devloop.night(cfg, env, date="2026-10-27", stages=(0, 4), log=lambda *_: None)
    assert not p2.get("stopped"), p2.get("stopped")
    # 止まった晩（HALT）の日誌も、その次の晩の門を止めない
    (root / "DEVLOOP_HALT").write_text("", encoding="utf-8")
    assert devloop.night(cfg, env, date="2026-10-28", stages=(0, 4), log=lambda *_: None)["stopped"]
    os.remove(root / "DEVLOOP_HALT")
    assert not devloop.night(cfg, env, date="2026-10-29", stages=(0, 4), log=lambda *_: None).get("stopped")


# ---------------------------------------------------------------- 件名・本文


def test_subject_for_ok_stopped_and_failed_nights():
    ok = {"ideate": {"counts": {"登録": 3, "人の手": 2}}, "run": {"before": 1120, "after": 1132, "picked": ["a", "b"]}, "select": {"new": ["a"]}, "cost": 5.24}
    assert dm.subject(ok, "2026-11-04", "[p]") == "[p] 11/4（水）✅ アイデア 5（登録 3・人の手 2）・回した 2・$5.24"
    none_run = {"ideate": {"counts": {"登録": 1}}, "run": {"before": 1120, "after": None}, "cost": 1.0}
    assert "None" not in dm.subject(none_run, "2026-11-04", "[p]") and "回した 0" in dm.subject(none_run, "2026-11-04", "[p]")
    assert "⚠ 止まった: 段 0: 止める手" in dm.subject({"stopped": "段 0: 止める手 DEVLOOP_HALT がある"}, "d", "[p]")
    assert dm.subject({"run_error": "X"}, "d", "[p]").endswith("⚠ 段 3 が落ちた")
    assert dm.is_problem({"select_error": "x"}) and not dm.is_problem(ok)


def test_body_lists_morning_todo_and_the_journal():
    parts = {"build": {"mode": "manual", "todo": ["i1", "i2"]}, "select": {"new": ["n1"]}, "cost": 1.0,
             "state": {"cost_usd_total": 9.0}, "commit": "コミット abc・push ✅", "stages": "0,1,2,3,4", "seconds": 60}
    b = dm.body(parts, "2026-11-04", "# 日誌\n本文", "docs/x.md", 20000)
    assert "段 2 を手で作る" in b and "`i1`, `i2`" in b and "`n1`" in b and "累計 $9.00" in b and "コミット abc" in b and b.endswith("本文\n")
    stopped = dm.body({"stopped": "段 0: 打ち切り ②: …"}, "d", "", "—", 100)
    assert "state --reset" in stopped
    assert "続きは日誌" in dm.body({}, "d", "x" * 500, "—", 100)
    assert "- なし" in dm.body({}, "d", "j", "—", 100)


# ---------------------------------------------------------------- 送る


def test_deliver_refuses_without_section_or_secrets(tmp_path):
    assert dm.deliver(dm.mail_cfg({}), "s", "t", sender=Outbox()).startswith("not-sent")          # 節が無い ＝ 送らない
    m = dm.mail_cfg({"mail": {"secrets": str(tmp_path / "none.env")}})
    assert "設定ファイルが無い" in dm.deliver(m, "s", "t", sender=Outbox())
    m = dm.mail_cfg({"mail": {"secrets": _secrets(tmp_path, AIL_MAIL_TO=None)}})
    assert "AIL_MAIL_TO" in dm.deliver(m, "s", "t", sender=Outbox())


def test_deliver_sends_retries_once_and_never_raises(tmp_path):
    m = dm.mail_cfg({"mail": {"secrets": _secrets(tmp_path)}})
    box = Outbox()
    assert dm.deliver(m, "s", "t", sender=box) == "sent" and box.sent == [("s", "t")]
    assert dm.deliver(m, "s", "t", sender=Outbox(fail=1)).endswith("（2 回目）")
    assert dm.deliver(m, "s", "t", sender=Outbox(fail=2)).startswith("error: OSError")
    assert "本文に入っている" in dm.deliver(m, "s", f"x {FAKE_PW}", sender=Outbox())   # 秘密の値が紛れたら送らない


def test_world_readable_secrets_still_send_with_a_warning(tmp_path):
    p = _secrets(tmp_path)
    os.chmod(p, 0o644)
    assert "chmod 600" in dm.deliver(dm.mail_cfg({"mail": {"secrets": p}}), "s", "t", sender=Outbox())


# ---------------------------------------------------------------- night に配線


def test_night_mails_once_after_the_journal_and_records_it_in_state(world, tmp_path):
    root, cfg, conn = _mail_world(world, tmp_path)
    fake = FakeEnv(root, conn)
    box = Outbox()
    fake.env["mail"] = box
    parts, logs = _night(world, fake, stages=(0, 4))
    assert len(box.sent) == 1 and parts["mail"] == "sent"
    subj, text = box.sent[0]
    journal = (root / "log" / "2026-10-26.md").read_text(encoding="utf-8")
    assert subj.startswith("[t] 10/26（月）✅") and journal in text and FAKE_PW not in text
    html = box.html[0]
    assert html and html.startswith("<!doctype html>") and "✅ 完走" in html and FAKE_PW not in html
    assert "sent" not in journal                                   # ⚠ 日誌は書き換えない
    assert devloop.load_state(conn)["nights"][-1]["mail"] == "sent"


def test_stopped_and_locked_nights_also_mail(world, tmp_path):
    root, cfg, conn = _mail_world(world, tmp_path)
    (root / "DEVLOOP_HALT").write_text("", encoding="utf-8")
    fake = FakeEnv(root, conn)
    fake.env["mail"] = box = Outbox()
    _night(world, fake)
    assert "⚠ 止まった" in box.sent[0][0] and "DEVLOOP_HALT" in box.sent[0][1]
    os.remove(root / "DEVLOOP_HALT")
    assert devloop.take_lock(str(root / "devloop.lock"))
    _night(world, fake)
    devloop.release_lock(str(root / "devloop.lock"))
    assert len(box.sent) == 2 and "錠" in box.sent[1][0]


def test_dry_run_problems_mode_and_env_without_sender_do_not_mail(world, tmp_path):
    root, cfg, conn = _mail_world(world, tmp_path)
    fake = FakeEnv(root, conn)
    fake.env["mail"] = box = Outbox()
    parts, _ = _night(world, fake, dry_run=True)
    assert box.sent == [] and "mail" not in parts
    cfg["mail"]["send"] = "problems"
    parts, _ = _night(world, fake, stages=(0, 4))
    assert box.sent == [] and "problems" in parts["mail"]
    cfg["mail"]["send"] = "always"
    parts, _ = _night(world, FakeEnv(root, conn), stages=(0, 4))   # ⚠ 送り手の無い env（既存のテスト）は送らない
    assert parts["mail"].startswith("not-sent（env に送り手が無い")


def test_a_failing_sender_does_not_change_the_night(world, tmp_path):
    root, cfg, conn = _mail_world(world, tmp_path)
    fake = FakeEnv(root, conn)
    fake.env["mail"] = Outbox(fail=5)
    parts, _ = _night(world, fake, stages=(0, 4))
    assert parts["mail"].startswith("error:") and not parts.get("stopped") and parts["commit"].startswith("コミット")


# ---------------------------------------------------------------- run-devloop.sh（Python ごと落ちた晩）


def _runner(tmp_path, py_body: str) -> tuple[int, list[str]]:
    fake_py = tmp_path / "fakepy"
    fake_py.write_text("#!/bin/bash\n" + py_body + "\n", encoding="utf-8")
    fake_py.chmod(0o755)
    outbox = tmp_path / "outbox"
    env = {**os.environ, "AIL_DEVLOOP_PY": str(fake_py), "AIL_DEVLOOP_LOGDIR": str(tmp_path / "logs"),
           "AIL_DEVLOOP_MAIL_OUTBOX": str(outbox), "AIL_DEVLOOP_MAIL_SECRETS": _secrets(tmp_path)}
    p = subprocess.run(["bash", os.path.join(REPO, "run-devloop.sh")], env=env, capture_output=True, text=True, timeout=60)
    return p.returncode, sorted(os.listdir(outbox)) if outbox.exists() else []


@pytest.mark.parametrize("body,rc,mails", [
    ("echo crash >&2; exit 1", 1, 1),                                  # 報告まで届かずに落ちた → 送り手が 1 通
    ('echo sent > "$AIL_DEVLOOP_MAIL_MARK"; exit 1', 1, 0),            # night が送ろうとした後に落ちた → 2 通目は送らない
    ("exit 0", 0, 0), ("exit 2", 2, 0),                                # 通った ／ 門で止まった（night が自分で送る）
])
def test_runner_fallback_mail(tmp_path, body, rc, mails):
    got_rc, files = _runner(tmp_path, body)
    assert got_rc == rc and len(files) == mails
    if mails:
        text = (tmp_path / "outbox" / files[0]).read_text(encoding="utf-8")
        assert "運転手が落ちた（rc=1）" in text and "crash" in text and FAKE_PW not in text
    assert not list((tmp_path / "logs").glob("*.mark"))               # 印は残さない


# ---------------------------------------------------------------- HTML の本文（2026-11-04 利用者「D」＋ 頭の 1 行 ＋ 結果の札）


IDEA = {"id": "20261104-x", "title": "題 <b>強調</b>", "status": "登録", "frame": "外", "kind": "モデル", "effort": "小", "n_trials_planned": 3,
        "touches": "新しいモジュールだけ", "question": "問いの 1 文目。2 文目。", "prediction": "予想の文。", "learn_if_null": "分かること。", "p_hold": 0.3}


def test_clip_cuts_at_sentences_and_never_rewrites():
    assert dm.clip("一。二。三。", 4) == "一。二。"
    assert dm.clip("あ" * 200, 10) == "あ" * 10 + "…"
    assert dm.clip("") == "" and dm.clip("  改\n行 ") == "改 行"


def test_html_body_escapes_and_orders_cards_by_p_hold():
    low = {**IDEA, "id": "20261104-y", "title": "低い", "p_hold": 0.05, "status": "人の手", "touches": "共有の経路: x"}
    parts = {"stages": "0,1,2,3,4", "gate": "", "ideate": {"ideas": [low, IDEA], "counts": {"登録": 1, "人の手": 1}},
             "build": {"mode": "manual", "todo": ["20261104-x"]}, "cost": 2.15, "state": {"cost_usd_total": 6.13}, "commit": "コミット abc1234・push ✅"}
    h = dm.html_body(parts, "2026-11-04", "docs/log/2026-11-04.md", repo_url="https://example.invalid/r")
    assert "<b>強調</b>" not in h and "&lt;b&gt;強調&lt;/b&gt;" in h                      # 原文はエスケープする
    assert h.index("題 &lt;b&gt;") < h.index("低い")                                       # 予想の高い順
    assert "11/4（水）" in h and "作る 人の手 1" in h and "共有の経路を触る" in h and "枠の外" in h
    assert 'href="https://example.invalid/r/commit/abc1234"' in h and 'href="https://example.invalid/r/blob/main/docs/log/2026-11-04.md"' in h
    assert "<script" not in h and "<style" not in h                                       # メール向け ＝ 属性の style だけ


def test_idea_card_lists_similar_past_ideas_only_when_given():
    sim = [{"id": "20261106-planted-signal-power", "title": "既知の強さの <i>信号</i> を埋める", "status": "回した", "score": 0.218},
           {"id": "20261101-x", "title": "別の案", "status": "人の手", "score": 0.04}]
    h = dm.idea_card(IDEA, sim)
    assert "似た過去の案" in h and "0.22" in h and "0.04" in h and "（回した ・ 20261106）" in h and "（人の手 ・ 20261101）" in h
    assert "<i>信号</i>" not in h and "&lt;i&gt;信号&lt;/i&gt;" in h                         # 原文はエスケープする
    assert h.index("既知の強さ") < h.index("別の案")                                         # 渡した順（近い順）のまま
    assert "似た過去の案" not in dm.idea_card(IDEA) and "似た過去の案" not in dm.idea_card(IDEA, [])
    parts = {"stages": "0,1,2,3,4", "gate": "", "ideate": {"ideas": [IDEA], "counts": {"登録": 1}, "similar": {IDEA["id"]: sim}},
             "cost": 2.0, "state": {}, "commit": "コミット abc1234・push ✅"}
    assert "似た過去の案" in dm.html_body(parts, "2026-11-04", "docs/log/2026-11-04.md")
    parts["ideate"]["similar"] = {}
    assert "似た過去の案" not in dm.html_body(parts, "2026-11-04", "docs/log/2026-11-04.md")


def test_html_body_stopped_night_is_red_and_shows_the_fix():
    h = dm.html_body({"stopped": "段 0: 作業ツリーが汚れている（未コミット: TODO.md）", "gate": "作業ツリーが汚れている", "stages": "0,1,2,3,4"},
                     "2026-11-05", "—")
    assert "⚠ 止まった（段 0 の門）" in h and dm.C["bad"] in h and "門 止まった" in h and "コミットする" in h
    assert "この晩はアイデアを出していません" in h


def test_stopped_subject_is_short_and_keeps_the_count():
    # 2026-11-05 利用者「3つとも直して」: 件名にファイルの並びを入れない（本文に回す）
    why = "段 0: 作業ツリーが汚れている（利用者の未コミット 19 件: TODO.md, experiments/feature-discovery/config/ideas/a.toml）"
    subj = dm.subject({"stopped": why}, "2026-11-05", "[ail devloop]")
    assert subj == "[ail devloop] 11/5（木）⚠ 止まった: 段 0: 作業ツリーが汚れている（未コミット 19 件）"
    assert "TODO.md" not in subj
    assert dm.stop_short("段 0: " + "長" * 80).endswith("…") and len(dm.stop_short("段 0: " + "長" * 80)) == 40


def test_journal_link_only_when_pushed_and_label_only_with_stage1():
    base = {"stages": "0,1,2,3,4", "gate": "", "cost": 0.0, "state": {}}
    url = "https://example.invalid/r"
    stopped = dm.html_body({**base, "stopped": "段 0: 作業ツリーが汚れている（未コミット 1 件: x）", "gate": "汚れている"},
                           "2026-11-05", "docs/log/2026-11-05.md", repo_url=url)
    assert "/blob/main/" not in stopped and "GitHub には未反映" in stopped and "原文つき" not in stopped
    push_failed = dm.html_body({**base, "commit": "コミット abc1234・⚠ push 失敗: x"}, "2026-11-05", "docs/log/d.md", repo_url=url)
    assert "/blob/main/" not in push_failed
    no_stage1 = dm.html_body({**base, "commit": "コミット abc1234・push ✅"}, "2026-11-05", "docs/log/d.md", repo_url=url)
    assert 'href="https://example.invalid/r/blob/main/docs/log/d.md"' in no_stage1 and "原文つき" not in no_stage1
    with_stage1 = dm.html_body({**base, "commit": "コミット abc1234・push ✅", "ideate": {"bundles": {"ref": {"got": 2, "cost": 1.0}}}},
                               "2026-11-05", "docs/log/d.md", repo_url=url)
    assert "日誌を開く（段 1 の原文つき）" in with_stage1


def test_idea_results_counts_verdicts_and_judges_the_prediction(world):
    import json
    root, cfg, conn = world
    for name, v, edge in (("own.z.ridge.shared@50", "落とす", -100.0), ("own.z.ridge.shared@60", "保留", 5.0)):
        conn.execute("INSERT INTO ledger_rows (leak, trial_name, model_name, is_trial, verdict, closed, first_run, last_run, runs, doc) "
                     "VALUES (0, ?, ?, 1, ?, 0, 'd', 'd', '[]', ?)",
                     (name, name.split("@")[0], v, json.dumps({"実行一覧": ["run-z"], "閾値": name[-2:], "上乗せbp": edge, "上乗せfold": "1/5"})))
    docs = [{"id": "20261101-z", "title": "Z", "p_hold": 0.2, "runs": ["run-z"]}, {"id": "20261101-n", "title": "N", "runs": ["none"]}]
    r = devloop.idea_results(conn, docs)
    assert r[0]["counts"] == {"落とす": 1, "保留": 1} and r[0]["held"] and r[0]["hit"] is False      # 0.2 と言ったが保留が出た ＝ 外れ
    assert r[1]["n"] == 0 and r[1]["hit"] is None
    card = dm.result_card(r[0])
    assert "外れ" in card and "θ50 −100" in card and "own.z.ridge.shared" in card and "保留 1 ・ 落とす 1" in card


def test_html_failure_falls_back_to_text_only(world, tmp_path, monkeypatch):
    root, cfg, conn = _mail_world(world, tmp_path)
    fake = FakeEnv(root, conn)
    fake.env["mail"] = box = Outbox()
    # ⚠ monkeypatch.undo() で戻さない（world の差し替え〔load_config ・ ROOT〕まで外れ、2 回目の晩が本物の register.md を書き直す）
    with monkeypatch.context() as m:
        m.setattr(dm, "html_body", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("壊れた")))
        parts, logs = _night(world, fake, stages=(0, 4))
    assert parts["mail"] == "sent" and box.html == [None]
    cfg["mail"]["html"] = False
    _night(world, fake, stages=(0, 4))
    assert box.html[-1] is None


def test_outbox_writes_the_html_beside_the_text(tmp_path):
    send = dm._outbox_send(str(tmp_path / "o"))
    send({"AIL_MAIL_TO": "x"}, "s", "t", html="<p>h</p>")
    send({"AIL_MAIL_TO": "x"}, "s2", "t2")
    assert sorted(os.listdir(tmp_path / "o")) == ["001.html", "001.txt", "002.txt"]
