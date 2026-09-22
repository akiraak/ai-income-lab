"""研究の記録の DB（`runs/research.sqlite`）の道具。⚠ **正本は DB**（rules.md 10 章。2026-09-21 にディレクトリから移した）。

    python3 -m cli.db stats                        # 実行の数・ファイルの数・大きさ
    python3 -m cli.db import                       # ⚠ いままでの実行ディレクトリ（runs/<実行>/）を全部取り込む（入っているものはとばす）
    python3 -m cli.db verify                       # ⚠ ディレクトリと DB を 1 ビットずつ突き合わせる（消す前に必ず）
    python3 -m cli.db remove-dirs --i-verified     # ⚠ 突き合わせて一致した実行ディレクトリだけを消す（⚠ 利用者の了承のあと）
    python3 -m cli.db sweep                        # 途中で落ちた実行を閉じる
    python3 -m cli.db export --run <実行> --to <道>  # 実行のファイルを書き戻す（読むための写し）
    python3 -m cli.db backup --to <道>             # ⚠ 控え（SQLite の backup。書いている最中でも壊れない写し）
    python3 -m cli.db import-out                   # ⚠ いままでの out/（診断・突き合わせの出力）を表 outputs に取り込む（2026-09-21）
    python3 -m cli.db verify-out                   # ⚠ out/ のファイルと DB を 1 ビットずつ突き合わせる
    python3 -m cli.db remove-out --i-verified      # ⚠ 突き合わせて一致した out/ のファイルだけを消す（⚠ 利用者の了承のあと）
    python3 -m cli.db export-out --prefix <道> --to <置き場>   # 出力を書き戻す（読むための写し。例 --prefix diag/2026-09-13T08-56-31_trade_own_ridge_a/）

⚠ **取り込みは 1 実行 1 トランザクション**（途中で止めても、入った実行は全部そろっている）。
⚠ **`remove-dirs` は `verify` と同じ突き合わせをもう一度してから消す**（DB から取り出したバイト列の指紋 ＝ ディスクの指紋）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time

from ail import rundb, runs

# ⚠ 実行ディレクトリではないもの（`cli.queue` の状態 ・ 作業の置き場）
NOT_RUNS = {"queue", "work"}


def run_dirs() -> list[str]:
    """`runs/` にまだ残っている実行ディレクトリ（`config.json` の有無は問わない ＝ 落ちた実行も記録）。"""
    if not os.path.isdir(runs.RUNS):
        return []
    return sorted(n for n in os.listdir(runs.RUNS)
                  if n not in NOT_RUNS and os.path.isdir(os.path.join(runs.RUNS, n)))


def queue_files() -> list[str]:
    d = os.path.join(runs.RUNS, "queue")
    return sorted(f for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []


def cmd_stats(conn: sqlite3.Connection) -> None:
    n_runs, n_open = conn.execute("SELECT count(*), sum(closed_at IS NULL) FROM runs").fetchone()
    n_files, size, stored = conn.execute("SELECT count(*), coalesce(sum(size), 0), coalesce(sum(length(body)), 0) FROM files").fetchone()
    print(f"DB {runs.db_path()}（{os.path.getsize(runs.db_path()) / 1e6:,.1f} MB）")
    print(f"  実行 {n_runs:,}（閉じていない {n_open or 0}）／ ファイル {n_files:,} ／ 元の大きさ {size / 1e6:,.1f} MB → 入れた大きさ {stored / 1e6:,.1f} MB")
    print(f"  キューの状態 {conn.execute('SELECT count(*) FROM queue_state').fetchone()[0]}")
    left = run_dirs()
    if left:
        print(f"  ⚠ runs/ に残っている実行ディレクトリ {len(left)}（取り込んで突き合わせたら `remove-dirs`）")
    n_out, out_size = conn.execute("SELECT count(*), coalesce(sum(size), 0) FROM outputs").fetchone()
    print(f"  診断・突き合わせの出力 {n_out:,}（{out_size / 1e6:,.2f} MB）")
    left = out_files()
    if left:
        print(f"  ⚠ out/ に残っているファイル {len(left)}（取り込んで突き合わせたら `remove-out`）")


def cmd_import(conn: sqlite3.Connection) -> None:
    have = set(rundb.list_runs(conn))
    todo = [n for n in run_dirs() if n not in have]
    t0 = time.time()
    total = 0
    for i, name in enumerate(todo, 1):
        total += rundb.ingest_dir(conn, os.path.join(runs.RUNS, name), name)
        if i % 25 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)} 実行（ファイル {total:,}・{time.time() - t0:.0f} 秒）", flush=True)
    for f in queue_files():
        name = f[:-5]
        if rundb.load_queue(conn, name) is None:
            import json
            with open(os.path.join(runs.RUNS, "queue", f), encoding="utf-8") as fh:
                rundb.save_queue(conn, name, json.load(fh))
            print(f"  キューの状態 {name}")
    print(f"取り込んだ実行 {len(todo)}（とばした {len(have & set(run_dirs()))}）・{time.time() - t0:.1f} 秒")


def _queue_mismatch(conn: sqlite3.Connection) -> list[str]:
    import json
    bad = []
    for f in queue_files():
        with open(os.path.join(runs.RUNS, "queue", f), encoding="utf-8") as fh:
            if rundb.load_queue(conn, f[:-5]) != json.load(fh):
                bad.append(f"queue/{f}: 中身が違う")
    return bad


def verify(conn: sqlite3.Connection) -> tuple[list[str], dict[str, list[str]]]:
    """(一致した実行, 食い違った実行 → 食い違いの一覧)。⚠ DB に無い実行も「食い違い」。"""
    have = set(rundb.list_runs(conn))
    ok, bad = [], {}
    for name in run_dirs():
        if name not in have:
            bad[name] = ["DB に無い"]
            continue
        diff = rundb.verify_dir(conn, os.path.join(runs.RUNS, name), name)
        (bad.__setitem__(name, diff) if diff else ok.append(name))
    return ok, bad


def cmd_verify(conn: sqlite3.Connection) -> int:
    t0 = time.time()
    ok, bad = verify(conn)
    qbad = _queue_mismatch(conn)
    print(f"一致 {len(ok)} ／ 食い違い {len(bad)} ／ キューの状態の食い違い {len(qbad)}（{time.time() - t0:.1f} 秒）")
    for name, diff in list(bad.items())[:20]:
        print(f"  ⚠ {name}: {'; '.join(diff[:5])}")
    for q in qbad:
        print(f"  ⚠ {q}")
    return 0 if not bad and not qbad else 1


def cmd_remove_dirs(conn: sqlite3.Connection, confirmed: bool) -> int:
    if not confirmed:
        raise SystemExit("⚠ 消すと戻せない（runs/ は git 管理外）。`verify` の結果を見てから --i-verified を付ける")
    ok, bad = verify(conn)
    qbad = _queue_mismatch(conn)
    for name in ok:
        shutil.rmtree(os.path.join(runs.RUNS, name))
    if not qbad:
        for f in queue_files():
            os.remove(os.path.join(runs.RUNS, "queue", f))
        with_q = os.path.join(runs.RUNS, "queue")
        if os.path.isdir(with_q) and not os.listdir(with_q):
            os.rmdir(with_q)
    print(f"消した実行ディレクトリ {len(ok)} ／ ⚠ 残した（食い違い） {len(bad)} ／ キューの状態 {'消した' if not qbad else '⚠ 残した'}")
    return 0 if not bad and not qbad else 1


def out_files() -> list[str]:
    """`out/` にまだ残っているファイル（`out/` からの道）。"""
    return rundb._walk(runs.OUT) if os.path.isdir(runs.OUT) else []


def cmd_import_out(conn: sqlite3.Connection) -> None:
    if not os.path.isdir(runs.OUT):
        print("out/ が無い（取り込むものなし）")
        return
    t0 = time.time()
    added, same = rundb.ingest_outputs(conn, runs.OUT)
    print(f"取り込んだ出力 {added} ／ もう同じ中身が入っていた {same}（{time.time() - t0:.1f} 秒）")


def cmd_verify_out(conn: sqlite3.Connection) -> int:
    t0 = time.time()
    ok, bad = rundb.verify_outputs(conn, runs.OUT) if os.path.isdir(runs.OUT) else ([], [])
    for b in bad:
        print("  ⚠", b)
    print(f"一致 {len(ok)} ／ 食い違い {len(bad)}（{time.time() - t0:.1f} 秒）")
    return 0 if not bad else 1


def cmd_remove_out(conn: sqlite3.Connection, confirmed: bool) -> int:
    if not confirmed:
        raise SystemExit("⚠ 消すと戻せない（out/ は git 管理外）。`verify-out` の結果を見てから --i-verified を付ける")
    ok, bad = rundb.verify_outputs(conn, runs.OUT) if os.path.isdir(runs.OUT) else ([], [])
    for rel in ok:
        os.remove(os.path.join(runs.OUT, rel))
    for d, _dirs, _fs in sorted(os.walk(runs.OUT), key=lambda x: -len(x[0])):     # 空になったディレクトリを下から
        if not os.listdir(d):
            os.rmdir(d)
    print(f"消したファイル {len(ok)} ／ ⚠ 残した（食い違い） {len(bad)}")
    return 0 if not bad else 1


def cmd_export_out(conn: sqlite3.Connection, prefix: str, dest: str) -> None:
    n = 0
    for rel in rundb.list_outputs(conn, prefix):
        out = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(rundb.get_output(conn, rel))
        n += 1
    print(f"{n} ファイル → {dest}")


def cmd_backup(conn: sqlite3.Connection, dest: str) -> None:
    if os.path.exists(dest):
        raise SystemExit(f"⚠ {dest} はもうある（控えを上書きしない。日付を付けた別の名前にする）")
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    out = sqlite3.connect(dest)
    with out:
        conn.backup(out)
    out.close()
    ok = sqlite3.connect(f"file:{dest}?mode=ro", uri=True).execute("PRAGMA integrity_check").fetchone()[0]
    print(f"控え {dest}（{os.path.getsize(dest) / 1e6:,.1f} MB・整合性 {ok}）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("stats", "import", "verify", "sweep"):
        sub.add_parser(c)
    rm = sub.add_parser("remove-dirs")
    rm.add_argument("--i-verified", action="store_true")
    ex = sub.add_parser("export")
    ex.add_argument("--run", required=True)
    ex.add_argument("--to", required=True)
    bk = sub.add_parser("backup")
    bk.add_argument("--to", required=True)
    for c in ("import-out", "verify-out"):
        sub.add_parser(c)
    ro = sub.add_parser("remove-out")
    ro.add_argument("--i-verified", action="store_true")
    eo = sub.add_parser("export-out")
    eo.add_argument("--prefix", default="")
    eo.add_argument("--to", required=True)
    args = ap.parse_args(argv)

    conn = rundb.connect(runs.db_path())
    if args.cmd == "stats":
        cmd_stats(conn)
    elif args.cmd == "import":
        cmd_import(conn)
    elif args.cmd == "verify":
        return cmd_verify(conn)
    elif args.cmd == "remove-dirs":
        return cmd_remove_dirs(conn, args.i_verified)
    elif args.cmd == "sweep":
        closed = rundb.sweep(conn, runs.work_root())
        print(f"閉じた実行 {len(closed)}" + (": " + ", ".join(closed) if closed else ""))
    elif args.cmd == "export":
        name = runs.resolve(args.run)
        print(f"{rundb.export_run(conn, name, args.to)} ファイル → {args.to}")
    elif args.cmd == "backup":
        cmd_backup(conn, args.to)
    elif args.cmd == "import-out":
        cmd_import_out(conn)
    elif args.cmd == "verify-out":
        return cmd_verify_out(conn)
    elif args.cmd == "remove-out":
        return cmd_remove_out(conn, args.i_verified)
    elif args.cmd == "export-out":
        cmd_export_out(conn, args.prefix, args.to)
    return 0


if __name__ == "__main__":
    sys.exit(main())
