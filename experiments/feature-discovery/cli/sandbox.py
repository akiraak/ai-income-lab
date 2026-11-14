"""開発ループのサンドボックス（プラン docs/plans/devloop-sandbox.md）。

共有の経路（`cli/run.py`・`ail/validation` など）に触れる案を、**本線（`main`）にも本線の DB にも入れずに**作って回すための土台。
案ごとに 1 つ、`git worktree`（ブランチ `sandbox/<id>`）を本線のリポジトリの外に作る。

    python3 -m cli.sandbox create <id>                      # worktree ＋ ブランチ ＋ 日足のコピー
    python3 -m cli.sandbox tables <id> <実験名> [...]        # その実験が読むテーブルを本線からコピー（無いものは飛ばす）
    python3 -m cli.sandbox exec <id> -- -m cli.queue --config <名前>   # サンドボックスの中で python を起動する
    python3 -m cli.sandbox check <id>                        # main との diff を調べる（禁止パス・既存テストの改変）
    python3 -m cli.sandbox report <id>                       # サンドボックスの DB から結果の要約（JSON）
    python3 -m cli.sandbox record <id> [--note … | --note-file …] [--push]   # 結果を記録する（ブランチにコミット・結果の表・レジストリを「回した」に ＝ rules.md 31-7 の 5）
    python3 -m cli.sandbox list ／ remove <id> [--delete-branch]

なぜ分かれるか（どれも「コードの場所からの相対パス」で決まる ＝ 環境変数は要らない）:
  - コード ＝ ワーキングツリーの `ail/`・`cli/`（`python -m` は cwd を先に読む）
  - 検証の記録 ＝ ワーキングツリーの `runs/research.sqlite`（`ail/runs.py` の `RUNS`）。⚠ 作るときに**本線の DB をコピーして種にする**
    （空の DB では `config/catalog_notes.toml` の閉じる注記が 1 行にも当たらず、検証結果一覧を再生成できない【実測 2026-11-13】。
    種があれば、既出の検査・DSR の `n_trials`・「既存の行が 1 行も変わらない」の確認が本線と同じ条件でできる）
  - ⚠ 検証結果一覧は、古い実行（期間を記録していない実行）の「期間」を**テーブルの実物**から読む（`ail/catalog.py` の `_period_of` の 3 段目）。
    その 27 ファイル・1.4GB【実測】も作るときにコピーする（無いと期間が「—」になり、識別項目が変わって閉じる注記が外れる）
  - 日足とテーブル ＝ ワーキングツリーの `data/`（`ail/data/store.py` の `DATA`）。⚠ 本線の `data/` は**コピー**する
    （シンボリックリンク・ハードリンクにしない ＝ サンドボックスのコードの上書きが本物に届く）

⚠ **論理的な隔離であって OS レベルの隔離ではない**（プラン §2-1）。サンドボックスのコードは titan のユーザー権限で動く。
⚠ このファイルは本線のコードとして動き、サンドボックスの中のコードを import しない（subprocess で起動するだけ）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tomllib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO = os.path.abspath(os.path.join(ROOT, "..", ".."))
FD = "experiments/feature-discovery"            # リポジトリの中の、このディレクトリのパス
BRANCH_PREFIX = "sandbox/"
DATA_PARTS = ("raw", "adjusted", "derived", "manifests")     # 毎回まるごとコピーする（256MB【実測 2026-11-13】）。テーブルは要るものだけ
ID_RE = re.compile(r"^[0-9a-z][0-9a-z-]{2,80}$")
# サンドボックスの中の python に渡さない環境変数（本線の DB ／ データ ／ 実行の置き場を指してしまうもの）
DROP_ENV = ("AIL_RESEARCH_DB", "AIL_DATA_DIR", "AIL_RUNS_DIR", "CLAUDECODE")

# サンドボックスでも書いてはいけないパス（リポジトリ基準・fnmatch。プラン §2-3 の S6）
FORBID = (
    "experiments/live-trading/*", "experiments/tastytrade-api-sample/*", "dashboard/*",
    "TODO.md", "CLAUDE.md", "DONE.md", "run-*.sh", ".gitignore",
    f"{FD}/config/devloop.toml", f"{FD}/cli/devloop*.py", f"{FD}/cli/sandbox.py", f"{FD}/devloop/*", f"{FD}/systemd/*",
    "docs/*",
)
# docs/ の中で書いてよい例外（案のメモ 1 枚）
DOCS_OK = ("docs/specs/experiments/dev-loop/sandbox/*.md",)
# 回したときに書き換わる生成物（チェックでは数えない・コミットしない）
GENERATED = ("docs/specs/experiments/feature-discovery/ledger.md",)


class SandboxError(RuntimeError):
    pass


def base_dir(base: str | None = None) -> str:
    return os.path.abspath(os.path.expanduser(base or os.environ.get("AIL_SANDBOX_DIR") or "~/.cache/ai-income-lab-sandbox"))


def check_id(id_: str) -> str:
    if not ID_RE.match(id_ or ""):
        raise SandboxError(f"id は英小文字・数字・ハイフンだけ（3〜81 文字）: {id_!r}")
    return id_


def tree(id_: str, base: str | None = None) -> str:
    """ワーキングツリーのルート。"""
    return os.path.join(base_dir(base), check_id(id_))


def fd_dir(id_: str, base: str | None = None) -> str:
    """ワーキングツリーの中の experiments/feature-discovery。"""
    return os.path.join(tree(id_, base), *FD.split("/"))


def db_path(id_: str, base: str | None = None) -> str:
    return os.path.join(fd_dir(id_, base), "runs", "research.sqlite")


def _sh(cmd: list[str], cwd: str, env: dict | None = None, timeout: float | None = 600) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or "") if isinstance(e.stdout, str) else "", f"timeout {timeout}s"
    except OSError as e:
        return 127, "", str(e)


def _git(repo: str, *args: str, timeout: float = 600) -> str:
    rc, out, err = _sh(["git", *args], cwd=repo, timeout=timeout)
    if rc:
        raise SandboxError(f"git {' '.join(args)} が失敗（rc={rc}）: {(err or out).strip()[-300:]}")
    return out


def exists(id_: str, base: str | None = None) -> bool:
    return os.path.isdir(tree(id_, base))


# ---------------------------------------------------------------- 作る・消す


def create(id_: str, *, repo: str = REPO, base: str | None = None, ref: str = "main", data_src: str | None = None,
           db_src: str | None = None, period_files: bool = True, log=print) -> dict:
    """ワーキングツリー ＋ ブランチ `sandbox/<id>` を作り、日足（raw ／ adjusted ／ derived ／ manifests）と研究の DB をコピーする。

    ⚠ 既にあるサンドボックス・ブランチは上書きしない（止まる）。本線のワーキングツリーには何も書かない。
    """
    t, branch = tree(id_, base), BRANCH_PREFIX + check_id(id_)
    if os.path.exists(t):
        raise SandboxError(f"サンドボックスが既にある: {t}（消すなら remove）")
    rc, _out, _err = _sh(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo)
    if rc == 0:
        raise SandboxError(f"ブランチが既にある: {branch}（前の回の結果。消すなら remove --delete-branch）")
    base_commit = _git(repo, "rev-parse", ref).strip()
    os.makedirs(base_dir(base), exist_ok=True)
    _git(repo, "worktree", "add", "-q", "-b", branch, t, base_commit)
    src = data_src or os.path.join(repo, *FD.split("/"), "data")
    dst = os.path.join(fd_dir(id_, base), "data")
    copied = []
    for part in DATA_PARTS:
        s = os.path.join(src, part)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(dst, part), symlinks=False)
            copied.append(part)
    os.makedirs(os.path.join(dst, "features"), exist_ok=True)
    seed = db_src if db_src is not None else os.path.join(repo, *FD.split("/"), "runs", "research.sqlite")
    seeded = copy_db(seed, db_path(id_, base)) if seed and os.path.exists(seed) else False
    periods = copy_period_files(id_, repo=repo, base=base, data_src=src) if seeded and period_files else []
    base_rows = ledger_keys(db_path(id_, base)) if seeded else {}
    with open(_baseline_path(id_, base), "w", encoding="utf-8") as f:
        json.dump(base_rows, f, ensure_ascii=False)
    meta = {"id": id_, "branch": branch, "base_commit": base_commit, "tree": t, "created": _now(), "data_parts": copied,
            "db_seeded": seeded, "base_n_trials": sum(1 for v in base_rows.values() if v["trial"]), "period_files": len(periods)}
    with open(_meta_path(id_, base), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    log(f"サンドボックスを作った: {t}（ブランチ {branch}・元 {base_commit[:7]}・データ {' ／ '.join(copied) or 'なし'}・"
        f"DB {'コピーした（検証 ' + str(meta['base_n_trials']) + ' 行）' if seeded else 'なし'}・期間を読むテーブル {len(periods)} ファイル）")
    return meta


def copy_db(src: str, dst: str) -> bool:
    """本線の DB をサンドボックスへ（SQLite のバックアップ API ＝ 本線が書いている最中でも壊れないコピー。本線は読むだけで開く）。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    a = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    b = sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()
    return True


def ledger_keys(db: str) -> dict:
    """DB の `ledger_rows` を「leak|検証名」→ {判定・上乗せ・検証の行か・件数} にまとめる（サンドボックスの前後の比較に使う）。"""
    out: dict = {}
    if not os.path.exists(db):
        return out
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT leak, trial_name, is_trial, verdict, doc FROM ledger_rows").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    for leak, trial, is_trial, verdict, doc in rows:
        try:
            d = json.loads(doc or "{}")
        except ValueError:
            d = {}
        k = f"{int(bool(leak))}|{trial}"
        e = out.setdefault(k, {"trial": bool(is_trial) and not leak, "n": 0, "verdict": verdict, "excess_bp": d.get("上乗せbp"), "folds": d.get("上乗せfold")})
        e["n"] += 1
    return out


def _baseline_path(id_: str, base: str | None = None) -> str:
    return os.path.join(base_dir(base), f"{check_id(id_)}.baseline.json")


def baseline(id_: str, base: str | None = None) -> dict:
    try:
        with open(_baseline_path(id_, base), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _meta_path(id_: str, base: str | None = None) -> str:
    """作ったときの控え（ワーキングツリーの外 ＝ サンドボックスの git status に出ない）。"""
    return os.path.join(base_dir(base), f"{check_id(id_)}.json")


def meta(id_: str, base: str | None = None) -> dict:
    try:
        with open(_meta_path(id_, base), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def remove(id_: str, *, repo: str = REPO, base: str | None = None, delete_branch: bool = False, log=print) -> None:
    """ワーキングツリー（DB・データごと）を消す。ブランチは既定で残す（diff と履歴を後から見られるように）。"""
    t, branch = tree(id_, base), BRANCH_PREFIX + check_id(id_)
    if os.path.isdir(t):
        rc, out, err = _sh(["git", "worktree", "remove", "--force", t], cwd=repo)
        if rc:                                   # git が知らないディレクトリ（作りかけ）はそのまま消す
            shutil.rmtree(t, ignore_errors=True)
            _sh(["git", "worktree", "prune"], cwd=repo)
    else:
        _sh(["git", "worktree", "prune"], cwd=repo)
    for p in (_meta_path(id_, base), _baseline_path(id_, base)):
        try:
            os.remove(p)
        except OSError:
            pass
    if delete_branch:
        _sh(["git", "branch", "-D", branch], cwd=repo)
    log(f"サンドボックスを消した: {t}" + ("（ブランチも）" if delete_branch else f"（ブランチ {branch} は残した）"))


def list_ids(base: str | None = None) -> list[str]:
    b = base_dir(base)
    if not os.path.isdir(b):
        return []
    return sorted(d for d in os.listdir(b) if os.path.isdir(os.path.join(b, d)) and ID_RE.match(d))


# ---------------------------------------------------------------- テーブル


def tables_for(id_: str, experiments: list[str], base: str | None = None) -> list[str]:
    """実験が読むテーブルの名前（サンドボックスの中の config から。`features_from` があればその実験・無ければ自分 ＋ それぞれの `_leak`）。"""
    names: list[str] = []
    for e in experiments:
        p = os.path.join(fd_dir(id_, base), "config", "experiment", f"{e}.toml")
        try:
            with open(p, "rb") as f:
                doc = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as err:
            raise SandboxError(f"実験の config が読めない: {p}（{err}）") from err
        owner = str(doc.get("features_from") or e)          # config の一番上の項目（`cli/run.py` の `load_panel` と同じ読み方）
        for n in (owner, owner + "_leak"):
            if n not in names:
                names.append(n)
    return names


def copy_tables(id_: str, tables: list[str], *, repo: str = REPO, base: str | None = None, data_src: str | None = None, log=print) -> dict:
    """本線の `data/features/<テーブル>` をサンドボックスへコピーする。本線に無いもの・サンドボックスに既にあるものは飛ばす。"""
    src = os.path.join(data_src or os.path.join(repo, *FD.split("/"), "data"), "features")
    dst = os.path.join(fd_dir(id_, base), "data", "features")
    out = {"copied": [], "missing": [], "kept": []}
    for t in tables:
        if not re.match(r"^[A-Za-z0-9_.-]+$", t):
            raise SandboxError(f"テーブル名が不正: {t!r}")
        s, d = os.path.join(src, t), os.path.join(dst, t)
        if os.path.exists(d):
            out["kept"].append(t)
        elif os.path.isdir(s):
            shutil.copytree(s, d, symlinks=False)
            out["copied"].append(t)
        else:
            out["missing"].append(t)
    log(f"テーブル: コピー {len(out['copied'])}（{', '.join(out['copied']) or '—'}）／ 本線に無い {len(out['missing'])}（{', '.join(out['missing']) or '—'}）")
    return out


# 古い実行（期間を記録していない実行）が指しているテーブルのファイルを、サンドボックスの DB から列挙する（サンドボックスの中で動かす）
_PERIOD_SNIPPET = (
    "import json\n"
    "from ail import runs\n"
    "out = set()\n"
    "for name in runs.list_runs():\n"
    "    inp = runs.read_json(name, 'inputs.json') or {}\n"
    "    meta = inp.get('features_meta') or {}\n"
    "    rel = inp.get('features_file')\n"
    "    if rel and not (inp.get('panel_start') or meta.get('start')):\n"
    "        out.add(str(rel))\n"
    "print('PERIOD_FILES=' + json.dumps(sorted(out)))\n"
)


def copy_period_files(id_: str, *, repo: str = REPO, base: str | None = None, data_src: str | None = None, log=print) -> list[str]:
    """検証結果一覧が「期間」を読むテーブルのファイル（古い実行のぶん）を本線からコピーする。返すのはコピーした相対パス。"""
    rc, out, err = run(id_, ["-c", _PERIOD_SNIPPET], repo=repo, base=base, timeout=600)
    line = next((l for l in out.splitlines() if l.startswith("PERIOD_FILES=")), None)
    if rc or line is None:
        raise SandboxError(f"期間を読むテーブルを列挙できない（rc={rc}）: {(err or out).strip()[-300:]}")
    src_root = os.path.dirname(data_src or os.path.join(repo, *FD.split("/"), "data"))      # experiments/feature-discovery
    done = []
    for rel in json.loads(line[len("PERIOD_FILES="):]):
        rel = os.path.normpath(rel)
        if not rel.startswith("data" + os.sep + "features" + os.sep) or ".." in rel.split(os.sep):
            continue                                       # data/features の下だけ（data-widen など別の置き場の実行は期間を記録している）
        s_, d_ = os.path.join(src_root, rel), os.path.join(fd_dir(id_, base), rel)
        if os.path.isfile(s_) and not os.path.exists(d_):
            os.makedirs(os.path.dirname(d_), exist_ok=True)
            shutil.copy2(s_, d_)
            done.append(rel)
    return done


# ---------------------------------------------------------------- 実行


def python_bin(repo: str = REPO) -> str:
    """本線の venv の python（サンドボックスに venv は作らない。cwd がワーキングツリーなので、読むコードはサンドボックスのもの）。"""
    py = os.path.join(repo, *FD.split("/"), ".venv", "bin", "python")
    return py if os.path.exists(py) else sys.executable


def sandbox_env(id_: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    env["AIL_SANDBOX"] = id_
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run(id_: str, args: list[str], *, repo: str = REPO, base: str | None = None, timeout: float | None = 3600) -> tuple[int, str, str]:
    """サンドボックスの `experiments/feature-discovery` を cwd にして python を起動する（例 args = ["-m", "cli.queue", "--config", "x"]）。"""
    cwd = fd_dir(id_, base)
    if not os.path.isdir(cwd):
        raise SandboxError(f"サンドボックスが無い: {cwd}")
    return _sh([python_bin(repo), *args], cwd=cwd, env=sandbox_env(id_), timeout=timeout)


# ---------------------------------------------------------------- チェック（main との diff）


def _match(path: str, patterns) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def changes(id_: str, base: str | None = None) -> list[tuple[str, str]]:
    """作ったときの元のコミットから変わったファイル（コミット済み ＋ 未コミット ＋ 未追跡）。返すのは (状態, リポジトリ基準のパス)。
    状態 ＝ A 追加 ／ M 変更 ／ D 削除。生成物（`GENERATED`）は数えない。"""
    t = tree(id_, base)
    base_commit = meta(id_, base).get("base_commit") or "main"
    seen: dict[str, str] = {}
    for line in _git(t, "diff", "--name-status", "--no-renames", base_commit).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            seen[parts[-1]] = parts[0][:1]
    for line in _git(t, "ls-files", "--others", "--exclude-standard").splitlines():
        if line.strip():
            seen[line.strip()] = "A"
    return sorted((st, p) for p, st in seen.items() if not _match(p, GENERATED))


def check(id_: str, base: str | None = None) -> dict:
    """サンドボックスの変更が決まり（プラン §2-3 の S6・§2-4）を守っているか。

    NG になるもの: 禁止パスへの変更 ／ `experiments/feature-discovery/` の外への変更（案のメモを除く）／ **既存のテストの変更・削除**
    （守りのテストを弱められないように。追加だけ可）。
    """
    ch = changes(id_, base)
    forbidden, tests_touched, outside = [], [], []
    for st, p in ch:
        if _match(p, DOCS_OK):
            continue
        if _match(p, FORBID):
            forbidden.append(p)
        elif not p.startswith(FD + "/"):
            outside.append(p)
        elif p.startswith(f"{FD}/tests/") and st != "A":
            tests_touched.append(p)
    return {"ok": not (forbidden or tests_touched or outside), "changed": [p for _st, p in ch],
            "forbidden": forbidden, "tests_touched": tests_touched, "outside": outside}


def commit(id_: str, message: str, base: str | None = None) -> str:
    """サンドボックスのブランチにコミットする（回す前 ＝ 回したコードがブランチに残る）。生成物は入れない。変更が無ければ空文字。"""
    t = tree(id_, base)
    _git(t, "add", "-A")
    for g in GENERATED:
        _sh(["git", "reset", "-q", "--", g], cwd=t)
    rc, _out, _err = _sh(["git", "diff", "--cached", "--quiet"], cwd=t)
    if rc == 0:
        return ""
    _git(t, "commit", "-q", "-m", message)
    return _git(t, "rev-parse", "--short", "HEAD").strip()


# ---------------------------------------------------------------- 要約


def report(id_: str, base: str | None = None) -> dict:
    """サンドボックスで増えた行の要約（作ったときの DB の `ledger_rows` との差）。⚠ 「参考」（検証結果一覧には載せない・採否に使わない ＝ プラン S2）。

    - `n_trials` ＝ 新しく増えた、数える行（leak でない・検証の行）の数 ＝ サンドボックスの検証数（プラン S1）
    - `changed` ＝ 前からあった行の判定か上乗せが変わったもの。⚠ **1 行でもあれば、共有の経路の変更が既存の検証を動かした疑い**
      （「オプションの無い config は 1 ビットも変わらない」が破れている）
    - `gone` ＝ 前からあった行が消えたもの（識別項目の作り方が変わって行がまとまった ／ 割れた疑い）
    """
    p = db_path(id_, base)
    out: dict = {"id": id_, "db": p, "n_trials": 0, "verdicts": {}, "rows": [], "new_leak_rows": 0, "changed": [], "gone": []}
    if not os.path.exists(p):
        out["error"] = "サンドボックスの DB が無い"
        return out
    before, after = baseline(id_, base), ledger_keys(p)
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        docs = {f"{int(bool(leak))}|{trial}": (model, doc) for leak, trial, model, doc in
                conn.execute("SELECT leak, trial_name, model_name, doc FROM ledger_rows").fetchall()}
    except sqlite3.Error as e:
        out["error"] = f"ledger_rows が読めない: {e}"
        return out
    finally:
        conn.close()
    for k, e in sorted(after.items()):
        old = before.get(k)
        if old is None:
            if k.startswith("1|"):
                out["new_leak_rows"] += 1
                continue
            if not e["trial"]:
                continue                                     # 基準線の行は数えない
            model, doc = docs.get(k, ("", "{}"))
            try:
                d = json.loads(doc or "{}")
            except ValueError:
                d = {}
            out["n_trials"] += 1
            out["verdicts"][e["verdict"]] = out["verdicts"].get(e["verdict"], 0) + 1
            out["rows"].append({"trial": k[2:], "model": model, "verdict": e["verdict"], "method": d.get("手法名"), "theta": d.get("閾値"),
                                "style": d.get("検証方式"), "excess_bp": e["excess_bp"], "folds": e["folds"]})
        elif (old.get("verdict"), old.get("excess_bp"), old.get("folds"), old.get("n")) != (e["verdict"], e["excess_bp"], e["folds"], e["n"]):
            out["changed"].append({"key": k, "before": [old.get("verdict"), old.get("excess_bp"), old.get("folds"), old.get("n")],
                                   "after": [e["verdict"], e["excess_bp"], e["folds"], e["n"]]})
    out["gone"] = sorted(k for k in before if k not in after)
    out["base_n_trials"] = sum(1 for v in before.values() if v.get("trial"))
    # leak 対照（rules.md 31-7 の 8 ③）: 新しい検証の行ごとに、同じ検証名の leak の行があり、上乗せが正で元の行より大きければ「跳ねた」。
    # ⚠ 跳ねない ／ 無い行は配線の誤りを疑うフラグ（判定は変えない）。`cli.queue` は leak の実行の後に一覧を再生成しないので、
    #    呼ぶ側が先に `cli.report --catalog` で再生成しておく（しないと最後の leak の行が無いままになる）
    out["leak_ok"], out["leak_missing"] = 0, []
    for r in out["rows"]:
        lk = after.get("1|" + r["trial"])
        a, b = (lk or {}).get("excess_bp"), r.get("excess_bp")
        if lk is not None and isinstance(a, (int, float)) and a > 0 and (not isinstance(b, (int, float)) or a > b):
            out["leak_ok"] += 1
        else:
            out["leak_missing"].append(r["trial"])
    return out


# ---------------------------------------------------------------- CLI


# ---------------------------------------------------------------- 記録（rules.md 31-7 の 5 ＝ 結果は人の確認なしで自動で記録する）

RESULT_DIR = "docs/specs/experiments/dev-loop/sandbox"      # 案ごとの結果の表（リポジトリの中・main にコミットする）


def result_md(doc: dict, rep: dict, m: dict, commit_sha: str, recorded: str, note: str = "") -> str:
    """案ごとの結果の表（Markdown）。数字は `report` のまま（手で書かない）。"""
    invalid = bool(rep.get("changed") or rep.get("gone"))
    counts = "・".join(f"{k} {v}" for k, v in sorted((rep.get("verdicts") or {}).items())) or "—"
    L = [f"# サンドボックスの結果: `{doc.get('id')}` {doc.get('title')}", "",
         "⚠ **このファイルは生成物である**（`cli.sandbox record`。rules.md 31-7）。コードは `main` に入っていない ＝ ブランチ "
         f"`{m.get('branch')}`（コミット `{commit_sha or '—'}`・元 `{str(m.get('base_commit') or '')[:7]}`）。"
         "`ledger.md` と選ぶステージには入らない。採用には 取り込み（人のレビュー）→ `main` で回し直す が要る。", "",
         "| 項目 | 値 |", "| --- | --- |",
         f"| 着手（回すと決めた時刻） | {m.get('created') or '—'} |", f"| 記録 | {recorded} |",
         f"| サンドボックスの検証数 | {rep.get('n_trials', 0)}（予定 {doc.get('n_trials_planned')}） |",
         f"| 判定の内訳 | {counts} |",
         f"| leak 対照 | 新しい検証 {rep.get('n_trials', 0)} 行のうち、同じ検証名の leak の行が跳ねたもの {rep.get('leak_ok', 0)}"
         + ("　⚠ **跳ねていない ／ 無い行がある ＝ 配線の誤りを疑う**: " + "・".join(f"`{x}`" for x in (rep.get("leak_missing") or [])[:6]) if rep.get("leak_missing") else "（全部跳ねた）")
         + f"（新しい leak の行 {rep.get('new_leak_rows', 0)}） |",
         f"| 既存の行への影響 | 変わった {len(rep.get('changed') or [])} ／ 消えた {len(rep.get('gone') or [])}"
         + ("　⚠ **無効**（共有コードが既存の結果を変えた ＝ 判定を出さない。31-7 の 8）" if invalid else "（0 ＝ 既存の結果は変わっていない）") + " |", "",
         f"- 問い: {doc.get('question')}", f"- 構造（先に書いた）: {doc.get('structure')}", f"- 予想（先に書いた）: {doc.get('prediction')}", ""]
    if rep.get("rows"):
        L += ["| 検証名 | 検証方式 | 手法 | θ | 上乗せ bp/fold | fold の符号 | 判定 |", "| --- | --- | --- | ---: | ---: | --- | --- |"]
        for r in rep["rows"]:
            ex = r.get("excess_bp")
            L.append(f"| `{r.get('trial')}` | {r.get('style')} | {r.get('method')} | {r.get('theta')} | "
                     f"{ex:+.1f} | {r.get('folds')} | {'無効' if invalid else r.get('verdict')} |" if isinstance(ex, (int, float)) else
                     f"| `{r.get('trial')}` | {r.get('style')} | {r.get('method')} | {r.get('theta')} | — | {r.get('folds')} | {'無効' if invalid else r.get('verdict')} |")
        L.append("")
    if invalid:
        L += ["変わった ／ 消えた既存の行（先頭 10）:", ""] + [f"- `{x.get('key') if isinstance(x, dict) else x}`" for x in (list(rep.get("changed") or []) + list(rep.get("gone") or []))[:10]] + [""]
    if note.strip():
        L += ["## メモ", "", note.strip(), ""]
    return "\n".join(L)


def record(id_: str, *, repo: str = REPO, base: str | None = None, ideas_dir: str | None = None, note: str = "", push: bool = False, log=print) -> dict:
    """サンドボックスの結果を記録する: ① コードをブランチ `sandbox/<id>` にコミット（`push` なら origin へ）② 結果の表を
    `docs/specs/experiments/dev-loop/sandbox/<id>.md` に書く ③ レジストリを「回した」・`ran_in = "sandbox"`・`[sandbox]` に。

    ⚠ `main` のコード・`main` の DB・`ledger.md` には触らない（書くのは ② の表と ③ のレジストリの 1 ファイルだけ。コミットは呼んだ側）。
    ⚠ 既存の行が変わった ／ 消えた案は「無効」（判定を出さない・回したぶんは数える ＝ rules.md 31-7 の 4・8）。
    """
    from cli import devloop
    rep, m = report(id_, base), meta(id_, base)
    if rep.get("error"):
        raise SandboxError(rep["error"])
    ideas_dir = ideas_dir or os.path.join(repo, FD, "config", "ideas")
    path = os.path.join(ideas_dir, f"{id_}.toml")
    if not os.path.exists(path):
        raise SandboxError(f"レジストリに {id_} が無い: {path}")
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    doc["status"] = devloop.OLD_STATUS.get(doc.get("status"), doc.get("status"))
    if doc["status"] not in (devloop.SHARED, "回した") or (doc["status"] == "回した" and doc.get("ran_in") != "sandbox"):
        raise SandboxError(f"{id_}: 記録できるのはステータス「{devloop.SHARED}」の案だけ（いま {doc['status']}）")
    sha = commit(id_, f"sandbox: {id_}（開発ループのサンドボックス。main には入れない）", base)
    sha = sha or _git(tree(id_, base), "rev-parse", "--short", "HEAD").strip()
    if push:
        _git(tree(id_, base), "push", "-q", "-u", "origin", m.get("branch") or BRANCH_PREFIX + id_)
    recorded = _now()
    invalid = bool(rep.get("changed") or rep.get("gone"))
    verdicts = {} if invalid else dict(rep.get("verdicts") or {})
    held = bool(verdicts.get("採る") or verdicts.get("保留"))
    out_dir = os.path.join(repo, RESULT_DIR)
    os.makedirs(out_dir, exist_ok=True)
    md = os.path.join(out_dir, f"{id_}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(result_md(doc, rep, m, sha, recorded, note))
    doc["status"], doc["ran_in"] = "回した", "sandbox"
    doc.setdefault("registered_at", m.get("created") or recorded)      # 回すと決めた時刻 ＝ サンドボックスに着手した時刻（31-7 の 3）
    doc["sandbox"] = {"branch": m.get("branch") or BRANCH_PREFIX + id_, "commit": sha, "pushed": bool(push), "started_at": m.get("created") or "",
                      "recorded_at": recorded, "n_trials": int(rep.get("n_trials", 0)),
                      "verdicts": "・".join(f"{k} {v}" for k, v in sorted(verdicts.items())) or ("無効" if invalid else "—"),
                      "held": held, "invalid": invalid, "leak_rows": int(rep.get("new_leak_rows", 0)), "leak_ok": int(rep.get("leak_ok", 0)),
                      "result": f"{RESULT_DIR}/{id_}.md"}
    devloop.write_idea(path, doc)
    log(f"記録した: {id_} ＝ サンドボックスの検証数 {doc['sandbox']['n_trials']}・{doc['sandbox']['verdicts']}"
        + ("・⚠ 保留以上あり ＝ 人が見る" if held else "") + f"・ブランチ {doc['sandbox']['branch']} @ {sha}" + ("（push 済み）" if push else "") + f"・{md}")
    return {"id": id_, "md": md, "idea": path, "leak_missing": list(rep.get("leak_missing") or []), **doc["sandbox"]}


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="開発ループのサンドボックス（docs/plans/devloop-sandbox.md）")
    ap.add_argument("--base", help="サンドボックスの親ディレクトリ（既定 ~/.cache/ai-income-lab-sandbox ／ AIL_SANDBOX_DIR）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("id"); c.add_argument("--ref", default="main")
    t = sub.add_parser("tables"); t.add_argument("id"); t.add_argument("experiments", nargs="+")
    e = sub.add_parser("exec"); e.add_argument("id"); e.add_argument("--timeout", type=float, default=3600); e.add_argument("args", nargs=argparse.REMAINDER)
    k = sub.add_parser("check"); k.add_argument("id")
    r = sub.add_parser("report"); r.add_argument("id")
    d = sub.add_parser("remove"); d.add_argument("id"); d.add_argument("--delete-branch", action="store_true")
    w = sub.add_parser("record"); w.add_argument("id"); w.add_argument("--note", default=""); w.add_argument("--note-file"); w.add_argument("--push", action="store_true")
    sub.add_parser("list")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    try:
        if a.cmd == "create":
            create(a.id, base=a.base, ref=a.ref)
        elif a.cmd == "tables":
            copy_tables(a.id, tables_for(a.id, a.experiments, a.base), base=a.base)
        elif a.cmd == "exec":
            args = a.args[1:] if a.args[:1] == ["--"] else a.args
            rc, out, err = run(a.id, args, base=a.base, timeout=a.timeout)
            sys.stdout.write(out)
            sys.stderr.write(err)
            return rc
        elif a.cmd == "check":
            res = check(a.id, a.base)
            print(json.dumps(res, ensure_ascii=False, indent=1))
            return 0 if res["ok"] else 1
        elif a.cmd == "report":
            print(json.dumps(report(a.id, a.base), ensure_ascii=False, indent=1))
        elif a.cmd == "record":
            note = a.note
            if a.note_file:
                with open(a.note_file, encoding="utf-8") as f:
                    note = f.read()
            record(a.id, base=a.base, note=note, push=a.push)
        elif a.cmd == "remove":
            remove(a.id, base=a.base, delete_branch=a.delete_branch)
        elif a.cmd == "list":
            for i in list_ids(a.base):
                m = meta(i, a.base)
                print(f"{i}\t{m.get('branch', '?')}\t{m.get('created', '?')}")
    except SandboxError as e:
        print(f"⚠ {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
