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
        self.fail = fail

    def __call__(self, secrets, subj, text, timeout=30):
        if self.fail:
            self.fail -= 1
            raise OSError("一時的な失敗")
        self.sent.append((subj, text))


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
    ok = {"ideate": {"counts": {"登録": 3}}, "run": {"before": 1120, "after": 1132}, "select": {"new": ["a"]}, "cost": 5.24}
    assert dm.subject(ok, "2026-11-04", "[p]") == "[p] 2026-11-04 ✅ 登録 3・n_trials 1120→1132・新顔 1・$5.24"
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
    assert subj.startswith("[t] 2026-10-26 ✅") and journal in text and FAKE_PW not in text
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
