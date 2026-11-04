"""開発ループの報告メール（プラン docs/plans/devloop-mail.md・2026-11-03 利用者「推す案ですすめて」＝ M1 Gmail の SMTP ／ M2 毎晩 ／ M3 要約 ＋ 日誌の全文）。

⚠ **標準ライブラリだけ**（`ail` を import しない）＝ Python の .venv ごと落ちた晩に `run-devloop.sh` がシステムの python3 で呼ぶ送り手にもなる。
⚠ **秘密（宛先・アプリ パスワード）はリポジトリの外**: titan の `~/.config/ai-income-lab/devloop-mail.env`（chmod 600・置くのは利用者）。
   リポジトリ・日誌・メモリ・プランに書かない。無ければ送らず「not-sent」の 1 行を返すだけ。
⚠ **送れなくてもループは止めない**（呼ぶ側は戻り値の文を timer.log と状態に残すだけ・日誌は書き換えない）。

    python3 cli/devloop_mail.py fallback --date 2026-11-04 --rc 1 --log devloop/logs/2026-11-04.log   # run-devloop.sh が呼ぶ
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import smtplib
import ssl
import sys
import tomllib
from email.message import EmailMessage

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG = os.path.join(ROOT, "config", "devloop.toml")
KEYS = ("AIL_MAIL_HOST", "AIL_MAIL_PORT", "AIL_MAIL_USER", "AIL_MAIL_PASSWORD", "AIL_MAIL_TO")
DEFAULT_SECRETS = "~/.config/ai-income-lab/devloop-mail.env"
STAGE_ERRORS = (("ideate_error", 1), ("build_error", 2), ("run_error", 3), ("select_error", 4), ("weekly_error", 4))


# ---------------------------------------------------------------- 設定・秘密


def mail_cfg(cfg: dict | None) -> dict:
    """`[mail]`。⚠ 節が無い config は送らない（テストの config に節は無い ＝ 本物のメールは飛ばない）。"""
    m = dict((cfg or {}).get("mail") or {})
    m.setdefault("enabled", bool((cfg or {}).get("mail")))
    m.setdefault("send", "always")
    m.setdefault("secrets", DEFAULT_SECRETS)
    m.setdefault("subject_prefix", "[ail devloop]")
    m.setdefault("max_body_chars", 20000)
    return m


def secrets_path(m: dict) -> str:
    return os.path.expanduser(os.environ.get("AIL_DEVLOOP_MAIL_SECRETS") or m.get("secrets") or DEFAULT_SECRETS)


def load_secrets(path: str) -> tuple[dict | None, str]:
    """(秘密, 理由)。秘密が None なら送らない。KEY=VALUE の行だけ読む（source しない）。"""
    if not os.path.exists(path):
        return None, "設定ファイルが無い"
    out: dict = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    missing = [k for k in KEYS if not out.get(k)]
    if missing:
        return None, "設定ファイルに項目が無い: " + ", ".join(missing)
    try:
        int(out["AIL_MAIL_PORT"])
    except ValueError:
        return None, "AIL_MAIL_PORT が数でない"
    note = ""
    try:
        if os.stat(path).st_mode & 0o077:
            note = "⚠ 設定ファイルがほかの人にも読める（chmod 600 にする）"
    except OSError:
        pass
    return out, note


# ---------------------------------------------------------------- 件名と本文


def _first_error(parts: dict) -> tuple[int, str] | None:
    for key, stage in STAGE_ERRORS:
        if parts.get(key):
            return stage, str(parts[key])
    return None


def is_problem(parts: dict) -> bool:
    return bool(parts.get("stopped") or _first_error(parts))


def subject(parts: dict, date: str, prefix: str) -> str:
    if parts.get("stopped"):
        return f"{prefix} {date} ⚠ 止まった: {str(parts['stopped'])[:70]}"
    err = _first_error(parts)
    if err:
        return f"{prefix} {date} ⚠ 段 {err[0]} が落ちた"
    c = (parts.get("ideate") or {}).get("counts") or {}
    run = parts.get("run") or {}
    nt = f"{run.get('before')}→{run.get('after')}" if run else "—"
    new = len((parts.get("select") or {}).get("new") or [])
    return f"{prefix} {date} ✅ 登録 {c.get('登録', 0)}・n_trials {nt}・新顔 {new}・${float(parts.get('cost', 0.0)):.2f}"


def _fix_for_stop(why: str) -> str:
    if "DEVLOOP_HALT" in why or "止める手" in why:
        return "止めたままでよければ何もしない。再開は `experiments/feature-discovery/DEVLOOP_HALT` を消す"
    if "打ち切り" in why:
        return "原因を直してから `cli.devloop state --reset`（利用者）"
    if "汚れている" in why:
        return "titan の作業ツリーの未コミットをコミットする（または退ける）"
    if "pull" in why:
        return "titan で `git status` ／ `git pull --ff-only` を確かめる"
    if "錠" in why:
        return "前の晩の運転手が残っていないか確かめる（`devloop.lock`）"
    return "日誌と timer.log を見る"


def todo_lines(parts: dict, date: str) -> list[str]:
    """朝にやること（無ければ「なし」）。"""
    L: list[str] = []
    if parts.get("stopped"):
        L.append(f"- 止まった理由: {parts['stopped']}")
        L.append(f"- 解き方: {_fix_for_stop(str(parts['stopped']))}")
    err = _first_error(parts)
    for key, stage in STAGE_ERRORS:
        if parts.get(key):
            L.append(f"- 段 {stage} が落ちた: {str(parts[key])[:300]}")
    if err:
        L.append(f"- timer.log を見る: `experiments/feature-discovery/devloop/logs/{date}.log`")
    s1 = parts.get("ideate") or {}
    bad = [f"{w}: {'; '.join(b.get('errors') or [])}" for w, b in (s1.get("bundles") or {}).items() if b.get("errors")]
    if bad:
        L.append("- 段 1 の束に誤り（費用の上限に当たると 1 文字も返らない）: " + " ／ ".join(bad)[:300])
    s2 = parts.get("build") or {}
    if s2.get("mode") and s2["mode"] != "claude" and s2.get("todo"):
        L.append("- 段 2 を手で作る（`devloop/prompts/build.md` の手順）: " + ", ".join(f"`{i}`" for i in s2["todo"]))
    for f in s2.get("failed") or []:
        L.append(f"- 段 2 で通らなかった `{f.get('id')}`: {f.get('reason')}（{f.get('status')}）")
    s4 = parts.get("select") or {}
    if s4.get("new"):
        L.append("- 上位 3 の新顔（次の晩に机上へ。並べただけで採用ではない）: " + ", ".join(f"`{n}`" for n in s4["new"]))
    wk = parts.get("weekly") or {}
    if wk.get("drafts"):
        L.append("- 週 1 回の候補の棚の下書き（写すか・呼び名は利用者）: " + ", ".join(f"`{d}`" for d in wk["drafts"]))
    commit = str(parts.get("commit") or "")
    if "失敗" in commit:
        L.append(f"- コミット ／ push: {commit}")
    return L or ["- なし"]


def body(parts: dict, date: str, journal_text: str, journal_rel: str, max_chars: int) -> str:
    st = parts.get("state") or {}
    head = [f"開発ループ {date} の報告（titan・毎晩 01:00 PST。⚠ 機械が書いたメール）", "",
            "## 朝にやること", "", *todo_lines(parts, date), "",
            "## 数字", "",
            f"- 費用: この晩 ${float(parts.get('cost', 0.0)):.2f} ／ 累計 ${float(st.get('cost_usd_total', 0.0)):.2f}",
            f"- 所要: {float(parts.get('seconds', 0.0)):.0f} 秒 ／ 段 {parts.get('stages')}",
            f"- commit: {parts.get('commit') or '未コミット'}",
            f"- 日誌: `{journal_rel}`", "", "---", ""]
    text = journal_text or "（日誌が無い）"
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n…（{len(text) - max_chars} 文字を切った。続きは日誌）"
    return "\n".join(head) + text + "\n"


# ---------------------------------------------------------------- 送る


def _scrub_check(secrets: dict, subj: str, text: str) -> str | None:
    """本文に秘密の値が紛れていたら送らない理由（None ＝ 送ってよい）。"""
    for k in ("AIL_MAIL_PASSWORD",):
        v = secrets.get(k) or ""
        if v and (v in text or v in subj):
            return f"{k} の値が本文に入っている"
    return None


def smtp_send(secrets: dict, subj: str, text: str, timeout: float = 30) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subj, secrets["AIL_MAIL_USER"], secrets["AIL_MAIL_TO"]
    msg.set_content(text)
    host, port = secrets["AIL_MAIL_HOST"], int(secrets["AIL_MAIL_PORT"])
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx) as s:
            s.login(secrets["AIL_MAIL_USER"], secrets["AIL_MAIL_PASSWORD"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as s:
            s.starttls(context=ctx)
            s.login(secrets["AIL_MAIL_USER"], secrets["AIL_MAIL_PASSWORD"])
            s.send_message(msg)


def _outbox_send(outbox: str):
    """試験用: AIL_DEVLOOP_MAIL_OUTBOX があれば SMTP を使わずファイルに書く。"""
    def send(secrets: dict, subj: str, text: str, timeout: float = 30) -> None:
        os.makedirs(outbox, exist_ok=True)
        n = len(os.listdir(outbox)) + 1
        with open(os.path.join(outbox, f"{n:03d}.txt"), "w", encoding="utf-8") as f:
            f.write(f"To: {secrets['AIL_MAIL_TO']}\nSubject: {subj}\n\n{text}")
    return send


def default_sender():
    outbox = os.environ.get("AIL_DEVLOOP_MAIL_OUTBOX")
    return _outbox_send(outbox) if outbox else smtp_send


def mark(status: str) -> None:
    """run-devloop.sh が渡す印の道（AIL_DEVLOOP_MAIL_MARK）に「送ろうとした」を書く ＝ 送り手を 2 度呼ばない。"""
    p = os.environ.get("AIL_DEVLOOP_MAIL_MARK")
    if p:
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(status + "\n")
        except OSError:
            pass


def deliver(m: dict, subj: str, text: str, *, sender=None) -> str:
    """送る。戻り値は 1 行の文（sent ／ not-sent（理由）／ error: …）。⚠ 例外を外へ出さない。"""
    if not m.get("enabled"):
        return "not-sent（[mail] enabled = false）"
    secrets, note = load_secrets(secrets_path(m))
    if secrets is None:
        return f"not-sent（{note}: {secrets_path(m)}）"
    why = _scrub_check(secrets, subj, text)
    if why:
        return f"not-sent（{why}）"
    send = sender or default_sender()
    last = ""
    for attempt in (1, 2):                     # 1 回だけ取り直す（SMTP の一時的な失敗）
        try:
            send(secrets, subj, text)
            return "sent" + (f"（{note}）" if note else "") + ("" if attempt == 1 else "（2 回目）")
        except Exception as e:             # noqa: BLE001  ⚠ ループを止めない
            last = f"{type(e).__name__}: {str(e)[:200]}"
    return f"error: {last}"


# ---------------------------------------------------------------- Python ごと落ちた晩（run-devloop.sh から）


def load_config() -> dict:
    try:
        with open(CONFIG, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {"mail": {"enabled": True}}     # config が読めなくても、落ちたことは知らせる


def fallback(date: str, rc: int, log_path: str, *, sender=None) -> str:
    m = mail_cfg(load_config())
    tail = ""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            tail = "".join(f.readlines()[-40:])
    except OSError:
        tail = f"（{log_path} が読めない）"
    subj = f"{m['subject_prefix']} {date} ⚠ 運転手が落ちた（rc={rc}）"
    text = "\n".join([f"開発ループ {date} の報告（titan。⚠ cli.devloop night が報告まで届かずに終わった）", "",
                      "## 朝にやること", "",
                      f"- timer.log と `{os.path.relpath(log_path, ROOT) if os.path.isabs(log_path) else log_path}` を見る（rc={rc}）",
                      "- `.venv` が壊れていないか・作業ツリーと `devloop.lock` を確かめる", "",
                      "## ログの最後の 40 行", "", tail])
    return deliver(m, subj, text, sender=sender)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="開発ループの報告メール（Python ごと落ちた晩の送り手）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fb = sub.add_parser("fallback")
    fb.add_argument("--date", default=dt.date.today().isoformat())
    fb.add_argument("--rc", type=int, default=1)
    fb.add_argument("--log", default="")
    a = ap.parse_args(argv)
    status = fallback(a.date, a.rc, a.log)
    print(f"報告メール（落ちた晩）: {status}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
