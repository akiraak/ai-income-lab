"""開発ループ（rules.md 31 章・プラン docs/plans/daily-dev-loop.md）の登録簿を読む・並べる・登録する。

⚠ **Phase 1 の範囲 ＝ `register` だけ**（段 0〜4 を順に回す運転手 `run-devloop.sh` と段の中身は Phase 3）。
⚠ 登録簿 `config/ideas/<id>.toml` が事前固定の正本（31-1）。`register.md` は生成物（手で書かない）。
⚠ 研究の DB は読み取り専用で読む（既出の疑い ＝ `key_shape` が検証結果一覧の予測モデル名に当たる数）。回さない・数えない。

    .venv/bin/python -m cli.devloop register                     # 登録簿を検査し register.md を吐く
    .venv/bin/python -m cli.devloop register --promote <id> …    # 案 → 登録（registered_at ＝ 回すと決めた時刻を打つ。31-1 の 3）
    .venv/bin/python -m cli.devloop register --set <id>=<状態>    # 既出 ／ 人の手 ／ 失敗 など横道へ（31-1 の 2。戻さない）
    .venv/bin/python -m cli.devloop register --run <id>=<実行名>  # 回した実行を結ぶ（作成中 ／ 回した のとき）
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import glob
import json
import os
import sqlite3
import sys
import tomllib
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG = os.path.join(ROOT, "config", "devloop.toml")
KINDS = ("モデル", "データ", "検証方式", "物差し", "執行", "ループ自身")
STATUSES = ("案", "登録", "作成中", "回した", "既出", "人の手", "失敗")
FORWARD = {"案": {"登録", "既出", "人の手"}, "登録": {"作成中", "人の手", "失敗", "既出"}, "作成中": {"回した", "失敗", "人の手"}}
EFFORTS = ("小", "中", "大")
FRAMES = ("内", "外")      # 問いの枠（2026-11-01 利用者「未知なものへのトライが増えたらいいな」→「推す案で」＝ rules.md 31-6）
CONTRACT_VERSION = 2       # 段 1 の契約の版。2 から frame ／ p_hold ／ learn_if_null が要る（それより前の登録簿は求めない）
SEATTLE = ZoneInfo("America/Los_Angeles")
REQUIRED = ("id", "date", "title", "kind", "origin", "question", "structure", "n_trials_planned", "key_shape",
            "failure_modes", "prediction", "effort", "measurable", "touches", "status")


def load_config() -> dict:
    with open(CONFIG, "rb") as f:
        return tomllib.load(f)


def _path(cfg: dict, key: str) -> str:
    return os.path.normpath(os.path.join(ROOT, cfg["paths"][key]))


def now_seattle() -> str:
    return dt.datetime.now(SEATTLE).strftime("%Y-%m-%d %H:%M %Z")


# ---------------------------------------------------------------- 登録簿を読む・検査する


def validate(doc: dict, path: str) -> list[str]:
    """31-1 の項目がそろっているか。足りなければ理由の一覧（空 ＝ 通る）。"""
    # ⚠ 数える検証が 0 の案（物差し・ループ自身）は識別項目を持たないので key_shape は空でよい
    optional_empty = {"key_shape"} if doc.get("n_trials_planned") == 0 else set()
    bad = [f"項目 {k} が無い" for k in REQUIRED if k not in doc or (doc[k] in ("", [], None) and k not in optional_empty)]
    stem = os.path.splitext(os.path.basename(path))[0]
    if doc.get("id") != stem:
        bad.append(f"id {doc.get('id')!r} がファイル名 {stem!r} と違う")
    if doc.get("kind") not in KINDS:
        bad.append(f"kind {doc.get('kind')!r} は {KINDS} のどれか")
    o = doc.get("origin") or {}
    if not isinstance(o, dict) or o.get("source") not in ("reference", "independent"):
        bad.append("origin.source は reference ／ independent")
    if not isinstance(doc.get("n_trials_planned"), int) or doc.get("n_trials_planned", -1) < 0:
        bad.append("n_trials_planned は 0 以上の整数")
    if doc.get("effort") not in EFFORTS:
        bad.append(f"effort は {EFFORTS} のどれか")
    if int((o if isinstance(o, dict) else {}).get("contract", 1)) >= 2:
        # ⚠ 31-6: 問いの枠・確率つきの予想・差なしでも何が分かるか（契約 2 から）
        if doc.get("frame") not in FRAMES:
            bad.append(f"frame は {FRAMES} のどれか")
        p_hold = doc.get("p_hold")
        if not isinstance(p_hold, (int, float)) or isinstance(p_hold, bool) or not 0.0 <= float(p_hold) <= 1.0:
            bad.append("p_hold は 0〜1 の数（保留以上の行が 1 つでも出る確率）")
        if not str(doc.get("learn_if_null") or "").strip():
            bad.append("learn_if_null（決着なし ／ 差なしのとき何が分かり、次に何を変えるか）が無い")
    if not str(doc.get("measurable", "")).startswith(("測れる", "測れない")):
        bad.append("measurable は「測れる: …」か「測れない: …」")
    if not str(doc.get("touches", "")).startswith(("新しいモジュールだけ", "共有の経路")):
        bad.append("touches は「新しいモジュールだけ」か「共有の経路: …」")
    if doc.get("status") not in STATUSES:
        bad.append(f"status は {STATUSES} のどれか")
    if doc.get("status") in ("登録", "作成中", "回した", "失敗") and not doc.get("registered_at"):
        bad.append("status が 登録 より先なのに registered_at が無い（31-1 の 3）")
    if not isinstance(doc.get("failure_modes"), list) or not doc.get("failure_modes"):
        bad.append("failure_modes は 1 つ以上の一覧")
    return bad


def load_ideas(cfg: dict) -> list[tuple[str, dict, list[str]]]:
    out = []
    for p in sorted(glob.glob(os.path.join(_path(cfg, "ideas"), "*.toml"))):
        with open(p, "rb") as f:
            doc = tomllib.load(f)
        out.append((p, doc, validate(doc, p)))
    return out


# ---------------------------------------------------------------- 既出の疑い（検証結果一覧の予測モデル名に当たる数）


def ledger_models(db: str | None) -> list[str] | None:
    from ail import rundb, runs
    path = db or runs.db_path()
    if not os.path.exists(path):
        return None
    try:
        conn = rundb.connect(path, readonly=True)
        try:
            return [str(r[0]) for r in conn.execute("SELECT DISTINCT model_name FROM ledger_rows WHERE leak = 0 AND is_trial = 1")]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def duplicates(key_shape, models: list[str] | None) -> tuple[int, list[str]]:
    """`key_shape`（予測モデル名のパターン。文字列か一覧）に当たる既存の予測モデル名の数と例。⚠ 印は人 ／ ループが `--set` で付ける。"""
    if models is None:
        return -1, []
    pats = [key_shape] if isinstance(key_shape, str) else [str(x) for x in key_shape]
    hit = sorted({m for m in models for p in pats if fnmatch.fnmatchcase(m, p) or fnmatch.fnmatchcase(m, p + "~*")})
    return len(hit), hit[:3]


# ---------------------------------------------------------------- 書く（登録簿の TOML と register.md）


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(type(v))


def dump_idea(doc: dict) -> str:
    """登録簿の 1 ファイル（機械で書く。並びは 31-1 の項目順）。"""
    order = list(REQUIRED) + ["frame", "p_hold", "p_positive", "learn_if_null", "registered_at", "reference_mode", "notes", "runs"]
    keys = [k for k in order if k in doc] + [k for k in doc if k not in order]
    lines = ["# ⚠ 開発ループの登録簿（rules.md 31-1）。cli.devloop が書く。status が 登録 より先なら structure ／ prediction ／ n_trials_planned ／ key_shape を直さない（直すなら新しい id）"]
    for k in keys:
        lines.append(f"{k} = {_toml_value(doc[k])}")
    return "\n".join(lines) + "\n"


def write_idea(path: str, doc: dict) -> None:
    text = dump_idea(doc)
    tomllib.loads(text)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _esc(s) -> str:
    return str(s).replace("|", "／").replace("\n", " ")


def run_verdicts(db: str | None) -> dict[str, set[str]] | None:
    """実行名 → その実行が出した検証の判定の集まり（研究の DB の ledger_rows。予想の当たり方 ＝ 31-6 の 3 に使う）。"""
    from ail import rundb, runs
    path = db or runs.db_path()
    if not os.path.exists(path):
        return None
    try:
        conn = rundb.connect(path, readonly=True)
        try:
            out: dict[str, set[str]] = {}
            for doc, verdict in conn.execute("SELECT doc, verdict FROM ledger_rows WHERE leak = 0 AND is_trial = 1"):
                for r in (json.loads(doc).get("実行一覧") or []):
                    out.setdefault(str(r), set()).add(str(verdict))
            return out
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def calibration(ideas: list[tuple[str, dict, list[str]]], outcomes: dict[str, set[str]] | None) -> dict:
    """回した案のうち p_hold のあるもの: 当たり（p ≥ 0.5 と「保留以上が出た」が一致）の割合と Brier（31-6 の 3）。"""
    rows = []
    for _p, d, _b in ideas:
        if d.get("status") != "回した" or not isinstance(d.get("p_hold"), (int, float)) or not outcomes:
            continue
        vs = set().union(*(outcomes.get(r, set()) for r in d.get("runs") or [])) if d.get("runs") else set()
        if not vs:
            continue
        hit = bool(vs & {"保留", "採る"})
        rows.append((d["id"], float(d["p_hold"]), hit))
    n = len(rows)
    acc = sum((p >= 0.5) == h for _i, p, h in rows) / n if n else None
    brier = sum((p - h) ** 2 for _i, p, h in rows) / n if n else None
    return {"n": n, "acc": acc, "brier": brier, "rows": rows}


def build_register(cfg: dict, ideas: list[tuple[str, dict, list[str]]], models: list[str] | None, n_trials: int | None,
                   outcomes: dict[str, set[str]] | None = None) -> str:
    counts: dict[str, int] = {}
    for _p, d, _b in ideas:
        counts[d.get("status", "?")] = counts.get(d.get("status", "?"), 0) + 1
    lines = ["# 開発ループの登録簿（生成物）", "",
             "⚠ **このファイルは生成物である。手で書き換えない。** 正本は `experiments/feature-discovery/config/ideas/*.toml`（rules.md 31-1）。",
             f"生成 {now_seattle()} ／ 検証結果一覧の n_trials {n_trials if n_trials is not None else '—'} ／ "
             f"登録簿 {len(ideas)} 本（" + "・".join(f"{k} {v}" for k, v in sorted(counts.items())) + "）／ "
             f"r {cfg['ideation']['reference_pct']}%・N {cfg['ideation']['ideas_per_night']}", "",
             "⚠ 良し悪し・意味の有無の印は無い（利用者の指示 2026-10-24）。印は 手間・測れるか・触る場所 の 3 つ。「既出の疑い」は `key_shape` が検証結果一覧の予測モデル名に当たった数（印は人 ／ ループが付ける）。", "",
             "| id | 題 | 種類 | 枠 | 保留以上の確率 | 出どころ | 状態 | 登録時刻 | 数える | 識別項目の形 | 既出の疑い | 手間 | 測れるか | 触る場所 |",
             "| --- | --- | --- | --- | ---: | --- | --- | --- | ---: | --- | --- | --- | --- | --- |"]
    for _p, d, bad in ideas:
        o = d.get("origin") or {}
        src = "参考あり" if o.get("source") == "reference" else "参考なし"
        if d.get("reference_mode"):
            src += f"（{d['reference_mode']}）"
        n, ex = duplicates(d.get("key_shape", ""), models)
        dup = "DB なし" if n < 0 else ("0" if n == 0 else f"{n} 行（例 " + " ／ ".join(f"`{x}`" for x in ex) + "）")
        ks = d.get("key_shape", "")
        ks = " ／ ".join(ks) if isinstance(ks, list) else str(ks)
        status = d.get("status", "?") + (" ⚠ " + "・".join(bad) if bad else "")
        ph = f"{float(d['p_hold']):.2f}" if isinstance(d.get("p_hold"), (int, float)) else "—"
        lines.append(f"| `{d.get('id')}` | {_esc(d.get('title'))} | {_esc(d.get('kind'))} | {_esc(d.get('frame') or '—')} | {ph} | {src} | {_esc(status)} | {_esc(d.get('registered_at') or '—')} | "
                     f"{d.get('n_trials_planned')} | `{_esc(ks)}` | {dup} | {_esc(d.get('effort'))} | {_esc(d.get('measurable'))} | {_esc(d.get('touches'))} |")
    lines.append("")
    cal = calibration(ideas, outcomes)
    lines += ["## 予想の当たり方（31-6 の 3。回した案のうち確率つきの予想があるもの）", ""]
    if cal["n"]:
        lines.append(f"- {cal['n']} 本・当たり {cal['acc']:.0%}（確率 ≥ 0.5 と「保留以上が出た」が一致）・Brier {cal['brier']:.3f}（0 が最良・いつも 0.5 と言うと 0.25）")
        if cal["n"] >= 5 and cal["acc"] >= 0.9:
            lines.append("- ⚠ **当たりが 9 割を超えた ＝ 探り方が安全すぎる合図**（枠の外を増やす ／ r を下げるかは利用者）")
        lines += [f"  - `{i}` 確率 {p:.2f} → {'保留以上あり' if h else '保留以上なし'}" for i, p, h in cal["rows"]]
    else:
        lines.append("- まだ無い（契約 2 の案を回すと出る）")
    lines.append("")
    for _p, d, _bad in ideas:
        lines += [f"## `{d.get('id')}` {d.get('title')}", "",
                  f"- 問い: {d.get('question')}", f"- 構造: {d.get('structure')}",
                  "- 失敗の型: " + " ／ ".join(str(x) for x in d.get("failure_modes") or []),
                  f"- 予想（先に書いた）: {d.get('prediction')}"]
        if d.get("learn_if_null"):
            lines.append(f"- 決着なしのとき分かること: {d['learn_if_null']}")
        o = d.get("origin") or {}
        if o.get("refs"):
            lines.append("- 参考にした記録: " + " ／ ".join(f"`{x}`" for x in o["refs"]))
        if d.get("notes"):
            lines.append(f"- 注記: {d['notes']}")
        if d.get("runs"):
            lines.append("- 実行: " + " ／ ".join(f"`{x}`" for x in d["runs"]))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI


def cmd_register(args) -> int:
    cfg = load_config()
    ideas = load_ideas(cfg)
    by_id = {d.get("id"): (p, d, bad) for p, d, bad in ideas}
    changed = False
    for i in args.promote or []:
        if i not in by_id:
            raise SystemExit(f"⚠ 登録簿に {i!r} が無い")
        p, d, bad = by_id[i]
        if d.get("status") not in ("案", "人の手"):
            raise SystemExit(f"⚠ {i}: 登録できるのは 案 ／ 人の手 だけ（いま {d.get('status')}。31-1 の 2）")
        if d.get("status") == "人の手":
            # ⚠ 人の手 ＝ 自動では作らず利用者のセッションで作る（K7）。そのセッションで作ると決めたら、ここで登録する（2026-11-01 の 3 周目）
            d["built_by"] = "人の手（利用者のセッション）"
        if bad:
            raise SystemExit(f"⚠ {i}: 項目がそろっていない ＝ 登録できない（31-1 の 3）: " + "・".join(bad))
        d["status"], d["registered_at"] = "登録", now_seattle()
        write_idea(p, d)
        changed = True
        print(f"  登録 {i} @ {d['registered_at']}", file=sys.stderr)
    for spec in args.set or []:
        i, _, st = spec.partition("=")
        if i not in by_id:
            raise SystemExit(f"⚠ 登録簿に {i!r} が無い")
        p, d, _bad = by_id[i]
        cur = d.get("status")
        if st not in STATUSES or st not in FORWARD.get(cur, set()):
            raise SystemExit(f"⚠ {i}: {cur} → {st} は許されない遷移（31-1 の 2: {sorted(FORWARD.get(cur, set()))}）")
        d["status"] = st
        if st in ("作成中", "回した", "失敗") and not d.get("registered_at"):
            raise SystemExit(f"⚠ {i}: registered_at が無い（先に --promote）")
        write_idea(p, d)
        changed = True
        print(f"  {i}: {cur} → {st}", file=sys.stderr)
    for spec in args.run or []:
        i, _, run = spec.partition("=")
        if i not in by_id or not run:
            raise SystemExit(f"⚠ --run は <id>=<実行名>（登録簿に {i!r} が無いか実行名が空）")
        p, d, _bad = by_id[i]
        if d.get("status") not in ("作成中", "回した"):
            raise SystemExit(f"⚠ {i}: 実行を結べるのは 作成中 ／ 回した だけ（いま {d.get('status')}）")
        d["runs"] = sorted(set(d.get("runs") or []) | {run})
        write_idea(p, d)
        changed = True
        print(f"  {i}: 実行 {run}", file=sys.stderr)
    if changed:
        ideas = load_ideas(cfg)
    models = ledger_models(args.db)
    n_trials = None
    if models is not None:
        from ail import rundb, runs
        conn = rundb.connect(args.db or runs.db_path(), readonly=True)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'ledger_n_trials'").fetchone()
        finally:
            conn.close()
        n_trials = int(row[0]) if row and str(row[0]).isdigit() else None
    text = build_register(cfg, ideas, models, n_trials, run_verdicts(args.db))
    out = args.out or _path(cfg, "register")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    nbad = sum(1 for _p, _d, bad in ideas if bad)
    print(f"wrote {out}（{len(ideas)} 本・項目の足りないもの {nbad}）", file=sys.stderr)
    return 1 if nbad and args.strict else 0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register", help="登録簿を検査し register.md を吐く")
    r.add_argument("--promote", action="append", metavar="ID", help="案 → 登録（registered_at を打つ）")
    r.add_argument("--set", action="append", metavar="ID=状態", help="横道へ（既出 ／ 人の手 ／ 失敗 …）")
    r.add_argument("--run", action="append", metavar="ID=実行名", help="回した実行を登録簿に結ぶ（作成中 ／ 回した のとき）")
    r.add_argument("--out", default=None)
    r.add_argument("--db", default=None)
    r.add_argument("--strict", action="store_true", help="項目の足りない登録簿があれば rc=1")
    n = sub.add_parser("night", help="1 晩（段 0 門 → 段 1〜4 → 日誌 → commit）")
    n.add_argument("--date", default=None, help="YYYY-MM-DD（既定 ＝ 今日・シアトル時間）")
    n.add_argument("--stages", default="0,1,2,3,4", help="回す段（例 0,4）")
    n.add_argument("--dry-run", action="store_true", help="外の世界を呼ばない（門の検査も claude も queue も commit も無し。段 4 と日誌だけ）")
    n.add_argument("--no-commit", action="store_true")
    n.add_argument("--mail", action="store_true", help="dry-run でも報告メールを送る（試験用）")
    n.add_argument("--no-mail", action="store_true", help="この晩は報告メールを送らない")
    sub.add_parser("mail-test", help="報告メールの試しの 1 通（設定ファイルを置いた後に 1 回）")
    st = sub.add_parser("state", help="状態を見る ／ 直す")
    st.add_argument("--reset", action="store_true", help="連続失敗・連続 登録 0 の数を 0 に（打ち切りの解除）")
    st.add_argument("--seed-top", action="store_true", help="いまの上位 3 を last_top に写す（初回に机上を足さないため）")
    args = ap.parse_args(argv)
    if args.cmd == "register":
        raise SystemExit(cmd_register(args))
    if args.cmd == "night":
        raise SystemExit(cmd_night(args))
    if args.cmd == "state":
        raise SystemExit(cmd_state(args))
    if args.cmd == "mail-test":
        raise SystemExit(cmd_mail_test(args))



# ================================================================ 夜の 1 周（段 0 門 → 段 1〜4 → 日誌 → commit。Phase 3・rules.md 31 章）
#
# ⚠ 呼ぶのは `run-devloop.sh`（timer）か人。段ごとに時間と費用の上限・どの段で止まっても日誌は書く・段 4（読むだけ）は必ず回す。
# ⚠ 外の世界（git・claude -p・cli.queue・pytest）は `env` の関数で呼ぶ ＝ テストは差し替える。

import re
import subprocess
import time

from ail import rundb  # noqa: E402

REPO = os.path.abspath(os.path.join(ROOT, "..", ".."))
STATE_KEY = "devloop"                       # research.sqlite の queue_state の 1 行（rules.md 31 章・プラン §3-5）
STAGE_NAMES = {0: "門", 1: "アイデア出し", 2: "作る", 3: "回す", 4: "選ぶ"}
EFFORT_ORDER = {"小": 0, "中": 1, "大": 2}
IDEA_FIELDS = ("title", "kind", "question", "structure", "n_trials_planned", "key_shape", "failure_modes", "prediction",
               "effort", "measurable", "touches")
SHAPE_BUDGET = 600                          # 31-3 の 3: 2a ＝ 5 本・$600


# ---------------------------------------------------------------- 外の世界（差し替え可）


def _sh(cmd: list[str], cwd: str | None = None, timeout: float | None = None) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or "") if isinstance(e.stdout, str) else "", f"timeout {timeout}s"
    except OSError as e:
        return 127, "", str(e)
    return p.returncode, p.stdout or "", p.stderr or ""


def claude_bin() -> str:
    """`claude` の実体。timer の環境（systemd --user）は PATH に `~/.local/bin` を持たない【実測 2026-10-25 titan】ので、順に探す。"""
    import shutil
    for cand in (os.environ.get("AIL_CLAUDE_BIN"), shutil.which("claude"), os.path.expanduser("~/.local/bin/claude")):
        if cand and os.path.exists(cand):
            return cand
    return "claude"


def _claude(prompt: str, *, tools: tuple[str, ...], max_turns: int, budget: float, cwd: str, timeout: float = 3600) -> dict:
    """`claude -p`（headless）を 1 度呼ぶ。⚠ `--max-budget-usd` に当たると結果は 1 文字も返らない（費用だけ払う ＝ dev-loop.md §1-1）。"""
    cmd = ["env", "-u", "CLAUDECODE", claude_bin(), "-p", prompt, "--output-format", "json",
           "--max-turns", str(int(max_turns)), "--max-budget-usd", f"{float(budget):g}", "--tools", ",".join(tools)]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    rc, out, err = _sh(cmd, cwd=cwd, timeout=timeout)
    try:
        d = json.loads(out) if out.strip() else {}
    except ValueError:
        d = {}
    errors = list(d.get("errors") or [])
    if rc and not errors:
        errors.append((err or out).strip()[-300:] or f"rc={rc}")
    return {"result": d.get("result") or "", "cost": float(d.get("total_cost_usd") or 0.0),
            "turns": int(d.get("num_turns") or 0), "errors": errors, "rc": rc}


def default_env(cfg: dict, conn=None) -> dict:
    from ail import runs as _runs
    from cli import queue as _queue

    def _queue_runner(name: str, log) -> dict:
        return _queue.run_queue(name, log=log)

    def _tests(targets: list[str]) -> tuple[int, str]:
        py = os.path.join(ROOT, ".venv", "bin", "python")
        rc, out, err = _sh([py if os.path.exists(py) else sys.executable, "-m", "pytest", "-q", *targets], cwd=ROOT, timeout=1800)
        return rc, (out + err)[-3000:]

    return {"root": ROOT, "repo": REPO, "sh": _sh, "claude": _claude, "queue": _queue_runner, "tests": _tests,
            "conn": conn if conn is not None else rundb.connect(_runs.db_path()),
            "now": lambda: dt.datetime.now(SEATTLE),
            "mail": None}                    # 報告メールの送り手（None ＝ devloop_mail の既定 ＝ SMTP。テストの FakeEnv はこの項目を持たない ＝ 送らない）


# ---------------------------------------------------------------- 状態・錠


def load_state(conn) -> dict:
    doc = rundb.load_queue(conn, STATE_KEY) or {}
    doc.setdefault("consecutive_build_failures", 0)
    doc.setdefault("consecutive_no_new", 0)
    doc.setdefault("cost_usd_total", 0.0)
    doc.setdefault("pending_shapes", [])
    doc.setdefault("nights", [])
    return doc


def save_state(conn, doc: dict) -> None:
    rundb.save_queue(conn, STATE_KEY, doc)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def take_lock(path: str) -> bool:
    """錠（同時に 2 つ走らない）。死んだ pid の錠は外して取り直す。"""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                pid = int((f.read().split() or ["0"])[0])
        except (OSError, ValueError):
            pid = 0
        if pid and _pid_alive(pid):
            return False
        os.remove(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"{os.getpid()} {now_seattle()}\n")
    return True


def release_lock(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------- 段 0 門


def own_outputs(cfg: dict, env: dict) -> list[str]:
    """運転手が毎晩書く置き場（リポジトリからの相対・末尾の / なし）。門の「汚れた木」に数えない。"""
    repo = env.get("repo", REPO)
    paths = [_path(cfg, "log"), _path(cfg, "register"), env.get("drafts_dir") or DRAFTS]
    return [os.path.relpath(os.path.abspath(p), repo).replace(os.sep, "/") for p in paths]


def _is_own(rel: str, own: list[str]) -> bool:
    rel = rel.strip('"').rstrip("/")
    return any(o not in ("", ".") and not o.startswith("..") and (rel == o or rel.startswith(o + "/")) for o in own)


def gate(cfg: dict, env: dict, state: dict) -> str | None:
    """止める理由（None ＝ 通る）。順: HALT → 打ち切り ② ③ → 作業ツリー → git pull --ff-only。"""
    stop = cfg.get("stop") or {}
    halt = os.path.join(ROOT, stop.get("halt_file", "DEVLOOP_HALT"))
    if os.path.exists(halt):
        return f"止める手 {os.path.relpath(halt, ROOT)} がある（外すのは利用者）"
    if state.get("consecutive_build_failures", 0) >= int(stop.get("consecutive_build_failures", 3)):
        return f"打ち切り ②: 段 2 が {state['consecutive_build_failures']} 晩続けて 1 本も通らない（直してから `cli.devloop state --reset`）"
    if state.get("consecutive_no_new", 0) >= int(stop.get("consecutive_no_new", 14)):
        return f"打ち切り ③: {state['consecutive_no_new']} 晩続けて登録 0（r を変えるのは利用者。`cli.devloop state --reset`）"
    git = cfg.get("git") or {}
    if git.get("dirty_tree", "stop") == "stop":
        rc, out, err = env["sh"](["git", "status", "--porcelain"], cwd=env["repo"])
        if rc:
            return f"git status が失敗: {err.strip()[-200:]}"
        # ⚠ 自分の錠（devloop.lock）は数えない（night が門の前に取る ＝ 数えると門は毎晩止まる。2026-11-01 に踏んだ）
        mine = os.path.basename((cfg.get("stop") or {}).get("lock_file", "devloop.lock"))
        # ⚠ 自分が書く置き場（日誌・register.md・週 1 回の下書き）も数えない。コミットの後に日誌を書き直す・止まった晩も
        #    段 4 と日誌は書く ＝ 数えると次の晩から毎晩止まる（2026-11-03 Sx360 が見つけた）。次に通った晩のコミットに入る
        own = own_outputs(cfg, env)
        dirty = [l for l in out.splitlines()
                 if l.strip() and os.path.basename(l[3:].strip()) != mine and not _is_own(l[3:].strip(), own)]
        if dirty:
            return f"作業ツリーが汚れている（利用者の未コミット {len(dirty)} 件: {', '.join(l[3:] for l in dirty[:5])}）"
    if git.get("pull", True):
        rc, out, err = env["sh"](["git", "pull", "--ff-only"], cwd=env["repo"])
        if rc:
            return f"git pull --ff-only が失敗: {(err or out).strip()[-200:]}"
    return None


# ---------------------------------------------------------------- 段 1 アイデア出し


def split_n(cfg: dict) -> tuple[int, int]:
    n = int(cfg["ideation"]["ideas_per_night"])
    r = float(cfg["ideation"]["reference_pct"])
    n_ref = int(round(n * r / 100.0))
    return n_ref, n - n_ref


def prompt_text(cfg: dict, which: str, n: int) -> str:
    d = os.path.join(ROOT, cfg["ideation"].get("prompts", "devloop/prompts"))
    with open(os.path.join(d, "contract.md"), encoding="utf-8") as f:
        contract = f.read()
    with open(os.path.join(d, f"ideate_{which}.md"), encoding="utf-8") as f:
        body = f.read()
    return contract + "\n" + body.replace("{N}", str(n))


def parse_ideas(text: str) -> list[dict]:
    """段 1 の出力（```toml のフェンス 1 つ・`[[idea]]` × N）→ 辞書の一覧。フェンスが無ければ全文を TOML として読む。壊れていれば空。"""
    m = re.search(r"```toml\s*\n(.*?)```", text, re.S)
    src = m.group(1) if m else text
    try:
        doc = tomllib.loads(src)
    except (tomllib.TOMLDecodeError, ValueError):
        return []
    return [d for d in (doc.get("idea") or []) if isinstance(d, dict)]


def _slug(raw: dict, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", str(raw.get("slug") or "").strip().lower()).strip("-")
    return s[:24] or fallback


def _key_shapes(v) -> list[str]:
    """段 1 の `key_shape`（文字列か一覧）→ 予測モデル名のパターンの一覧（θ と括弧書きを落とす）。"""
    parts = v if isinstance(v, list) else re.split(r"[／,、]", str(v or ""))
    out = []
    for p in parts:
        p = re.sub(r"[（(].*?[）)]", "", str(p)).strip()
        p = re.sub(r"@.*$", "", p).strip()
        if p and " " not in p:
            out.append(p)
    return out


def register_ideas(cfg: dict, raw_ideas: list[dict], *, source: str, meta: dict, date: str, models: list[str] | None) -> list[dict]:
    """段 1 の案を登録簿に写し、重複の検査と K7 の線で status を決める（31-1 の 2）: 既出 ／ 人の手 ／ 登録（項目が足りなければ 案 のまま）。"""
    ideas_dir = _path(cfg, "ideas")
    os.makedirs(ideas_dir, exist_ok=True)
    out = []
    for i, raw in enumerate(raw_ideas, 1):
        slug = _slug(raw, f"{source[:3]}{i}")
        base = f"{date.replace('-', '')}-{slug}"
        id_, k = base, 2
        while os.path.exists(os.path.join(ideas_dir, f"{id_}.toml")):
            id_, k = f"{base}-{k}", k + 1
        missing = [f for f in IDEA_FIELDS if f not in raw or raw[f] in ("", [], None)]
        ks = _key_shapes(raw.get("key_shape"))
        doc = {"id": id_, "date": date, "title": str(raw.get("title") or slug), "kind": str(raw.get("kind") or "モデル"),
               "origin": {**meta, "source": source, "refs": [str(x) for x in (raw.get("origin_refs") or [])]},
               "question": str(raw.get("question") or ""), "structure": str(raw.get("structure") or ""),
               "n_trials_planned": raw.get("n_trials_planned") if isinstance(raw.get("n_trials_planned"), int) else 0,
               "key_shape": ks, "failure_modes": [str(x) for x in (raw.get("failure_modes") or [])],
               "prediction": str(raw.get("prediction") or ""), "effort": str(raw.get("effort") or ""),
               "measurable": str(raw.get("measurable") or ""), "touches": str(raw.get("touches") or ""), "status": "案"}
        if raw.get("reference_mode"):
            doc["reference_mode"] = str(raw["reference_mode"])
        for k in ("frame", "learn_if_null"):
            if raw.get(k) not in (None, ""):
                doc[k] = str(raw[k])
        for k in ("p_hold", "p_positive"):
            if isinstance(raw.get(k), (int, float)) and not isinstance(raw.get(k), bool):
                doc[k] = float(raw[k])
        doc["notes"] = f"段 1 の出力の key_shape の原文: {raw.get('key_shape')}"
        bad = validate(doc, os.path.join(ideas_dir, f"{id_}.toml"))
        if bad:
            doc["notes"] += " ／ ⚠ 項目が足りない（案のまま）: " + "・".join(bad)
        else:
            hits = [duplicates([p], models)[0] for p in ks]
            if ks and models is not None and all(h > 0 for h in hits):
                doc["status"] = "既出"
                doc["notes"] += f" ／ 既出: 識別項目が検証結果一覧に既にある（{'・'.join(f'{p} {h} 行' for p, h in zip(ks, hits))}）"
            elif doc["touches"].startswith("共有の経路"):
                doc["status"] = "人の手"           # K7
            else:
                doc["status"], doc["registered_at"] = "登録", now_seattle()
        write_idea(os.path.join(ideas_dir, f"{id_}.toml"), doc)
        out.append(doc)
    return out


def ideate(cfg: dict, env: dict, date: str, log, models: list[str] | None) -> dict:
    n_ref, n_free = split_n(cfg)
    ide = cfg["ideation"]
    split = ide.get("budget_split") or {"ref": 0.7, "free": 0.3}
    total = float(ide.get("max_budget_usd", 6.0))
    out: dict = {"n_ref": n_ref, "n_free": n_free, "bundles": {}, "ideas": [], "cost": 0.0}
    for which, n, tools in (("ref", n_ref, ("Read", "Grep", "Glob")), ("free", n_free, ())):
        if n <= 0:
            continue
        budget = total * float(split.get(which, 0.5))
        prompt = prompt_text(cfg, which, n)
        res, tries = None, 0
        while tries < 2:                                     # ⚠ 1 回だけ再試行（プラン 3-2）
            tries += 1
            res = env["claude"](prompt, tools=tools, max_turns=int(ide.get("max_turns", 40)), budget=budget, cwd=env["repo"])
            out["cost"] += res["cost"]
            if res["result"].strip() and not res["errors"]:
                break
            log(f"  ⚠ 段 1（{which}）{tries} 回目: {'; '.join(res['errors']) or '結果が空'}")
        raw = parse_ideas(res["result"]) if res else []
        meta = {"r_pct": ide["reference_pct"], "n_per_night": ide["ideas_per_night"], "prompt": f"ideate_{which}",
                "contract": int(ide.get("contract", 1)), "model": "claude", "cost_usd": round(res["cost"], 3) if res else 0.0, "turns": res["turns"] if res else 0}
        docs = register_ideas(cfg, raw, source=("reference" if which == "ref" else "independent"), meta=meta, date=date, models=models)
        out["bundles"][which] = {"n": n, "got": len(raw), "cost": round(res["cost"], 3) if res else 0.0, "turns": res["turns"] if res else 0,
                                 "errors": res["errors"] if res else ["呼ばなかった"], "tries": tries, "raw": res["result"] if res else ""}
        out["ideas"] += docs
        log(f"  段 1（{which}）: {len(raw)} ／ {n} 本・${out['bundles'][which]['cost']:.2f}・{out['bundles'][which]['turns']} turns")
    out["counts"] = {s: sum(1 for d in out["ideas"] if d["status"] == s) for s in STATUSES if any(d["status"] == s for d in out["ideas"])}
    # ⚠ 31-6: 枠の外の本数（下限に届かなくても止めない ＝ 日誌に書く）
    need = int(ide.get("outside_frame_min", 2))
    out["outside"] = sum(1 for d in out["ideas"] if d.get("frame") == "外")
    if out["outside"] < need:
        out["outside_short"] = f"枠の外 {out['outside']} 本 ＜ 下限 {need} 本"
        log(f"  ⚠ 段 1: {out['outside_short']}")
    return out


# ---------------------------------------------------------------- 段 2 作る（K3: 試運転は人の手。mode = "claude" のとき）


def _norm(path: str) -> str:
    """`/` 終わり（ディレクトリの印）は残す。"""
    p = os.path.normpath(path).replace(os.sep, "/")
    return p + "/" if str(path).endswith("/") and not p.endswith("/") else p


def allowed_path(cfg: dict, rel_to_root: str) -> bool:
    """`[allowlist]`: forbid が先に当たれば書けない。write の接頭辞（ディレクトリは `/` 終わり・ファイルの接頭辞はそのまま）に当たれば書ける。"""
    p = _norm(rel_to_root)
    al = cfg.get("allowlist") or {}
    for f in al.get("forbid") or []:
        f = _norm(f)
        if p == f or p.startswith(f.rstrip("/") + "/") or (not f.endswith("/") and p.startswith(f) and "/" not in p[len(f):]):
            return False
    for w in al.get("write") or []:
        w = _norm(w)
        if w.endswith("/") and p.startswith(w):
            return True
        if not w.endswith("/") and (p == w or (p.startswith(w) and "/" not in p[len(w):])):
            return True
    return False


def changed_files(env: dict) -> set[str]:
    rc, out, _err = env["sh"](["git", "status", "--porcelain", "--untracked-files=all"], cwd=env["repo"])
    files = set()
    for line in out.splitlines():
        if len(line) > 3:
            name = line[3:].strip()
            if " -> " in name:
                name = name.split(" -> ", 1)[1]
            files.add(name)
    return files


def _rel_root(repo_rel: str) -> str:
    """リポジトリ基準の道 → experiments/feature-discovery 基準（外なら `../` 始まり）。"""
    return _norm(os.path.relpath(os.path.join(REPO, repo_rel), ROOT))


def discard(env: dict, repo_rel: list[str], log) -> None:
    """allowlist の外に書かれたものを捨てる（追跡済み ＝ checkout・未追跡 ＝ 削除）。"""
    for f in repo_rel:
        rc, out, _ = env["sh"](["git", "ls-files", "--error-unmatch", f], cwd=env["repo"])
        if rc == 0:
            env["sh"](["git", "checkout", "--", f], cwd=env["repo"])
        else:
            try:
                os.remove(os.path.join(env["repo"], f))
            except OSError:
                pass
        log(f"  ⚠ allowlist の外に書いたので捨てた: {f}")


def parse_built(text: str) -> dict:
    m = re.search(r"```toml\s*\n(.*?)```", text, re.S)
    try:
        doc = tomllib.loads(m.group(1) if m else text)
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    b = doc.get("built") or []
    return b[0] if b and isinstance(b[0], dict) else {}


def build(cfg: dict, env: dict, state: dict, date: str, log) -> dict:
    b = cfg.get("build") or {}
    ideas = load_ideas(cfg)
    todo = sorted([(p, d) for p, d, bad in ideas if d.get("status") == "登録" and not bad],
                  key=lambda x: (EFFORT_ORDER.get(x[1].get("effort"), 9), x[1]["id"]))
    out: dict = {"mode": b.get("mode", "manual"), "todo": [d["id"] for _p, d in todo], "built": [], "failed": [], "cost": 0.0}
    if out["mode"] != "claude":
        log(f"  段 2 は人の手（mode = {out['mode']}）。登録 {len(todo)} 本はそのまま")
        return out
    with open(os.path.join(ROOT, cfg["ideation"].get("prompts", "devloop/prompts"), "build.md"), encoding="utf-8") as f:
        base_prompt = f.read()
    budget = float(b.get("max_budget_usd", 5.0))
    for p, d in todo:
        remaining = budget - out["cost"]
        if remaining <= 0.5:
            log("  段 2: 費用の上限に達した（残りは翌晩）")
            break
        before = changed_files(env)
        prompt = base_prompt + "\n\n## 登録簿（この 1 本）\n\n```toml\n" + dump_idea(d) + "```\n"
        res = env["claude"](prompt, tools=tuple(b.get("tools") or ("Read", "Grep", "Glob", "Write", "Edit")),
                            max_turns=int(b.get("max_turns", 60)), budget=remaining, cwd=env["repo"])
        out["cost"] += res["cost"]
        new = sorted(changed_files(env) - before)
        outside = [f for f in new if not allowed_path(cfg, _rel_root(f))]
        d["build_attempts"] = int(d.get("build_attempts", 0)) + 1
        reason = None
        if outside:
            discard(env, new, log)                       # ⚠ その案の変更は全部捨てる（混ぜない）
            reason = "allowlist の外に書いた: " + ", ".join(outside[:5])
        else:
            built = parse_built(res["result"])
            if built.get("status") == "人の手":
                d["status"] = "人の手"
                d["notes"] = (d.get("notes", "") + " ／ 段 2: " + str(built.get("reason") or "共有の経路")).strip(" ／")
                write_idea(p, d)
                log(f"  {d['id']}: 人の手（{built.get('reason') or '共有の経路'}）")
                continue
            exps = [str(x) for x in (built.get("experiments") or [])]
            tests = sorted(f for f in new if f.startswith("experiments/feature-discovery/tests/test_ideas_"))
            if not exps:
                reason = "出力に experiments が無い"
            elif not tests:
                reason = "tests/test_ideas_<id>.py が無い（「無い config は 1 ビットも変わらない」の 1 本が要る）"
            else:
                rc, tail = env["tests"]([_rel_root(t) for t in tests] + ["tests/test_predict.py", "tests/test_trading_run.py", "tests/test_names.py", "tests/test_catalog.py"])
                if rc != 0:
                    reason = "テストが落ちた: " + tail.strip().splitlines()[-1][:200] if tail.strip() else "テストが落ちた"
                    discard(env, new, log)
                else:
                    d["experiments"], d["status"] = exps, "作成中"
                    write_idea(p, d)
                    out["built"].append(d["id"])
                    log(f"  {d['id']}: 作成中（{', '.join(exps)}・${res['cost']:.2f}）")
                    continue
        if reason:
            if d["build_attempts"] >= int(b.get("retries_per_idea", 2)):
                d["status"] = "失敗"
            d["notes"] = (d.get("notes", "") + f" ／ 段 2 {d['build_attempts']} 回目: {reason}").strip(" ／")
            write_idea(p, d)
            out["failed"].append({"id": d["id"], "reason": reason, "status": d["status"]})
            log(f"  ⚠ {d['id']}: {reason}（{d['status']}）")
    return out


# ---------------------------------------------------------------- 段 3 回す


def plan_queue(cfg: dict, ideas: list[tuple[str, dict, list[str]]], state: dict) -> tuple[list[str], list[str], int]:
    """その晩に回す実験の並び（state の pending_shapes ＝ 前の晩の段 4 が足した机上 → 作成中の案を手間の小さい順）。
    1 晩に数える検証数の上限（`max_trials_per_night`）に収まるものだけ。返すのは (実験名, 選んだ id, 数える検証数)。"""
    cap = int((cfg.get("run") or {}).get("max_trials_per_night", 12))
    exps = list(dict.fromkeys(state.get("pending_shapes") or []))
    count = 2 * len(exps)
    picked: list[str] = []
    for _p, d, bad in sorted([x for x in ideas if x[1].get("status") == "作成中" and x[1].get("experiments")],
                             key=lambda x: (EFFORT_ORDER.get(x[1].get("effort"), 9), x[1]["id"])):
        if bad:
            continue
        n = int(d.get("n_trials_planned") or 0)
        if count + n > cap:
            continue
        exps += [e for e in d["experiments"] if e not in exps]
        count += n
        picked.append(d["id"])
    return exps, picked, count


def write_queue(cfg: dict, name: str, exps: list[str], time_budget_s: float, note: str) -> str:
    run = cfg.get("run") or {}
    path = os.path.join(ROOT, "config", "queue", f"{name}.toml")
    lines = [f"# ⚠ 開発ループの晩の queue（rules.md 31 章。cli.devloop が書く）。{note}",
             "# ⚠ 回すと決めたのは結果を見る前（登録簿の registered_at）。表は作り直さない（features_from）。",
             "experiments = [" + ", ".join(json.dumps(e) for e in exps) + "]", "",
             f"leak = {'true' if run.get('leak', True) else 'false'}",
             f"ledger = {'true' if run.get('ledger', True) else 'false'}",
             f"max_failures = {int(run.get('max_failures', 1))}",
             f"time_budget_s = {int(time_budget_s)}", ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def n_trials_of(conn) -> int | None:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'ledger_n_trials'").fetchone()
    except sqlite3.Error:
        return None
    return int(row[0]) if row and str(row[0]).isdigit() else None


def run_stage(cfg: dict, env: dict, state: dict, date: str, log, deadline: float | None = None) -> dict:
    ideas = load_ideas(cfg)
    exps, picked, count = plan_queue(cfg, ideas, state)
    out: dict = {"queue": None, "experiments": exps, "picked": picked, "planned": count, "before": n_trials_of(env["conn"]), "after": None,
                 "done": [], "failed": []}
    if not exps:
        log("  段 3: 回すものが無い（作成中の案も机上の持ち越しも無い）")
        return out
    base = f"dev_{date.replace('-', '')}"
    name = base
    k = 2
    while os.path.exists(os.path.join(ROOT, "config", "queue", f"{name}.toml")):
        name, k = f"{base}_{k}", k + 1
    budget = float((cfg.get("run") or {}).get("time_budget_s", 5400))
    if deadline is not None:
        budget = max(60.0, min(budget, deadline - time.time() - 120))
    write_queue(cfg, name, exps, budget, f"晩 {date}・案 {', '.join(picked) or '—'}・机上の持ち越し {len(state.get('pending_shapes') or [])}・数える {count}")
    out["queue"] = name
    log(f"  段 3: queue {name}（{len(exps)} 本・数える {count}・上限 {budget:.0f}s）")
    st = env["queue"](name, log)
    items = st.get("items") or []
    runs_by_exp = {i["experiment"]: i.get("run_dir") for i in items if i.get("status") == "done" and not i.get("leak")}
    out["done"] = sorted(runs_by_exp)
    out["failed"] = sorted({i["experiment"] for i in items if i.get("status") != "done" and not i.get("leak")})
    by_id = {d["id"]: (p, d) for p, d, _b in ideas}
    for id_ in picked:
        p, d = by_id[id_]
        got = [runs_by_exp[e] for e in d["experiments"] if runs_by_exp.get(e)]
        d["runs"] = sorted(set(d.get("runs") or []) | set(got))
        if all(e in runs_by_exp for e in d["experiments"]):
            d["status"] = "回した"
        else:
            d["notes"] = (d.get("notes", "") + f" ／ 段 3 {date}: 回し切れていない（{', '.join(e for e in d['experiments'] if e not in runs_by_exp)}）").strip(" ／")
        write_idea(p, d)
    state["pending_shapes"] = [e for e in (state.get("pending_shapes") or []) if e not in runs_by_exp]
    out["after"] = n_trials_of(env["conn"])
    return out


# ---------------------------------------------------------------- 段 4 選ぶ（読むだけ ＋ 新顔の机上の config）


def _shape_exists(models: set[str], base_model: str) -> bool:
    parts = base_model.split(".", 2)
    if len(parts) < 3:
        return True
    return f"{parts[0]}.{parts[1]}-tr5-600.{parts[2]}" in models


def select_stage(cfg: dict, env: dict, state: dict, date: str, log) -> dict:
    from cli import select_candidates as sc
    from cli import trader_shape as ts

    sel = cfg.get("select") or {}
    res = sc.select(env["conn"], top=int(sel.get("top", 3)), diag=True)
    names_now = [r.name for r in res["top"]]
    out: dict = {"top": [(r.name, r.method, r.fold, r.edge, r.theta, r.marks, r.diag) for r in res["top"]], "n_trials": res.get("n_trials"),
                 "render": sc.render(res), "new": [], "shaped": [], "first": "last_top" not in state}
    if out["first"]:
        log("  段 4: 初回なので上位を写すだけ（机上に足さない）")
    else:
        out["new"] = [n for n in names_now if n not in (state.get("last_top") or [])]
        if sel.get("shape_new_only", True) and out["new"]:
            models = {str(r[0]) for r in env["conn"].execute("SELECT DISTINCT model_name FROM ledger_rows WHERE leak = 0")}
            for n in out["new"]:
                row = next(r for r in res["top"] if r.name == n)
                if sc.is_trader_shape(row.method):
                    continue
                if _shape_exists(models, row.model):
                    log(f"  段 4: {n} は 5 本・$600 の形が既にある")
                    continue
                try:
                    t = ts.resolve_trial(env["conn"], n)
                    cname = f"dev_tr_{re.sub(r'[^a-z0-9]+', '-', t['experiment'].lower()).strip('-')}_{t['theta']}"
                    path = ts.source_path(cname)
                    if not os.path.exists(path):
                        text = ts.shape_config(ts.source_text(t["experiment"]), src=t["experiment"], methods=[t["method"]],
                                               theta=t["theta"], budget=SHAPE_BUDGET, name=cname)
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(text)
                    out["shaped"].append(cname)
                    log(f"  段 4: 新顔 {n} → 机上 {cname}（次の晩の queue。＋2）")
                except SystemExit as e:
                    log(f"  ⚠ 段 4: {n} の机上の config を書けない: {e}")
    pend = list(state.get("pending_shapes") or [])
    state["pending_shapes"] = pend + [c for c in out["shaped"] if c not in pend]
    state["last_top"] = names_now
    return out


# ---------------------------------------------------------------- 週 1 回の候補の棚（rules.md 31-3 の 4・プラン 4-2・2026-11-01）

WEEKLY_WEEKDAY = 5          # 土曜 01:00 の回 ＝ 金曜の晩（31-3 の 4）
DRAFTS = os.path.abspath(os.path.join(ROOT, "..", "..", "docs", "specs", "experiments", "dev-loop", "drafts"))   # ⚠ 執行器の側（live-trading/）には書かない（allowlist の forbid）
_RUN_ID = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_(.+)$")


def is_weekly_night(date: str) -> bool:
    return dt.date.fromisoformat(date).weekday() == WEEKLY_WEEKDAY


def draft_name(model: str, theta: str) -> str:
    """下書きの識別名（ファイル名）。⚠ 本番の識別名（T<数>）は利用者が付ける ＝ これは仮の名前。"""
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")[:60]
    return f"dev_{slug}_{theta}"


def draft_toml(name: str, *, experiment: str, method: str, theta: str, trial: str, date: str) -> str:
    """配線だけの人の下書き（howto 2 章 2-1 の形・5 本・$600・整数株・asis）。"""
    return (f"# ⚠ **下書き（候補の人ではない・本番の人ではない）**。開発ループの週 1 回の段（rules.md 31-3 の 4）が {date} に書いた。\n"
            f"#    元の行: `{trial}`（20 章の机上で 採る ＝ 対「持ち続ける」の上乗せ > 0 かつ fold 5/5）。⚠ 成績で人を選ばない\n"
            "# ⚠ ここ（docs/specs/experiments/dev-loop/drafts/）は外部のタブも執行器も読まない。候補にするなら利用者が呼び名を決め、識別名 T<数> で\n#    experiments/live-trading/config/traders/candidates/dev/ へ写し、\n"
            "#    models.toml ／ traders.toml ／ compare.toml を書く（howto 2-1〜2-6）。cli.predict で予測が出るかを先に確かめる\n\n"
            f'name = "{name}"\n'
            "test = false\n"
            "budget_usd = 600.0\n"
            'symbols = ["T", "PFE", "NKE", "VZ", "BAC"]\n'
            'sizing = "shares"\n'
            'combine = "asis"\n'
            f"threshold = {float(theta):.1f}\n\n"
            "[[models]]\n"
            'kind = "experiment"\n'
            f'name = "{experiment}"\n'
            f'method = "{method}"\n')


def weekly_stage(cfg: dict, env: dict, date: str, log, drafts_dir: str | None = None) -> dict:
    """週 1 回: `select_candidates.weekly` の行を並べ、1 本の手法の行だけ下書きを書く（合成の行は人の手）。読むのは DB と config だけ。"""
    from cli import select_candidates as sc

    drafts_dir = drafts_dir or DRAFTS
    rows = sc.weekly(env["conn"])
    out: dict = {"rows": [(r.name, r.method, r.fold, r.edge, r.theta) for r in rows], "drafts": [], "manual": []}
    for r in rows:
        base = r.method[: -len(sc.SHAPE_600)]
        m = _RUN_ID.match((r.runs or [""])[-1])
        if not m:
            out["manual"].append((r.name, "実行名から実験名を引けない"))
            continue
        exp = m.group(1).removesuffix("_leak")
        try:
            with open(os.path.join(ROOT, "config", "experiment", f"{exp}.toml"), "rb") as f:
                cfg_exp = tomllib.load(f)
        except OSError:
            out["manual"].append((r.name, f"config/experiment/{exp}.toml が無い"))
            continue
        if base in {str(x.get("name")) for x in ((cfg_exp.get("trading") or {}).get("mix") or [])}:
            out["manual"].append((r.name, "合成の行（members と役割を人が写す）"))
            continue
        name = draft_name(r.model, r.theta)
        path = os.path.join(drafts_dir, f"{name}.toml")
        if not os.path.exists(path):
            os.makedirs(drafts_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(draft_toml(name, experiment=exp, method=base, theta=r.theta, trial=r.name, date=date))
        out["drafts"].append(name)
        log(f"  週 1 回: {r.name} → 下書き {name}")
    if not rows:
        log("  週 1 回: 候補の棚に載る行なし")
    return out


# ---------------------------------------------------------------- 日誌・commit・1 晩


def _journal_path(cfg: dict, date: str) -> str:
    d = _path(cfg, "log")
    os.makedirs(d, exist_ok=True)
    # ⚠ 同じ日の 2 回目以降は <日付>-2.md … に書く（前の回の日誌を上書きしない。2026-11-01 に踏んだ）
    path, k = os.path.join(d, f"{date}.md"), 2
    while os.path.exists(path):
        path, k = os.path.join(d, f"{date}-{k}.md"), k + 1
    return path


def write_journal(cfg: dict, date: str, parts: dict, path: str) -> str:
    ide = cfg["ideation"]
    L = [f"# 開発ループの日誌 — {date}", "",
         f"⚠ 機械で書く（`cli.devloop night`。rules.md 31 章）。手で書き換えない。生成 {now_seattle()} ／ r {ide['reference_pct']}%・N {ide['ideas_per_night']} ／ "
         f"段 {parts.get('stages')} ／ 所要 {parts.get('seconds', 0):.0f} 秒 ／ 費用 ${parts.get('cost', 0.0):.2f}", ""]
    if parts.get("stopped"):
        L += [f"## ⚠ 止まった: {parts['stopped']}", ""]
    g = parts.get("gate")
    if g is not None:
        L += ["## 段 0 門", "", ("✅ 通った" if g is None or g == "" else f"⚠ {g}"), ""]
    s1 = parts.get("ideate")
    if s1:
        c = s1.get("counts") or {}
        L += [f"## 段 1 アイデア出し — 参考あり {s1['n_ref']} ／ 参考なし {s1['n_free']} → " + "・".join(f"{k} {v}" for k, v in c.items()) + f"（${s1['cost']:.2f}）", ""]
        for which, b in (s1.get("bundles") or {}).items():
            L.append(f"- {which}: {b['got']} ／ {b['n']} 本・${b['cost']:.2f}・{b['turns']} turns・{b['tries']} 回" + (f"・⚠ {'; '.join(b['errors'])}" if b["errors"] else ""))
        if s1.get("outside_short"):
            L.append(f"- ⚠ {s1['outside_short']}（31-6 の 1。止めない）")
        L += ["", "| id | 題 | 枠 | 保留以上の確率 | 状態 | 手間 | 数える | 触る場所 |", "| --- | --- | --- | ---: | --- | --- | ---: | --- |"]
        for d in s1.get("ideas") or []:
            ph = f"{float(d['p_hold']):.2f}" if isinstance(d.get("p_hold"), (int, float)) else "—"
            L.append(f"| `{d['id']}` | {_esc(d['title'])} | {d.get('frame') or '—'} | {ph} | {d['status']} | {d.get('effort')} | {d.get('n_trials_planned')} | {_esc(d.get('touches'))} |")
        L.append("")
    elif parts.get("ideate_error"):
        L += ["## 段 1 アイデア出し", "", f"⚠ 落ちた: {parts['ideate_error']}", ""]
    s2 = parts.get("build")
    if s2:
        L += [f"## 段 2 作る — mode {s2['mode']}・登録 {len(s2['todo'])} 本", ""]
        if s2["mode"] != "claude":
            L.append("- 人の手（朝に Claude のセッションで `devloop/prompts/build.md` の手順で）: " + (", ".join(f"`{i}`" for i in s2["todo"]) or "—"))
        else:
            L.append(f"- 作成中 {len(s2['built'])}（{', '.join(s2['built']) or '—'}）／ 失敗 {len(s2['failed'])}・${s2['cost']:.2f}")
            for f in s2["failed"]:
                L.append(f"  - `{f['id']}`: {f['reason']}（{f['status']}）")
        L.append("")
    elif parts.get("build_error"):
        L += ["## 段 2 作る", "", f"⚠ 落ちた: {parts['build_error']}", ""]
    s3 = parts.get("run")
    if s3:
        L += [f"## 段 3 回す — queue {s3['queue'] or '—'}・数える {s3['planned']}・n_trials {s3.get('before')} → {s3.get('after')}", ""]
        L.append("- 実験: " + (", ".join(f"`{e}`" for e in s3["experiments"]) or "—"))
        L.append("- 済み: " + (", ".join(s3["done"]) or "—") + " ／ 失敗: " + (", ".join(s3["failed"]) or "—"))
        L.append("")
    elif parts.get("run_error"):
        L += ["## 段 3 回す", "", f"⚠ 落ちた: {parts['run_error']}", ""]
    s4 = parts.get("select")
    if s4:
        L += [f"## 段 4 選ぶ — 上位 {len(s4['top'])}（n_trials {s4.get('n_trials')}）" + ("・初回は写すだけ" if s4.get("first") else ""), ""]
        for i, (name, method, fold, edge, theta, marks, diag) in enumerate(s4["top"], 1):
            dsr = (diag or {}).get("dsr")
            L.append(f"{i}. `{name}` {method}・θ {theta}・{fold}・上乗せ {edge:+,.1f}bp/fold・DSR {'—' if dsr is None else f'{dsr:.2f}'}"
                     + (f"・印: {' ／ '.join(marks)}" if marks else ""))
        L.append("")
        L.append("- 新顔: " + (", ".join(f"`{n}`" for n in s4["new"]) or "—") + " ／ 次の晩の机上: " + (", ".join(f"`{c}`" for c in s4["shaped"]) or "—"))
        L.append("- ⚠ 並べたものであって採用ではない（候補の人・本番は利用者。成績で人を選ばない）")
        L.append("")
    elif parts.get("select_error"):
        L += ["## 段 4 選ぶ", "", f"⚠ 落ちた: {parts['select_error']}", ""]
    wk = parts.get("weekly")
    if wk is not None:
        L += [f"## 週 1 回の候補の棚（rules.md 31-3 の 4）— 採るの 5 本・$600 の行 {len(wk['rows'])}", ""]
        for name, method, fold, edge, theta in wk["rows"]:
            L.append(f"- `{name}` {method}・θ {theta}・{fold}・上乗せ {edge:+,.1f}bp/fold")
        L.append("- 下書き（`docs/specs/experiments/dev-loop/drafts/`）: " + (", ".join(f"`{d}`" for d in wk["drafts"]) or "—")
                 + " ／ 人の手: " + (", ".join(f"`{n}`（{why}）" for n, why in wk["manual"]) or "—"))
        L.append("- ⚠ 候補にするか・呼び名・本番に入れるかは利用者（成績で人を選ばない）")
        L.append("")
    elif parts.get("weekly_error"):
        L += ["## 週 1 回の候補の棚", "", f"⚠ 落ちた: {parts['weekly_error']}", ""]
    st = parts.get("state") or {}
    L += ["## 状態", "", f"- 連続で段 2 が 0 本: {st.get('consecutive_build_failures', 0)} ／ 連続で登録 0: {st.get('consecutive_no_new', 0)} ／ "
          f"費用の累計 ${st.get('cost_usd_total', 0.0):.2f} ／ 机上の持ち越し: {', '.join(st.get('pending_shapes') or []) or '—'}", ""]
    L += ["## commit", "", f"- {parts.get('commit') or '未コミット'}", ""]
    if s1:
        L += ["## 段 1 の原文（良し悪しの印は無い ＝ 利用者の指示 2026-10-24）", ""]
        for which, b in (s1.get("bundles") or {}).items():
            L += [f"### {'参考あり' if which == 'ref' else '参考なし'}（{which}）", "", b.get("raw") or "（空）", ""]
    text = "\n".join(L)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def commit(cfg: dict, env: dict, date: str, summary: str, log) -> str:
    """K5: 毎晩 1 コミット（`main` だけ・頭に `devloop:`）。⚠ `prod` は進めない。失敗は文で返す（止めない）。"""
    git = cfg.get("git") or {}
    if not git.get("commit", True):
        return "未コミット（config の git.commit = false）"
    rc, out, _ = env["sh"](["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=env["repo"])
    branch = out.strip()
    if branch != git.get("branch", "main"):
        return f"未コミット（いまのブランチ {branch} ≠ {git.get('branch', 'main')}）"
    paths = ["experiments/feature-discovery/config/ideas", "experiments/feature-discovery/config/queue", "experiments/feature-discovery/config/experiment",
             "experiments/feature-discovery/config/names.toml", "experiments/feature-discovery/config/catalog_notes.toml",
             "experiments/feature-discovery/ail/ideas", "experiments/feature-discovery/tests", "docs/specs/experiments/dev-loop",
             "docs/specs/experiments/feature-discovery/ledger.md"]
    env["sh"](["git", "add", "-A", "--", *[p for p in paths if os.path.exists(os.path.join(env["repo"], p))]], cwd=env["repo"])
    rc, out, _ = env["sh"](["git", "diff", "--cached", "--quiet"], cwd=env["repo"])
    if rc == 0:
        return "未コミット（変更なし）"
    msg = f"devloop: {date} {summary}\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
    rc, out, err = env["sh"](["git", "commit", "-q", "-m", msg], cwd=env["repo"])
    if rc:
        return f"コミット失敗: {(err or out).strip()[-200:]}"
    rc, sha, _ = env["sh"](["git", "rev-parse", "--short", "HEAD"], cwd=env["repo"])
    res = f"コミット {sha.strip()}"
    if git.get("push", True):
        rc, out, err = env["sh"](["git", "push", "origin", git.get("branch", "main")], cwd=env["repo"])
        res += "・push ✅" if rc == 0 else f"・⚠ push 失敗: {(err or out).strip()[-200:]}"
    log(f"  {res}")
    return res


def report(cfg: dict, env: dict, date: str, parts: dict, journal_text: str, journal_path: str | None, log, *,
           mail: bool | None, dry_run: bool) -> str | None:
    """報告メール（プラン docs/plans/devloop-mail.md）。日誌とコミットの後に 1 通。⚠ 送れなくても止めない・日誌は書き換えない。

    mail: None ＝ dry-run でなければ送る ／ True ＝ 送る ／ False ＝ 送らない。⚠ env に "mail" が無ければ送らない（テストの FakeEnv）。
    """
    from cli import devloop_mail as dm
    if not ((not dry_run) if mail is None else mail):
        return None
    m = dm.mail_cfg(cfg)
    try:
        if m.get("send") == "problems" and not dm.is_problem(parts):
            status = "not-sent（send = problems・問題の無い晩）"
        elif "mail" not in env:
            status = "not-sent（env に送り手が無い）"
        else:
            rel = os.path.relpath(journal_path, REPO) if journal_path else "—"
            status = dm.deliver(m, dm.subject(parts, date, m["subject_prefix"]),
                                dm.body(parts, date, journal_text, rel, int(m["max_body_chars"])), sender=env["mail"])
    except Exception as e:                   # noqa: BLE001  ⚠ ループを止めない
        status = f"error: {type(e).__name__}: {str(e)[:200]}"
    dm.mark(status)
    parts["mail"] = status
    log(f"報告メール: {status}")
    return status


def night(cfg: dict, env: dict, *, date: str | None = None, stages: tuple[int, ...] = (0, 1, 2, 3, 4), dry_run: bool = False,
          log=print, journal: bool = True, mail: bool | None = None) -> dict:
    """1 晩。段 0 で止まれば段 1〜3 は回さず段 4 だけ回して日誌を書く。どの段が落ちても日誌は書く。最後に報告メール。"""
    t0 = time.time()
    date = date or env["now"]().strftime("%Y-%m-%d")
    conn = env["conn"]
    state = load_state(conn)
    parts: dict = {"stages": ",".join(str(s) for s in stages), "cost": 0.0}
    lock = os.path.join(ROOT, (cfg.get("stop") or {}).get("lock_file", "devloop.lock"))
    if not take_lock(lock):
        parts["stopped"] = f"錠 {os.path.relpath(lock, ROOT)} が取れない（別の運転手が動いている）"
        log(f"⚠ {parts['stopped']}")
        text, path = "", None
        if journal:
            path = _journal_path(cfg, date)
            text = write_journal(cfg, date, parts, path)
        report(cfg, env, date, parts, text, path, log, mail=mail, dry_run=dry_run)
        return parts
    deadline = t0 + float((cfg.get("schedule") or {}).get("total_hours", 3)) * 3600
    journal_text, journal_path = "", None
    try:
        models = None
        try:
            models = [str(r[0]) for r in conn.execute("SELECT DISTINCT model_name FROM ledger_rows WHERE leak = 0 AND is_trial = 1")]
        except sqlite3.Error:
            pass
        if 0 in stages:
            why = gate(cfg, env, state) if not dry_run else None
            parts["gate"] = why or ""
            if why:
                parts["stopped"] = f"段 0: {why}"
                log(f"⚠ 段 0 門: {why}")
        if 1 in stages and not parts.get("stopped") and not dry_run:
            try:
                s1 = ideate(cfg, env, date, log, models)
                parts["ideate"] = s1
                parts["cost"] += s1["cost"]
                new = s1["counts"].get("登録", 0)
                state["consecutive_no_new"] = 0 if new else state.get("consecutive_no_new", 0) + 1
            except Exception as e:           # noqa: BLE001  ⚠ 日誌に残して先へ
                parts["ideate_error"] = f"{type(e).__name__}: {e}"
                log(f"⚠ 段 1 が落ちた: {parts['ideate_error']}")
        if 2 in stages and not parts.get("stopped") and not dry_run:
            try:
                s2 = build(cfg, env, state, date, log)
                parts["build"] = s2
                parts["cost"] += s2["cost"]
                if s2["mode"] == "claude":
                    state["consecutive_build_failures"] = 0 if s2["built"] or not s2["todo"] else state.get("consecutive_build_failures", 0) + 1
            except Exception as e:           # noqa: BLE001
                parts["build_error"] = f"{type(e).__name__}: {e}"
                log(f"⚠ 段 2 が落ちた: {parts['build_error']}")
        if 3 in stages and not parts.get("stopped") and not dry_run:
            try:
                parts["run"] = run_stage(cfg, env, state, date, log, deadline=deadline)
            except Exception as e:           # noqa: BLE001
                parts["run_error"] = f"{type(e).__name__}: {e}"
                log(f"⚠ 段 3 が落ちた: {parts['run_error']}")
        if 4 in stages:                      # ⚠ 読むだけ ＝ 止まっても必ず回す
            try:
                parts["select"] = select_stage(cfg, env, state, date, log)
            except Exception as e:           # noqa: BLE001
                parts["select_error"] = f"{type(e).__name__}: {e}"
                log(f"⚠ 段 4 が落ちた: {parts['select_error']}")
            if is_weekly_night(date):        # ⚠ 週 1 回（31-3 の 4）。読む ＋ 下書きを書くだけ
                try:
                    parts["weekly"] = weekly_stage(cfg, env, date, log, drafts_dir=env.get("drafts_dir"))
                except Exception as e:       # noqa: BLE001
                    parts["weekly_error"] = f"{type(e).__name__}: {e}"
                    log(f"⚠ 週 1 回の段が落ちた: {parts['weekly_error']}")
        state["cost_usd_total"] = round(float(state.get("cost_usd_total", 0.0)) + parts["cost"], 3)
        state["last_night"] = date
        state["nights"] = (state.get("nights") or [])[-29:] + [{"date": date, "cost": round(parts["cost"], 3), "stopped": parts.get("stopped") or "",
                                                                "registered": (parts.get("ideate") or {}).get("counts", {}).get("登録", 0),
                                                                "n_trials": (parts.get("run") or {}).get("after")}]
        parts["state"] = state
        parts["seconds"] = time.time() - t0
        if not dry_run:
            save_state(conn, state)
            try:
                cmd_register(argparse.Namespace(promote=None, set=None, run=None, out=None, db=None, strict=False))
            except SystemExit:
                pass
        if journal:
            path = journal_path = _journal_path(cfg, date)
            journal_text = write_journal(cfg, date, parts, path)
            if not dry_run and not parts.get("stopped"):
                c = (parts.get("ideate") or {}).get("counts", {})
                summ = (f"段 1 {sum(c.values())} 本（登録 {c.get('登録', 0)}・既出 {c.get('既出', 0)}・人の手 {c.get('人の手', 0)}）"
                        f" ／ 段 3 n_trials {(parts.get('run') or {}).get('before')} → {(parts.get('run') or {}).get('after')}"
                        f" ／ 段 4 新顔 {len((parts.get('select') or {}).get('new', []))}")
                parts["commit"] = commit(cfg, env, date, summ, log)
                journal_text = write_journal(cfg, date, parts, path)   # commit の結果を書き足す（⚠ 日誌自身は次の晩のコミットに入る ＝ 門は数えない）
            log(f"日誌 {os.path.relpath(path, REPO)}")
    finally:
        release_lock(lock)
    status = report(cfg, env, date, parts, journal_text, journal_path, log, mail=mail, dry_run=dry_run)
    if status is not None and not dry_run and (state.get("nights") or []):
        state["nights"][-1]["mail"] = status          # ⚠ 状態にだけ残す（日誌は書き換えない）
        try:
            save_state(conn, state)
        except sqlite3.Error:
            pass
    return parts


def cmd_night(args) -> int:
    cfg = load_config()
    env = default_env(cfg)
    stages = tuple(int(s) for s in str(args.stages).split(",") if s.strip())
    if args.no_commit:
        cfg = {**cfg, "git": {**(cfg.get("git") or {}), "commit": False}}
    mail = True if args.mail else False if args.no_mail else None
    parts = night(cfg, env, date=args.date, stages=stages, dry_run=args.dry_run, mail=mail)
    return 2 if parts.get("stopped") else 0


def cmd_mail_test(args) -> int:
    from cli import devloop_mail as dm
    m = dm.mail_cfg(load_config())
    subj = f"{m['subject_prefix']} 試しの 1 通 {now_seattle()}"
    text = ("開発ループの報告メールの試し（`cli.devloop mail-test`）。\n\n"
            "これが届けば、毎晩 01:00 PST の回の最後に同じ宛先へ 1 通届く（止まった晩も）。\n"
            f"設定ファイル: {dm.secrets_path(m)}\n")
    status = dm.deliver(m, subj, text)
    print(f"報告メール（試し）: {status}", file=sys.stderr)
    return 0 if status.startswith("sent") else 1


def cmd_state(args) -> int:
    cfg = load_config()
    from ail import runs as _runs
    conn = rundb.connect(_runs.db_path())
    st = load_state(conn)
    if args.reset:
        st["consecutive_build_failures"], st["consecutive_no_new"] = 0, 0
        save_state(conn, st)
        print("  連続失敗・連続 登録 0 の数を 0 にした", file=sys.stderr)
    if args.seed_top:
        from cli import select_candidates as sc
        res = sc.select(conn, top=int((cfg.get("select") or {}).get("top", 3)), diag=False)
        st["last_top"] = [r.name for r in res["top"]]
        save_state(conn, st)
        print("  いまの上位を写した: " + ", ".join(st["last_top"]), file=sys.stderr)
    print(json.dumps(st, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    main()
