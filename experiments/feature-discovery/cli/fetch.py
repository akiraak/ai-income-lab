"""取得 → `data/raw/`。⚠ **入口は薄く保つ**（引数を読んで ail を呼ぶだけ）。

    python3 -m cli.fetch --dataset daily
    python3 -m cli.fetch --dataset min1 --symbols SPY,QQQ
    python3 -m cli.fetch --import-legacy data      # ⚠ 既にある CSV を raw/ へ移すだけ（取得しない）

⚠ **`raw/` は取ってきたまま。以後どのコードも書き換えない**（rules.md 1 章）。
⚠ 取得は tastytrade サンプルの venv で動かす（`ttclient` と `websockets` が要る）。
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil

from ail import config, registry
from ail.data import check, store
import ail.bootstrap  # noqa: F401


def _record(directory: str, layer: str, period: str, extra: dict) -> None:
    """⚠ **書いたら必ず検査して manifest に残す**（rules.md 5 章）。"""
    reports, entries = {}, {}
    for s in store.symbols_in(directory):
        df = store.read_bars(directory, s)
        reports[s] = check.check_bars(df)
        entries[s] = {**store.fingerprint(df), "check": reports[s]}
    total = check.merge(reports)
    print(check.format_report(total, reports))
    fatal = check.fatal_of(total)
    if fatal:
        raise SystemExit(f"⚠ 不変条件に違反している: {fatal}")
    path = store.write_manifest(layer, period, entries, {"totals": total, **extra})
    print(f"→ {os.path.relpath(path, store.ROOT)}")


def import_legacy(src: str, source: str = "tastytrade") -> None:
    """⚠ **2026-09-08 以前に `data/*.csv` に置いていたものを `raw/` へ移す。** 中身は触らない。"""
    src = os.path.join(store.ROOT, src)
    for period in ("d", "m"):
        files = sorted(glob.glob(os.path.join(src, f"*_{period}.csv")))
        if not files:
            continue
        dst = store.raw_dir(source, period)
        os.makedirs(dst, exist_ok=True)
        for f in files:
            base = os.path.basename(f)[: -(len(period) + 5)]
            shutil.copy2(f, os.path.join(dst, f"{base}.csv"))
        print(f"{period} 足: {len(files)} 件 → {os.path.relpath(dst, store.ROOT)}")
        _record(dst, "raw", period, {"source": source, "imported_from": os.path.basename(src)})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", help="config/dataset/<名前>.toml")
    ap.add_argument("--symbols", help="カンマ区切り。省略時は universe の全部")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--import-legacy", metavar="DIR",
                    help="⚠ 取得せず、既存の <DIR>/*_<period>.csv を raw/ へ移す")
    args = ap.parse_args()

    if args.import_legacy:
        import_legacy(args.import_legacy)
        return
    if not args.dataset:
        raise SystemExit("--dataset か --import-legacy のどちらかが要る")

    ds = config.dataset(args.dataset)
    symbols = args.symbols.split(",") if args.symbols else config.symbols_of(ds["universe"])
    fetch = registry.resolve("source", ds["source"])
    bars = fetch(symbols, ds["period"], ds["days"], batch=args.batch)

    directory = store.raw_dir(ds["source"], ds["period"])
    for sym, df in bars.items():
        if len(df):
            store.write_bars(directory, sym, df)
    _record(directory, "raw", ds["period"],
            {"source": ds["source"], "dataset": ds["name"], "days_requested": ds["days"]})


if __name__ == "__main__":
    main()
