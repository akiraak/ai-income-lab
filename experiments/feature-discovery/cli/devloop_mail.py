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
import html as _html
import os
import re
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
    m.setdefault("html", True)
    m.setdefault("repo_url", "")
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


WEEKDAYS = "月火水木金土日"


def date_label(date: str) -> str:
    """2026-11-04 → 11/4（水）。読めない日付はそのまま。"""
    try:
        d = dt.date.fromisoformat(str(date))
    except ValueError:
        return str(date)
    return f"{d.month}/{d.day}（{WEEKDAYS[d.weekday()]}）"


def _idea_counts(parts: dict) -> str:
    c = (parts.get("ideate") or {}).get("counts") or {}
    n = sum(c.values())
    inner = "・".join(f"{k} {v}" for k, v in c.items() if v)
    return f"アイデア {n}" + (f"（{inner}）" if inner else "")


def subject(parts: dict, date: str, prefix: str) -> str:
    """件名（2026-11-04 利用者「このままで組み込みに進めて」＝ HTML の見本の形）。⚠ `n_trials 1120→None` は出さない。"""
    day = date_label(date)
    if parts.get("stopped"):
        return f"{prefix} {day}⚠ 止まった: {str(parts['stopped'])[:70]}"
    err = _first_error(parts)
    if err:
        return f"{prefix} {day}⚠ 段 {err[0]} が落ちた"
    ran = len((parts.get("run") or {}).get("picked") or [])
    return f"{prefix} {day}✅ {_idea_counts(parts)}・回した {ran}・${float(parts.get('cost', 0.0)):.2f}"


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


# ---------------------------------------------------------------- HTML の本文（2026-11-04 利用者「D」＋ 頭の 1 行 ＋ 結果の札）
# ⚠ メールなので CSS は全部 style 属性に書く・横並びは table・外の字体や画像は読まない・JavaScript なし。
# ⚠ 札の文は登録簿の原文を切り出すだけ（言い換えない）。テキストの本文（body）は今までどおり一緒に送る。

FONT = "'Hiragino Sans','Noto Sans JP','Yu Gothic UI',Meiryo,sans-serif"
MONO = "Menlo,Consolas,monospace"
C = {"ok": "#1f7a4d", "warn": "#dc6803", "bad": "#b42318", "off": "#98a2b3", "ink": "#1d2633", "sub": "#475467", "dim": "#667085",
     "line": "#dfe3e8", "accent": "#3a4fb8"}
STATUS_STYLE = {"登録": ("#e3f4ea", "#1f7a4d"), "人の手": ("#fef0c7", "#93370d"), "既出": ("#eef0f3", "#475467"),
                "案": ("#eef0f3", "#475467"), "作成中": ("#e0eaff", "#2d4ab0"), "回した": ("#e0eaff", "#2d4ab0")}


def _e(s) -> str:
    return _html.escape(str(s), quote=True)


def _inline(s) -> str:
    """エスケープしてから `x` を <code> に。"""
    return re.sub(r"`([^`]+)`", lambda m: f'<code style="font-family:{MONO};font-size:12px">{m.group(1)}</code>', _e(s))


def clip(s, n: int = 120) -> str:
    """原文の先頭の文から n 字まで（文の切れ目「。」で止める・超えたら「…」）。言い換えない。"""
    s = " ".join(str(s or "").split())
    if not s:
        return ""
    out = ""
    for sent in re.findall(r"[^。]*。|[^。]+$", s):
        if out and len(out) + len(sent) > n:
            break
        out += sent
    return out if len(out) <= n else out[:n].rstrip() + "…"


def _pill(text: str, bg: str, fg: str, bold: bool = False) -> str:
    return (f'<span style="background:{bg};color:{fg};padding:1px 8px;border-radius:999px;'
            f'{"font-weight:700;" if bold else ""}white-space:nowrap">{_e(text)}</span>')


def stage_strip(parts: dict) -> list[tuple[str, str, str]]:
    """段 0〜4 の (色, 印, 文)。"""
    stages = {int(x) for x in str(parts.get("stages") or "").split(",") if x.strip().isdigit()}
    out = []
    if parts.get("stopped") and not parts.get("gate"):          # 錠が取れない晩など、門の前で止まった
        out.append((C["bad"], "✕", "門 止まった"))
    elif parts.get("gate"):
        out.append((C["bad"], "✕", "門 止まった"))
    elif 0 in stages:
        out.append((C["ok"], "●", "門 通った"))
    else:
        out.append((C["off"], "●", "門 —"))
    s1 = parts.get("ideate")
    if parts.get("ideate_error"):
        out.append((C["bad"], "✕", "出す 落ちた"))
    elif s1:
        out.append((C["ok"], "●", f"出す {len(s1.get('ideas') or [])} 本"))
    else:
        out.append((C["off"], "●", "出す —"))
    s2 = parts.get("build")
    if parts.get("build_error"):
        out.append((C["bad"], "✕", "作る 落ちた"))
    elif s2 and s2.get("mode") != "claude" and s2.get("todo"):
        out.append((C["warn"], "●", f"作る 人の手 {len(s2['todo'])}"))
    elif s2 and (s2.get("built") or s2.get("failed")):
        f = len(s2.get("failed") or [])
        out.append((C["warn"] if f else C["ok"], "●", f"作る {len(s2.get('built') or [])} 本" + (f"・失敗 {f}" if f else "")))
    elif s2:
        out.append((C["ok"], "●", "作る なし"))
    else:
        out.append((C["off"], "●", "作る —"))
    s3 = parts.get("run")
    if parts.get("run_error"):
        out.append((C["bad"], "✕", "回す 落ちた"))
    elif s3 and s3.get("picked"):
        f = len(s3.get("failed") or [])
        out.append((C["warn"] if f else C["ok"], "●", f"回す {len(s3['picked'])} 本" + (f"・失敗 {f}" if f else "")))
    elif s3 and s3.get("experiments"):
        out.append((C["ok"], "●", f"回す 机上 {len(s3['experiments'])}"))
    elif s3:
        out.append((C["off"], "●", "回す なし"))
    else:
        out.append((C["off"], "●", "回す —"))
    s4 = parts.get("select")
    if parts.get("select_error"):
        out.append((C["bad"], "✕", "選ぶ 落ちた"))
    elif s4:
        out.append((C["ok"], "●", f"選ぶ 新顔 {len(s4['new'])}" if s4.get("new") else "選ぶ 新顔なし"))
    else:
        out.append((C["off"], "●", "選ぶ —"))
    return out


def _bar(p) -> str:
    if not isinstance(p, (int, float)) or isinstance(p, bool):
        return ""
    w = max(0, min(100, round(float(p) * 100)))
    fill = f'<div style="background:{C["accent"]};width:{w}%;height:8px;border-radius:3px"></div>' if w else ""
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-top:8px;font-size:12px;color:{C["sub"]}">'
            f'<tr><td style="padding-right:8px;white-space:nowrap">保留以上の予想</td><td style="width:160px">'
            f'<div style="background:#eef0f3;border-radius:3px;height:8px">{fill}</div></td>'
            f'<td style="padding-left:8px">{float(p):.2f}</td></tr></table>')


def _card(inner: str, border: str = "") -> str:
    return (f'<div style="background:#ffffff;border:1px solid {C["line"]};{border}border-radius:8px;'
            f'padding:14px 18px;margin-bottom:10px">{inner}</div>')


def idea_card(d: dict) -> str:
    bg, fg = STATUS_STYLE.get(str(d.get("status")), ("#eef0f3", C["sub"]))
    frame = d.get("frame")
    tags = [_pill(str(d.get("status") or "?"), bg, fg, bold=True)]
    if frame == "外":
        tags.append(_pill("枠の外", "#f3e8ff", "#6b3fa0"))
    elif frame == "内":
        tags.append(_pill("枠の内", "#e0eaff", "#2d4ab0"))
    meta = [x for x in (d.get("kind"), f"手間 {d['effort']}" if d.get("effort") else "",
                        f"数える {d.get('n_trials_planned', 0)}") if x]
    if str(d.get("touches") or "").startswith("共有の経路"):
        meta.append("共有の経路を触る")
    rows = []
    if d.get("prediction"):
        rows.append(f'<b style="color:#344054">予想</b>　{_inline(clip(d["prediction"]))}')
    if d.get("learn_if_null"):
        rows.append(f'<b style="color:#344054">差が出なかったら</b>　{_inline(clip(d["learn_if_null"]))}')
    foot = (f'<div style="margin-top:8px;font-size:12px;color:{C["sub"]};border-top:1px dashed #e4e7ec;padding-top:6px">'
            + "<br>".join(rows) + "</div>") if rows else ""
    return _card(
        f'<div style="font-size:12px">{" ".join(tags)} <span style="color:{C["dim"]}">{_e(" ・ ".join(meta))}</span></div>'
        f'<div style="font-size:15px;font-weight:700;margin-top:6px">{_inline(d.get("title") or d.get("id"))}</div>'
        + (f'<div style="font-size:13px;color:{C["sub"]};margin-top:4px">{_inline(clip(d.get("question"), 160))}</div>' if d.get("question") else "")
        + _bar(d.get("p_hold")) + foot)


def _bp(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"{v:+,.0f}".replace("+", "＋").replace("-", "−")


def result_card(r: dict) -> str:
    """回した案 1 本。r ＝ devloop.idea_results の 1 要素（id・title・p_hold・prediction・counts・rows・hit）。"""
    hit = r.get("hit")
    color = C["off"] if hit is None else (C["ok"] if hit else C["warn"])
    counts = r.get("counts") or {}
    verdict = " ・ ".join(f"{k} {counts[k]}" for k in ("採る", "保留", "落とす") if counts.get(k)) or "検証結果一覧に行が無い"
    groups: dict[str, list] = {}                 # 予測モデル名（@θ の前）ごと ＝ 同じ実行で回し直した素の行と分けて見せる
    for n, t, e, f, _v in r.get("rows") or []:
        groups.setdefault(n.split("@")[0], []).append((t, e, f))
    lines = []
    for base, rs in list(groups.items())[:4]:
        vals = " ・ ".join(f"θ{_e(t)} {_bp(e)}（{_e(f)}）" for t, e, f in rs[:3]) + (f" ・ ほか {len(rs) - 3}" if len(rs) > 3 else "")
        lines.append(f'<code style="font-family:{MONO};font-size:11px;color:{C["dim"]}">{_e(base)}</code><br>{vals}')
    if len(groups) > 4:
        lines.append(f"ほか {len(groups) - 4} 形")
    actual = ("上乗せ bp/fold（fold の符号）<br>" + "<br>".join(lines)) if lines else ""
    p = r.get("p_hold")
    if hit is None:
        judge = "確率つきの予想が無い"
    else:
        held = "保留以上あり" if r.get("held") else "保留以上なし"
        judge = f'<span style="color:{color};font-weight:700">{"当たり" if hit else "外れ"}</span>（予想 {float(p):.2f} → {held}）'
    def row(k, v):
        return (f'<tr><td style="width:72px;color:{C["dim"]};padding:3px 0;vertical-align:top">{k}</td>'
                f'<td style="padding:3px 0">{v}</td></tr>')
    body = [row("判定", f"<b>{_e(verdict)}</b>")]
    if r.get("prediction"):
        body.append(row("予想", _inline(clip(r["prediction"]))))
    if actual:
        body.append(row("実際", actual))
    body.append(row("当たり方", judge))
    origin = f"{date_label(r['id'][:4] + '-' + r['id'][4:6] + '-' + r['id'][6:8])} に出したアイデア" if re.match(r"^\d{8}-", str(r.get("id"))) else str(r.get("id"))
    return _card(
        f'<div style="font-size:12px;color:{C["dim"]}">{_e(origin)} ・ 数えた {r.get("n", 0)}</div>'
        f'<div style="font-size:15px;font-weight:700;margin-top:4px">{_inline(r.get("title") or r.get("id"))}</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-top:8px;font-size:13px">'
        + "".join(body) + "</table>", border=f"border-left:4px solid {color};")


def _p_key(d: dict):
    p = d.get("p_hold")
    return -(float(p)) if isinstance(p, (int, float)) and not isinstance(p, bool) else 1.0


def html_body(parts: dict, date: str, journal_rel: str, *, repo_url: str = "", results: list[dict] | None = None,
              calib: dict | None = None) -> str:
    st = parts.get("state") or {}
    day = date_label(date)
    cost = float(parts.get("cost", 0.0))
    err = _first_error(parts)
    if parts.get("stopped"):
        band, head = C["bad"], "⚠ 止まった" + ("（段 0 の門）" if str(parts["stopped"]).startswith("段 0") else "")
    elif err:
        band, head = C["bad"], f"⚠ 段 {err[0]} が落ちた"
    else:
        band, head = C["ok"], "✅ 完走"
    sub = f"{day} ・ titan ・ {float(parts.get('seconds', 0.0)):.0f} 秒 ・ ${cost:.2f}（累計 ${float(st.get('cost_usd_total', 0.0)):.2f}）"
    strip = " → ".join(f'<span style="white-space:nowrap;{"" if col != C["off"] else "color:" + C["off"] + ";"}">'
                       f'<b style="color:{col}">{mk}</b> {_e(t)}</span>' for col, mk, t in stage_strip(parts))
    todo = [x[2:] if x.startswith("- ") else x for x in todo_lines(parts, date)]
    problem = bool(parts.get("stopped") or err)
    box_bg, box_line = ("#fef3f2", "#fda29b") if problem else ("#fff8e1", "#f0d58a")
    todo_html = ("なし" if todo == ["なし"] else "<br>".join(_inline(x) for x in todo))
    out = [f'<div style="max-width:600px;margin:0 auto;font-family:{FONT};color:{C["ink"]};line-height:1.6">',
           f'<div style="background:#ffffff;border:1px solid {C["line"]};border-radius:8px;overflow:hidden">',
           f'<div style="background:{band};color:#ffffff;padding:10px 18px"><span style="font-size:16px;font-weight:700">{_e(head)}</span>'
           f'<span style="font-size:12px">　{_e(sub)}</span></div>',
           f'<div style="padding:10px 18px 4px;font-size:12px;color:#344054;line-height:2">{strip}</div>',
           f'<div style="margin:6px 18px 14px;padding:8px 12px;background:{box_bg};border:1px solid {box_line};border-radius:6px;font-size:13px">'
           f'<b>朝にやること</b><br>{todo_html}</div></div>']
    ideas = sorted((parts.get("ideate") or {}).get("ideas") or [], key=_p_key)
    label = f'<div style="font-size:12px;color:{C["dim"]};margin:18px 4px 6px;letter-spacing:.06em">'
    if ideas:
        out.append(label + f"この晩のアイデア {len(ideas)} 本（保留以上の予想が高い順）</div>")
        out += [idea_card(d) for d in ideas]
    elif parts.get("stopped") or parts.get("ideate_error"):
        out.append(f'<div style="font-size:12px;color:{C["dim"]};margin:14px 4px 0">この晩はアイデアを出していません</div>')
    if results:
        tot: dict[str, int] = {}
        for r in results:
            for k, v in (r.get("counts") or {}).items():
                tot[k] = tot.get(k, 0) + v
        run = parts.get("run") or {}
        nt = f" ・ n_trials {run['before']:,} → {run['after']:,}" if isinstance(run.get("before"), int) and isinstance(run.get("after"), int) else ""
        out.append(label + f"回した結果 {len(results)} 本 ・ " + " ／ ".join(f"{k} {tot.get(k, 0)}" for k in ("採る", "保留", "落とす")) + f"{_e(nt)}</div>")
        out += [result_card(r) for r in results]
    if calib and calib.get("n"):
        out.append(f'<div style="font-size:12px;color:{C["dim"]};margin:4px 4px 0">予想の当たり方（累計）: {calib["n"]} 本 ・ '
                   f'当たり {calib["acc"]:.0%} ・ Brier {calib["brier"]:.3f}</div>')
    foot = []
    top = (parts.get("select") or {}).get("top") or []
    if top:
        items = []
        for t in top[:3]:
            name, method, _fold, edge, theta = (list(t) + [None] * 5)[:5]
            items.append(f"{_e(str(method or name).split(' ')[0])} θ{_e(theta)} {_bp(edge)}bp/fold")
        foot.append("選ぶ段の上位 3: " + " ・ ".join(items) + "（並べたものであって採用ではない）")
    bundles = (parts.get("ideate") or {}).get("bundles") or {}
    if bundles:
        name = {"ref": "参考あり", "free": "参考なし"}
        foot.append("段 1: " + " ・ ".join(f"{name.get(w, w)} {b.get('got', 0)} 本 ${float(b.get('cost', 0)):.2f}" for w, b in bundles.items()))
    commit = str(parts.get("commit") or "未コミット")
    m = re.search(r"コミット ([0-9a-f]{7,40})", commit)
    if m and repo_url:
        commit = _e(commit).replace(m.group(1), f'<a href="{_e(repo_url)}/commit/{m.group(1)}" style="color:{C["accent"]};font-family:{MONO}">{m.group(1)}</a>', 1)
    else:
        commit = _e(commit)
    jl = (f'<a href="{_e(repo_url)}/blob/main/{_e(journal_rel)}" style="color:{C["accent"]}">日誌を開く（段 1 の原文つき）</a>'
          if repo_url and journal_rel and journal_rel != "—" else f"日誌: {_e(journal_rel)}")
    foot.append(f"{commit} ・ {jl}")
    out.append(f'<div style="margin-top:18px;background:#ffffff;border:1px solid {C["line"]};border-radius:8px;padding:12px 18px;'
               f'font-size:12px;color:{C["sub"]};line-height:1.8">' + "<br>".join(foot) + "</div>")
    out.append(f'<div style="color:{C["off"]};font-size:11px;padding:10px 4px">機械が書いたメールです（cli.devloop night）。'
               "止めるには experiments/feature-discovery/DEVLOOP_HALT を置く。</div></div>")
    return ('<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'</head><body style="margin:0;background:#f4f5f7;padding:16px 8px">{"".join(out)}</body></html>')


# ---------------------------------------------------------------- 送る


def _scrub_check(secrets: dict, subj: str, text: str, html: str | None = None) -> str | None:
    """本文に秘密の値が紛れていたら送らない理由（None ＝ 送ってよい）。"""
    for k in ("AIL_MAIL_PASSWORD",):
        v = secrets.get(k) or ""
        if v and (v in text or v in subj or v in (html or "")):
            return f"{k} の値が本文に入っている"
    return None


def smtp_send(secrets: dict, subj: str, text: str, timeout: float = 30, html: str | None = None) -> None:
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subj, secrets["AIL_MAIL_USER"], secrets["AIL_MAIL_TO"]
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")     # multipart/alternative ＝ 文字だけのメールソフトはテキストを出す
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
    def send(secrets: dict, subj: str, text: str, timeout: float = 30, html: str | None = None) -> None:
        os.makedirs(outbox, exist_ok=True)
        n = len([x for x in os.listdir(outbox) if x.endswith(".txt")]) + 1
        with open(os.path.join(outbox, f"{n:03d}.txt"), "w", encoding="utf-8") as f:
            f.write(f"To: {secrets['AIL_MAIL_TO']}\nSubject: {subj}\n\n{text}")
        if html:
            with open(os.path.join(outbox, f"{n:03d}.html"), "w", encoding="utf-8") as f:
                f.write(html)
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


def deliver(m: dict, subj: str, text: str, *, sender=None, html: str | None = None) -> str:
    """送る。戻り値は 1 行の文（sent ／ not-sent（理由）／ error: …）。⚠ 例外を外へ出さない。"""
    if not m.get("enabled"):
        return "not-sent（[mail] enabled = false）"
    secrets, note = load_secrets(secrets_path(m))
    if secrets is None:
        return f"not-sent（{note}: {secrets_path(m)}）"
    why = _scrub_check(secrets, subj, text, html)
    if why:
        return f"not-sent（{why}）"
    send = sender or default_sender()
    last = ""
    for attempt in (1, 2):                     # 1 回だけ取り直す（SMTP の一時的な失敗）
        try:
            if html:
                send(secrets, subj, text, html=html)
            else:
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
