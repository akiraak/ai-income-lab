#!/usr/bin/env python3
"""実売買・API 検証・管理画面・シミュレーションの記録の置き場（SQLite）。2026-09-21（プラン db-model-facts.md §11）。

⚠ **記録の道の決め方はいままでと同じ**。記録のファイルの道（`out/2026-09-22/orders.jsonl`・`state/prod/T1.json` …）を
そのまま鍵にして、道の近くにある DB ファイルに入れる:

  - 道から親へたどり、最初に見つかった `live.sqlite` ／ `sim.sqlite` ／ `demo.sqlite` に入る
    （鍵 ＝ その DB のあるディレクトリからの相対の道）。シミュレーションの木・テストの一時置き場は自分の DB を持つ
  - 本物 ＝ リポジトリ直下の `live.sqlite`（執行器 ＋ API 検証 ＋ 管理画面で 1 つ ＝ 2026-09-21 の利用者の裁定）。
    ⚠ **本物に入れてよいのは `REAL_PLACES` の下だけ**。ほかの道（DB を作り忘れたシミュレーションの木・デモ）は止まる
  - ⚠ どの DB にも当たらない道は止まる（`LiveFsError`）＝ テストが本物に書く事故は起きない

表:
  lines         いままでの `*.jsonl`・`*.log`（1 行 1 行・元の文字のまま。⚠ 足すだけ ＝ 書き換えも削除もトリガーが拒む）
  docs          丸ごと書き換えるもの（売買履歴 `state/<env>/<名前>.json`・紙上の対照 `daily.csv` など）
  docs_history  docs を書き換える・消す前の中身（トリガーが残す）

⚠ **入れないもの**: `HALT`・`MODE`・`run.lock`・シミュレーションの `control.json`（ファイルの有無で止める・切り替える
仕組み）・入力データ・設定・プロセスの画面の写し。これらは今までどおりファイル。
⚠ 標準ライブラリだけ（執行器・管理画面・vibeboard の sidecar のどこからでも同じ形で読める）。

    python3 livefs.py stats                          # 本物の DB の中身の数
    python3 livefs.py ls <道> ／ cat <道>             # 記録を見る（cat は元のファイルと同じ文字）
    python3 livefs.py dump <DB のあるディレクトリ>    # 全部の行と docs（秘密の grep 用）
    python3 livefs.py append <道> < 行               # 標準入力の行を足す（run-live.sh の predict.jsonl）
    python3 livefs.py import <ディレクトリ>...        # いままでのファイルを取り込む（入っているものは突き合わせてとばす）
    python3 livefs.py verify <ディレクトリ>...        # ファイルと DB を 1 ビットずつ突き合わせる
    python3 livefs.py remove --i-verified <ディレクトリ>...   # 突き合わせて一致したファイルだけ消す（⚠ 利用者の了承のあと）
    python3 livefs.py backup --db <DB> --to <道>     # 控え（書いている最中でも壊れない写し）
    python3 livefs.py export <道> --to <置き場>       # 道の下の記録を、いままでと同じファイルの形で書き出す（控え・読むための写し）
    python3 livefs.py init <ディレクトリ> live|sim|demo   # そのディレクトリに DB を作る（シミュレーションの木・テスト）
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
DB_NAMES = ("live.sqlite", "sim.sqlite", "demo.sqlite")
REAL_DB = os.path.join(REPO_ROOT, "live.sqlite")
# ⚠ 本物の DB に入れてよい置き場（リポジトリ直下からの道）。ほかは止まる
REAL_PLACES = ("experiments/live-trading/out", "experiments/live-trading/state", "experiments/live-trading/mode.log",
               "experiments/tastytrade-api-sample/out", "dashboard/data")
REAL_EXCLUDED = ("dashboard/data/demo",)
# いままでのファイルのうち、1 行ずつの記録として入れるもの（ほかの文字のファイルは丸ごと docs）
LINE_SUFFIXES = (".jsonl", ".log")
# ⚠ 取り込まない（ファイルのまま）: 止める・切り替える・排他の仕組み・シミュレーションの制御と入力
NOT_RECORDS = {"HALT", "MODE", "run.lock", "control.json", "status.json", "data.json", "env.empty"} | set(DB_NAMES)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lines (
  path  TEXT NOT NULL,                   -- DB のあるディレクトリからの道（`out/2026-09-22/orders.jsonl`）
  seq   INTEGER NOT NULL,                -- その道の中の行の順（1 から）
  text  TEXT NOT NULL,                   -- 行（改行なし・元の文字のまま）
  at    TEXT NOT NULL,                   -- DB に入った時刻（UTC）
  PRIMARY KEY (path, seq)
);
CREATE TABLE IF NOT EXISTS docs (
  path        TEXT PRIMARY KEY,
  body        TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS docs_history (
  id          INTEGER PRIMARY KEY,
  path        TEXT NOT NULL,
  body        TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  replaced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS docs_history_path ON docs_history (path);
CREATE TRIGGER IF NOT EXISTS lines_frozen_update BEFORE UPDATE ON lines
  BEGIN SELECT RAISE(ABORT, '記録の行は書き換えない（足すだけ）'); END;
CREATE TRIGGER IF NOT EXISTS lines_frozen_delete BEFORE DELETE ON lines
  BEGIN SELECT RAISE(ABORT, '記録の行は消さない（足すだけ）'); END;
CREATE TRIGGER IF NOT EXISTS docs_keep_update BEFORE UPDATE ON docs
  BEGIN INSERT INTO docs_history (path, body, updated_at, replaced_at)
        VALUES (OLD.path, OLD.body, OLD.updated_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')); END;
CREATE TRIGGER IF NOT EXISTS docs_keep_delete BEFORE DELETE ON docs
  BEGIN INSERT INTO docs_history (path, body, updated_at, replaced_at)
        VALUES (OLD.path, OLD.body, OLD.updated_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')); END;
"""


class LiveFsError(RuntimeError):
    pass


_CONN: dict[str, sqlite3.Connection] = {}
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _rel(path: str, base: str) -> str:
    rel = os.path.relpath(path, base).replace(os.sep, "/")
    return "" if rel == "." else rel


def _under(rel: str, place: str) -> bool:
    return rel == place or rel.startswith(place + "/")


def locate(path) -> tuple[str, str]:
    """記録の道 → (DB ファイル, 鍵)。⚠ 当たる DB が無ければ止まる（本物に落ちない）。"""
    p = os.path.abspath(os.fspath(path))
    d = p
    while True:
        for name in DB_NAMES:
            db = os.path.join(d, name)
            if os.path.isfile(db):
                key = _rel(p, d)
                if db == REAL_DB:
                    _check_real(key, p)
                return db, key
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    key = _rel(p, REPO_ROOT)
    if not key.startswith(".."):
        _check_real(key, p)            # 本物の DB がまだ無い（最初の 1 回）＝ 決まった置き場なら作る
        return REAL_DB, key
    raise LiveFsError(f"記録の置き場の DB が無い: {p}（シミュレーション・テストは `livefs.init(<置き場>, ...)` で先に作る）")


def _check_real(key: str, p: str) -> None:
    if not any(_under(key, x) for x in REAL_PLACES) or any(_under(key, x) for x in REAL_EXCLUDED):
        raise LiveFsError(f"本物の DB（{REAL_DB}）に入れてよい置き場ではない: {p}"
                          "（シミュレーションの木・デモは自分の DB を先に作る）")


def connect(db: str) -> sqlite3.Connection:
    """DB ファイルの接続（プロセスに 1 本。スレッドからも使う ＝ `_LOCK` で順に）。無ければ作る。"""
    with _LOCK:
        conn = _CONN.get(db)
        if conn is None:
            os.makedirs(os.path.dirname(db), exist_ok=True)
            conn = sqlite3.connect(db, timeout=30, check_same_thread=False, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")       # 書いている間も読み手（管理画面）を止めない
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', '1')")
            _CONN[db] = conn
        return conn


def forget(db: str | None = None) -> None:
    """使い回しの接続を閉じる（テストが置き場を消したとき・fork の前）。"""
    with _LOCK:
        for k in ([db] if db else list(_CONN)):
            if (c := _CONN.pop(k, None)) is not None:
                c.close()


def init(dirpath, kind: str) -> str:
    """`<dirpath>/<kind>.sqlite` を作る（シミュレーションの木・テストの一時置き場・デモ）。できた DB の道。"""
    if f"{kind}.sqlite" not in DB_NAMES:
        raise ValueError(f"知らない DB の種類: {kind}")
    db = os.path.join(os.path.abspath(os.fspath(dirpath)), f"{kind}.sqlite")
    connect(db)
    return db


def db_of(path) -> str:
    return locate(path)[0]


# --- 行（いままでの *.jsonl・*.log）------------------------------------

def append(path, text: str) -> None:
    """1 行足す（改行は付けない）。⚠ 行の中に改行を入れない。"""
    append_many(path, [text])


def append_many(path, texts) -> None:
    texts = [str(t) for t in texts]
    if any("\n" in t for t in texts):
        raise ValueError("行の中に改行がある")
    if not texts:
        return
    db, key = locate(path)
    with _LOCK:
        conn = connect(db)
        conn.execute("BEGIN IMMEDIATE")
        try:
            (last,) = conn.execute("SELECT coalesce(max(seq), 0) FROM lines WHERE path = ?", (key,)).fetchone()
            at = _now()
            conn.executemany("INSERT INTO lines (path, seq, text, at) VALUES (?, ?, ?, ?)",
                             [(key, last + i, t, at) for i, t in enumerate(texts, 1)])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def _locate_for_read(path, missing_ok: bool) -> tuple[str, str] | None:
    """読むときの道 → (DB, 鍵)。⚠ **どの DB にも当たらない道は止まる**（設定の誤りで「何も持っていない」と読まない ＝
    執行器が同じ株を買い直さない）。`missing_ok=True`（管理画面）なら None。"""
    try:
        return locate(path)
    except LiveFsError:
        if missing_ok:
            return None
        raise


def read_lines(path, missing_ok: bool = False) -> list[str]:
    """行の並び（まだ書いていなければ空）。"""
    hit = _locate_for_read(path, missing_ok)
    if hit is None:
        return []
    db, key = hit
    if not os.path.isfile(db):
        return []
    with _LOCK:
        return [t for (t,) in connect(db).execute("SELECT text FROM lines WHERE path = ? ORDER BY seq", (key,))]


# --- 丸ごと（売買履歴・daily.csv など）----------------------------------

def write_doc(path, body: str) -> None:
    """丸ごと書く（前の中身は docs_history に残る）。1 つのトランザクション ＝ 途中で落ちても前のまま。"""
    db, key = locate(path)
    with _LOCK:
        connect(db).execute("INSERT INTO docs (path, body, updated_at) VALUES (?, ?, ?) ON CONFLICT (path) DO UPDATE"
                            " SET body = excluded.body, updated_at = excluded.updated_at", (key, str(body), _now()))


def read_doc(path, missing_ok: bool = False) -> str | None:
    hit = _locate_for_read(path, missing_ok)
    if hit is None:
        return None
    db, key = hit
    if not os.path.isfile(db):
        return None
    with _LOCK:
        row = connect(db).execute("SELECT body FROM docs WHERE path = ?", (key,)).fetchone()
    return None if row is None else row[0]


def remove_doc(path) -> bool:
    db, key = locate(path)
    with _LOCK:
        return connect(db).execute("DELETE FROM docs WHERE path = ?", (key,)).rowcount > 0


# --- 道として見る -----------------------------------------------------

def read_text(path, missing_ok: bool = False) -> str | None:
    """いままでのファイルと同じ文字（行なら改行でつないで最後に改行・丸ごとならそのまま）。無ければ None。"""
    doc = read_doc(path, missing_ok)
    if doc is not None:
        return doc
    lines = read_lines(path, missing_ok)
    return "".join(t + "\n" for t in lines) if lines else None


def exists(path, missing_ok: bool = False) -> bool:
    return read_doc(path, missing_ok) is not None or bool(read_lines(path, missing_ok))


def _keys_under(dirpath, missing_ok: bool = True) -> tuple[str, str, list[str]]:
    """(DB, 鍵の頭, 頭より下の全部の鍵)。"""
    hit = _locate_for_read(dirpath, missing_ok)
    if hit is None:
        return "", "", []
    db, key = hit
    if not os.path.isfile(db):
        return db, key, []
    head = key + "/" if key else ""
    with _LOCK:
        conn = connect(db)
        keys = [k for (k,) in conn.execute(
            "SELECT DISTINCT path FROM lines WHERE substr(path, 1, ?) = ? UNION SELECT path FROM docs WHERE substr(path, 1, ?) = ?",
            (len(head), head, len(head), head))]
    return db, head, sorted(keys)


def isdir(path, missing_ok: bool = False) -> bool:
    return bool(_keys_under(path, missing_ok)[2])


def listdir(dirpath, missing_ok: bool = False) -> list[str]:
    """すぐ下の名前（ファイルとディレクトリ）。"""
    _db, head, keys = _keys_under(dirpath, missing_ok)
    return sorted({k[len(head):].split("/", 1)[0] for k in keys})


def stamp(dirpath, missing_ok: bool = True) -> tuple:
    """その下の行の指紋（道・行数）。⚠ 行は足すだけ（書き換え・消しは trigger が止める）なので、行数が同じなら中身も同じ。
    管理画面が過ぎた日を覚えておくのに使う（丸ごとの docs は見ない）。"""
    hit = _locate_for_read(dirpath, missing_ok)
    if hit is None:
        return ()
    db, key = hit
    if not os.path.isfile(db):
        return ()
    head = key + "/" if key else ""
    with _LOCK:
        return tuple(connect(db).execute(
            "SELECT path, max(seq) FROM lines WHERE substr(path, 1, ?) = ? GROUP BY path ORDER BY path", (len(head), head)))


def find(dirpath, pattern: str, missing_ok: bool = False) -> list[str]:
    """`dirpath` の下で `pattern`（`*/positions.jsonl`・`*.json` など。`/` で区切った段ごとに fnmatch）に合う道。"""
    _db, head, keys = _keys_under(dirpath, missing_ok)
    want = pattern.split("/")
    base = os.path.abspath(os.fspath(dirpath))
    out = []
    for k in keys:
        parts = k[len(head):].split("/")
        if len(parts) == len(want) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(parts, want)):
            out.append(os.path.join(base, *parts))
    return sorted(out)


# --- いままでのファイルの取り込み・突き合わせ -------------------------

def _files(root: str) -> list[str]:
    out = []
    for d, _dirs, fs in os.walk(root):
        for f in fs:
            if f in NOT_RECORDS or f.endswith(".tmp") or f.endswith((".sqlite-wal", ".sqlite-shm")):
                continue
            out.append(os.path.join(d, f))
    return sorted(out)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def import_file(fs_path: str) -> str:
    """ファイル 1 つを取り込む。'added' ／ 'same'（もう同じ中身が入っている）。⚠ 違う中身が入っていれば止まる。"""
    with open(fs_path, "rb") as f:
        data = f.read()
    text = data.decode("utf-8")
    if fs_path.endswith(LINE_SUFFIXES):
        if text and not text.endswith("\n"):
            raise LiveFsError(f"終わりに改行が無い（途中で切れた書き込み？）: {fs_path}")
        have = read_lines(fs_path)
        new = text.split("\n")[:-1] if text else []
        if have:
            if have == new:
                return "same"
            raise LiveFsError(f"DB に違う中身で入っている: {fs_path}")
        append_many(fs_path, new)
        return "added"
    have = read_doc(fs_path)
    if have is not None:
        if have == text:
            return "same"
        raise LiveFsError(f"DB に違う中身で入っている: {fs_path}")
    write_doc(fs_path, text)
    return "added"


def verify_file(fs_path: str) -> str | None:
    """食い違いの理由（一致なら None）。⚠ DB から作り直した文字の指紋とディスクの指紋を比べる。"""
    with open(fs_path, "rb") as f:
        want = _sha(f.read())
    got = read_text(fs_path)
    if got is None:
        return "DB に無い"
    if _sha(got.encode("utf-8")) != want:
        return "中身が違う"
    return None


def cmd_import(dirs: list[str]) -> int:
    added = same = 0
    for root in dirs:
        for p in _files(root):
            r = import_file(p)
            added += r == "added"
            same += r == "same"
    print(f"取り込んだ {added} ／ もう同じ中身が入っていた {same}")
    return 0


def _verify(dirs: list[str]) -> tuple[list[str], list[str]]:
    ok, bad = [], []
    for root in dirs:
        for p in _files(root):
            why = verify_file(p)
            (bad.append(f"{why}: {p}") if why else ok.append(p))
    return ok, bad


def cmd_verify(dirs: list[str]) -> int:
    ok, bad = _verify(dirs)
    for b in bad:
        print("  ⚠", b)
    print(f"一致 {len(ok)} ／ 食い違い {len(bad)}")
    return 0 if not bad else 1


def cmd_remove(dirs: list[str], confirmed: bool) -> int:
    if not confirmed:
        raise SystemExit("⚠ 消すと戻せない（git 管理外）。`verify` の結果を見てから --i-verified を付ける")
    ok, bad = _verify(dirs)
    for p in ok:
        os.remove(p)
    for root in dirs:
        for d, _dirs, _fs in sorted(os.walk(root), key=lambda x: -len(x[0])):
            if d != root and not os.listdir(d):
                os.rmdir(d)
    print(f"消したファイル {len(ok)} ／ ⚠ 残した（食い違い） {len(bad)}")
    return 0 if not bad else 1


def cmd_stats(db: str) -> int:
    if not os.path.isfile(db):
        print(f"DB が無い: {db}")
        return 1
    conn = connect(db)
    n_paths, n_lines = conn.execute("SELECT count(DISTINCT path), count(*) FROM lines").fetchone()
    n_docs = conn.execute("SELECT count(*) FROM docs").fetchone()[0]
    n_hist = conn.execute("SELECT count(*) FROM docs_history").fetchone()[0]
    print(f"DB {db}（{os.path.getsize(db) / 1e6:,.2f} MB）")
    print(f"  行の記録 {n_paths:,} 本・{n_lines:,} 行 ／ 丸ごと {n_docs:,}（書き換えの前の中身 {n_hist:,}）")
    return 0


def cmd_dump(dirpath: str) -> int:
    db = None
    for name in DB_NAMES:
        if os.path.isfile(os.path.join(dirpath, name)):
            db = os.path.join(dirpath, name)
    if db is None:
        print(f"DB が無い: {dirpath}", file=sys.stderr)
        return 1
    conn = connect(db)
    for path, text in conn.execute("SELECT path, text FROM lines ORDER BY path, seq"):
        print(f"{path}\t{text}")
    for path, body in conn.execute("SELECT path, body FROM docs ORDER BY path"):
        for line in body.splitlines():
            print(f"{path}\t{line}")
    for path, body in conn.execute("SELECT path, body FROM docs_history ORDER BY id"):
        for line in body.splitlines():
            print(f"{path}（前の中身）\t{line}")
    return 0


def cmd_export(dirpath: str, dest: str) -> int:
    """`dirpath` の下の記録を、いままでと同じファイルの形（行は改行でつなぐ・丸ごとはそのまま）で `dest` に書き出す。"""
    if os.path.exists(dest):
        raise SystemExit(f"⚠ {dest} はもうある（上書きしない）")
    _db, head, keys = _keys_under(dirpath)
    base = os.path.abspath(os.fspath(dirpath))
    for k in keys:
        rel = k[len(head):]
        text = read_text(os.path.join(base, *rel.split("/")))
        out = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write(text or "")
    print(f"{len(keys)} ファイル → {dest}")
    return 0


def cmd_backup(db: str, dest: str) -> int:
    if os.path.exists(dest):
        raise SystemExit(f"⚠ {dest} はもうある（控えを上書きしない）")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    out = sqlite3.connect(dest)
    with _LOCK:
        connect(db).backup(out)
    out.close()
    ok = sqlite3.connect(f"file:{dest}?mode=ro", uri=True).execute("PRAGMA integrity_check").fetchone()[0]
    print(f"控え {dest}（{os.path.getsize(dest) / 1e6:,.2f} MB・整合性 {ok}）")
    return 0 if ok == "ok" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("stats")
    st.add_argument("--db", default=REAL_DB)
    for c in ("ls", "cat", "append"):
        sub.add_parser(c).add_argument("path")
    sub.add_parser("dump").add_argument("dir")
    for c in ("import", "verify"):
        sub.add_parser(c).add_argument("dirs", nargs="+")
    rm = sub.add_parser("remove")
    rm.add_argument("dirs", nargs="+")
    rm.add_argument("--i-verified", action="store_true")
    ex = sub.add_parser("export")
    ex.add_argument("dir")
    ex.add_argument("--to", required=True)
    bk = sub.add_parser("backup")
    bk.add_argument("--db", default=REAL_DB)
    bk.add_argument("--to", required=True)
    it = sub.add_parser("init")
    it.add_argument("dir")
    it.add_argument("kind", choices=[n.split(".")[0] for n in DB_NAMES])
    args = ap.parse_args(argv)

    if args.cmd == "stats":
        return cmd_stats(args.db)
    if args.cmd == "ls":
        for name in listdir(args.path):
            print(name)
        return 0
    if args.cmd == "cat":
        text = read_text(args.path)
        if text is None:
            print(f"無い: {args.path}", file=sys.stderr)
            return 1
        sys.stdout.write(text)
        return 0
    if args.cmd == "append":
        append_many(args.path, [line.rstrip("\n") for line in sys.stdin if line.strip()])
        return 0
    if args.cmd == "dump":
        return cmd_dump(args.dir)
    if args.cmd == "import":
        return cmd_import(args.dirs)
    if args.cmd == "verify":
        return cmd_verify(args.dirs)
    if args.cmd == "remove":
        return cmd_remove(args.dirs, args.i_verified)
    if args.cmd == "backup":
        return cmd_backup(args.db, args.to)
    if args.cmd == "export":
        return cmd_export(args.dir, args.to)
    if args.cmd == "init":
        print(init(args.dir, args.kind))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
