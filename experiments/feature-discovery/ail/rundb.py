"""研究の記録の正本（SQLite）。⚠ **1 実行 1 記録・上書きしない**（rules.md 10 章。2026-09-21 にディレクトリから移した）。

    runs/research.sqlite
      runs         実行 1 本 1 行（名前 ＝ いままでのディレクトリ名 `<時刻>_<実験名>`）
      files        実行の中のファイル 1 つ 1 行（`summary.csv`・`fitted/calibration_f1.json` …）。中身はそのまま
      queue_state  `cli.queue` の再開の状態（1 キュー 1 行。これだけは書き換える）

  - ⚠ **中身は元のバイト列のまま入れる**（JSON は文字列のまま ＝ `json_extract` で引ける。ほかは zlib で縮めるだけ）。
    ⚠ `sha256` は元のバイト列の指紋 ＝ 取り込んだファイルと 1 ビットも違わないことを確かめる鍵
  - ⚠ **閉じた実行は書き換えない・消さない**（トリガーが止める）。足すのは許す（`cli.report --recheck` が
    `checks.json` の無い実行にだけ足す）
  - ⚠ 実行中の実行は、書くたびにその場で入れる（`cli.run` は自分の `summary.csv` が台帳に入った状態で
    `n_trials` を数える ＝ いままでのディレクトリと同じ見え方）
  - 標準ライブラリだけ（pandas を import しない。管理画面・vibeboard の sidecar からも同じ形で読める）
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import time
import zlib

FILE_NAME = "research.sqlite"
SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  name        TEXT PRIMARY KEY,          -- <時刻>_<実験名>（いままでのディレクトリ名）
  started_at  TEXT,                      -- 名前の頭の時刻
  origin      TEXT NOT NULL,             -- 'run'（実行が書いた）／ 'import'（ディレクトリから取り込んだ）
  created_at  TEXT NOT NULL,             -- DB に入った時刻
  closed_at   TEXT,                      -- ⚠ 閉じた後は書き換えない・消さない
  host        TEXT,
  pid         INTEGER                    -- 実行中の印（落ちた実行を `sweep` が閉じる）
);
CREATE TABLE IF NOT EXISTS files (
  run     TEXT NOT NULL REFERENCES runs(name),
  path    TEXT NOT NULL,                 -- 実行の中の道（`/` 区切り）
  size    INTEGER NOT NULL,              -- 元のバイト数
  sha256  TEXT NOT NULL,                 -- 元のバイト列の指紋
  codec   TEXT NOT NULL,                 -- 'text'（UTF-8 の文字列のまま）／ 'zlib'
  body    BLOB NOT NULL,
  PRIMARY KEY (run, path)
);
CREATE TABLE IF NOT EXISTS queue_state (name TEXT PRIMARY KEY, doc TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS files_frozen_update BEFORE UPDATE ON files
  WHEN (SELECT closed_at FROM runs WHERE name = OLD.run) IS NOT NULL
  BEGIN SELECT RAISE(ABORT, '閉じた実行のファイルは書き換えない（rules.md 10 章）'); END;
CREATE TRIGGER IF NOT EXISTS files_frozen_delete BEFORE DELETE ON files
  WHEN (SELECT closed_at FROM runs WHERE name = OLD.run) IS NOT NULL
  BEGIN SELECT RAISE(ABORT, '閉じた実行のファイルは消さない（rules.md 10 章）'); END;
CREATE TRIGGER IF NOT EXISTS runs_frozen_delete BEFORE DELETE ON runs
  WHEN OLD.closed_at IS NOT NULL
  BEGIN SELECT RAISE(ABORT, '閉じた実行は消さない（rules.md 10 章）'); END;
"""

_CONN: dict[str, sqlite3.Connection] = {}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H-%M-%S")


def connect(path: str, readonly: bool = False) -> sqlite3.Connection:
    """⚠ 読むだけなら `readonly=True`（無ければ作らない・書けない）。"""
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        conn = sqlite3.connect(path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")       # 書いている間も読み手（管理画面）を止めない
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def shared(path: str) -> sqlite3.Connection | None:
    """読み手が使い回す接続（プロセスに 1 本）。⚠ DB が無ければ None（作らない）。"""
    conn = _CONN.get(path)
    if conn is None:
        if not os.path.exists(path):
            return None
        conn = _CONN[path] = connect(path, readonly=True)
    return conn


def forget(path: str | None = None) -> None:
    """使い回しの接続を閉じる（テストが DB を入れ替えたとき）。"""
    for p in ([path] if path else list(_CONN)):
        if (c := _CONN.pop(p, None)) is not None:
            c.close()


# --- 中身 --------------------------------------------------------------

def encode(path: str, data: bytes) -> tuple[str, bytes | str]:
    if path.endswith(".json"):
        try:
            return "text", data.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return "zlib", zlib.compress(data, 6)


def decode(codec: str, body) -> bytes:
    if codec == "text":
        return body.encode("utf-8") if isinstance(body, str) else bytes(body)
    if codec == "zlib":
        return zlib.decompress(body)
    raise ValueError(f"知らない codec: {codec}")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- 書く --------------------------------------------------------------

def open_run(conn: sqlite3.Connection, name: str, origin: str = "run") -> None:
    """実行を 1 つ始める。⚠ 同じ名前の実行がもうあれば止める（1 実行 1 記録）。"""
    if conn.execute("SELECT 1 FROM runs WHERE name = ?", (name,)).fetchone():
        raise SystemExit(f"⚠ 実行 {name} はもう DB にある（1 実行 1 記録。rules.md 10 章）")
    conn.execute("INSERT INTO runs (name, started_at, origin, created_at, host, pid) VALUES (?, ?, ?, ?, ?, ?)",
                 (name, name[:19], origin, now(), socket.gethostname(), os.getpid() if origin == "run" else None))
    conn.commit()


def put(conn: sqlite3.Connection, run: str, path: str, data: bytes, commit: bool = True) -> None:
    """ファイルを 1 つ入れる。⚠ 閉じた実行の既存のファイルは書き換えられない（トリガー）。"""
    codec, body = encode(path, data)
    conn.execute("INSERT INTO files (run, path, size, sha256, codec, body) VALUES (?, ?, ?, ?, ?, ?)"
                 " ON CONFLICT (run, path) DO UPDATE SET size = excluded.size, sha256 = excluded.sha256,"
                 " codec = excluded.codec, body = excluded.body",
                 (run, path, len(data), sha256(data), codec, body))
    if commit:
        conn.commit()


def close_run(conn: sqlite3.Connection, run: str) -> None:
    conn.execute("UPDATE runs SET closed_at = ?, pid = NULL WHERE name = ? AND closed_at IS NULL", (now(), run))
    conn.commit()


def _walk(root: str) -> list[str]:
    out = []
    for d, _dirs, fs in os.walk(root):
        for f in fs:
            out.append(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/"))
    return sorted(out)


def put_dir(conn: sqlite3.Connection, run: str, root: str, commit: bool = True) -> int:
    """ディレクトリの中身を全部入れる（道は `root` からの相対）。入れたファイルの数。"""
    n = 0
    for rel in _walk(root):
        with open(os.path.join(root, rel), "rb") as f:
            put(conn, run, rel, f.read(), commit=False)
        n += 1
    if commit:
        conn.commit()
    return n


def ingest_dir(conn: sqlite3.Connection, root: str, name: str | None = None) -> int:
    """⚠ いままでの実行ディレクトリを 1 つ取り込む（1 つのトランザクション。途中で落ちたら何も入らない）。"""
    name = name or os.path.basename(os.path.normpath(root))
    if conn.execute("SELECT 1 FROM runs WHERE name = ?", (name,)).fetchone():
        raise SystemExit(f"⚠ 実行 {name} はもう DB にある（取り込み直さない）")
    with conn:
        conn.execute("INSERT INTO runs (name, started_at, origin, created_at, closed_at, host) VALUES (?, ?, 'import', ?, ?, ?)",
                     (name, name[:19], now(), now(), socket.gethostname()))
        n = put_dir(conn, name, root, commit=False)
    return n


def verify_dir(conn: sqlite3.Connection, root: str, name: str | None = None) -> list[str]:
    """ディレクトリと DB の中身を突き合わせる。⚠ **食い違いの一覧**（空なら 1 ビットも違わない）。

    ⚠ DB から取り出したバイト列の指紋と、ディスクのファイルの指紋を比べる（DB に書いた指紋だけを信じない）。
    """
    name = name or os.path.basename(os.path.normpath(root))
    disk = set(_walk(root))
    db = {p for (p,) in conn.execute("SELECT path FROM files WHERE run = ?", (name,))}
    bad = [f"DB に無い: {p}" for p in sorted(disk - db)] + [f"ディスクに無い: {p}" for p in sorted(db - disk)]
    for p in sorted(disk & db):
        with open(os.path.join(root, p), "rb") as f:
            want = sha256(f.read())
        got = get(conn, name, p)
        if got is None or sha256(got) != want:
            bad.append(f"中身が違う: {p}")
    return bad


# --- 読む --------------------------------------------------------------

def list_runs(conn: sqlite3.Connection | None) -> list[str]:
    if conn is None:
        return []
    return [n for (n,) in conn.execute("SELECT name FROM runs ORDER BY name")]


def list_files(conn: sqlite3.Connection | None, run: str, prefix: str = "") -> list[str]:
    if conn is None:
        return []
    return [p for (p,) in conn.execute("SELECT path FROM files WHERE run = ? AND substr(path, 1, ?) = ? ORDER BY path",
                                       (run, len(prefix), prefix))]


def get(conn: sqlite3.Connection | None, run: str, path: str) -> bytes | None:
    if conn is None:
        return None
    row = conn.execute("SELECT codec, body FROM files WHERE run = ? AND path = ?", (run, path)).fetchone()
    return None if row is None else decode(*row)


def get_json(conn: sqlite3.Connection | None, run: str, path: str):
    data = get(conn, run, path)
    return None if data is None else json.loads(data.decode("utf-8"))


def export_run(conn: sqlite3.Connection, run: str, dest: str, prefix: str = "") -> int:
    """実行のファイルを `dest` に書き戻す（`torch.load` のようにファイルの道が要る読み手のため）。"""
    n = 0
    for p in list_files(conn, run, prefix):
        out = os.path.join(dest, *p.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(get(conn, run, p))
        n += 1
    return n


# --- キューの状態（書き換える。実行の記録ではない）--------------------

def load_queue(conn: sqlite3.Connection | None, name: str) -> dict | None:
    if conn is None:
        return None
    row = conn.execute("SELECT doc FROM queue_state WHERE name = ?", (name,)).fetchone()
    return None if row is None else json.loads(row[0])


def save_queue(conn: sqlite3.Connection, name: str, doc: dict) -> None:
    conn.execute("INSERT INTO queue_state VALUES (?, ?, ?) ON CONFLICT (name) DO UPDATE SET doc = excluded.doc,"
                 " updated_at = excluded.updated_at", (name, json.dumps(doc, ensure_ascii=False, indent=2), now()))
    conn.commit()


# --- 落ちた実行 --------------------------------------------------------

def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def sweep(conn: sqlite3.Connection, work_root: str) -> list[str]:
    """⚠ **途中で落ちた実行を閉じる**（プロセスが居ない・閉じていない）。作業の置き場に残ったファイルも入れる。

    ⚠ 閉じるだけで、中身は足さない・消さない（`summary.csv` が無ければ台帳には今までどおり行が立たない）。
    """
    import shutil

    host = socket.gethostname()
    closed = []
    for name, h, pid in conn.execute("SELECT name, host, pid FROM runs WHERE closed_at IS NULL").fetchall():
        if h != host or _alive(pid):
            continue                     # ⚠ ほかの機械の実行は、その機械でしか生死を確かめられない
        work = os.path.join(work_root, name)
        if os.path.isdir(work):
            put_dir(conn, name, work)
            shutil.rmtree(work)          # ⚠ 入れたら消す（2 か所に置かない）
        close_run(conn, name)
        closed.append(name)
    return closed
