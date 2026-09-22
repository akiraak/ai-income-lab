"""研究の記録の正本（SQLite）。⚠ **1 実行 1 記録・上書きしない**（rules.md 10 章。2026-09-21 にディレクトリから移した）。

    runs/research.sqlite
      runs         実行 1 本 1 行（名前 ＝ いままでのディレクトリ名 `<時刻>_<実験名>`）
      files        実行の中のファイル 1 つ 1 行（`summary.csv`・`fitted/calibration_f1.json` …）。中身はそのまま
      queue_state  `cli.queue` の再開の状態（1 キュー 1 行。これだけは書き換える）
      outputs      診断・突き合わせの出力（いままでの `out/` のファイル 1 つ 1 行。道は `out/` からの相対のまま。
                   ⚠ 実行ではない ＝ 検証結果一覧・実行タブには出ない。書き換えない・消さない。2026-09-21）
      ledger_rows  検証結果一覧の行と判定（⚠ **記録ではなく生成物**。`ledger.md` と同じもので、吐き直すたびにまるごと
                   入れ直す。標準ライブラリだけの読み手 ＝ vibeboard の予測モデルのタブが、判定を pandas なしで引くため）

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
CREATE TABLE IF NOT EXISTS outputs (
  path        TEXT PRIMARY KEY,          -- `out/` からの道（`diag/<時刻>_<名前>/folds.csv`・`crosscheck_<日>.json` …）
  size        INTEGER NOT NULL,          -- 元のバイト数
  sha256      TEXT NOT NULL,             -- 元のバイト列の指紋
  codec       TEXT NOT NULL,             -- 'text' ／ 'zlib'（files と同じ）
  body        BLOB NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS outputs_frozen_update BEFORE UPDATE ON outputs
  BEGIN SELECT RAISE(ABORT, '診断の出力は書き換えない（rules.md 10 章）'); END;
CREATE TRIGGER IF NOT EXISTS outputs_frozen_delete BEFORE DELETE ON outputs
  BEGIN SELECT RAISE(ABORT, '診断の出力は消さない（rules.md 10 章）'); END;
CREATE TABLE IF NOT EXISTS ledger_rows (
  leak        INTEGER NOT NULL,          -- 1 ＝ leak 対照の行
  trial_name  TEXT NOT NULL,             -- 検証名（rules.md 10-2。識別項目の 12 列の別名）
  model_name  TEXT NOT NULL,             -- 予測モデル名（θ を除いた 11 列の別名）
  is_trial    INTEGER NOT NULL,          -- 1 ＝ n_trials に数える行（catalog.is_trial）
  verdict     TEXT NOT NULL,             -- 判定（採る ／ 保留 ／ 落とす ／ 基準 …）
  closed      INTEGER NOT NULL,          -- 1 ＝ 「閉じる」注記のある行
  first_run   TEXT,                      -- 実行一覧のいちばん古い日（YYYY-MM-DD。時刻で始まらない実行は数えない）
  last_run    TEXT,
  runs        TEXT NOT NULL,             -- 実行一覧（JSON）
  doc         TEXT NOT NULL,             -- 検証結果一覧の行まるごと（JSON。NaN は null）
  PRIMARY KEY (leak, trial_name)
);
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


# --- 診断・突き合わせの出力（いままでの out/。書き換えない）-----------

def put_output(conn: sqlite3.Connection, path: str, data: bytes) -> None:
    """診断の出力を 1 つ入れる。⚠ 同じ道がもうあれば止める（上書きしない。名前に時刻か日付を入れる）。"""
    if conn.execute("SELECT 1 FROM outputs WHERE path = ?", (path,)).fetchone():
        raise SystemExit(f"⚠ 出力 {path} はもう DB にある（上書きしない。rules.md 10 章）")
    codec, body = encode(path, data)
    with conn:
        conn.execute("INSERT INTO outputs (path, size, sha256, codec, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (path, len(data), sha256(data), codec, body, now()))


def get_output(conn: sqlite3.Connection | None, path: str) -> bytes | None:
    if conn is None:
        return None
    row = conn.execute("SELECT codec, body FROM outputs WHERE path = ?", (path,)).fetchone()
    return None if row is None else decode(*row)


def list_outputs(conn: sqlite3.Connection | None, prefix: str = "") -> list[str]:
    if conn is None:
        return []
    return [p for (p,) in conn.execute("SELECT path FROM outputs WHERE substr(path, 1, ?) = ? ORDER BY path",
                                       (len(prefix), prefix))]


def ingest_outputs(conn: sqlite3.Connection, root: str) -> tuple[int, int]:
    """⚠ いままでの `out/` を取り込む（1 つのトランザクション）。(入れた数, もう同じ中身が入っていた数)。
    ⚠ 同じ道に違う中身が入っていれば止める（何も入れない）。"""
    added = same = 0
    with conn:
        for rel in _walk(root):
            with open(os.path.join(root, rel), "rb") as f:
                data = f.read()
            row = conn.execute("SELECT sha256 FROM outputs WHERE path = ?", (rel,)).fetchone()
            if row is not None:
                if row[0] != sha256(data):
                    raise SystemExit(f"⚠ 出力 {rel} は DB に違う中身で入っている（取り込み直さない）")
                same += 1
                continue
            codec, body = encode(rel, data)
            conn.execute("INSERT INTO outputs (path, size, sha256, codec, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (rel, len(data), sha256(data), codec, body, now()))
            added += 1
    return added, same


def verify_outputs(conn: sqlite3.Connection, root: str) -> tuple[list[str], list[str]]:
    """`out/` のファイルと DB を突き合わせる。(一致した道, 食い違いの一覧)。⚠ DB から取り出したバイト列の指紋で比べる。"""
    ok, bad = [], []
    for rel in _walk(root):
        with open(os.path.join(root, rel), "rb") as f:
            want = sha256(f.read())
        got = get_output(conn, rel)
        if got is None:
            bad.append(f"DB に無い: {rel}")
        elif sha256(got) != want:
            bad.append(f"中身が違う: {rel}")
        else:
            ok.append(rel)
    return ok, bad


# --- 検証結果一覧（生成物。まるごと入れ直す）--------------------------

LEDGER_COLUMNS = ("leak", "trial_name", "model_name", "is_trial", "verdict", "closed",
                  "first_run", "last_run", "runs", "doc")


def write_ledger(conn: sqlite3.Connection, items: list[dict], meta: dict[str, str]) -> None:
    """⚠ **検証結果一覧の行をまるごと入れ直す**（1 つのトランザクション。途中で落ちたら前のまま）。

    ⚠ 記録ではなく `ledger.md` と同じ生成物なので、書き換えてよい（判定の規則は `catalog.py` の 1 か所のまま）。
    """
    cols = ", ".join(LEDGER_COLUMNS)
    marks = ", ".join(":" + c for c in LEDGER_COLUMNS)
    with conn:
        conn.execute("DELETE FROM ledger_rows")
        conn.executemany(f"INSERT INTO ledger_rows ({cols}) VALUES ({marks})", items)
        conn.executemany("INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                         list(meta.items()))


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
