"""不変条件を層ごとに通す。⚠ **取得と調整のたびに自動で通す**（rules.md 5 章）。

    python3 -m cli.check --layer raw --period d
    python3 -m cli.check --layer adjusted --period d
"""

from __future__ import annotations

import argparse

from ail.data import check, store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", default="adjusted", choices=("raw", "adjusted"))
    ap.add_argument("--period", default="d")
    ap.add_argument("--source", default="tastytrade")
    args = ap.parse_args()

    directory = (store.raw_dir(args.source, args.period) if args.layer == "raw"
                 else store.adjusted_dir(args.period))
    reports = {s: check.check_bars(store.read_bars(directory, s))
               for s in store.symbols_in(directory)}
    if not reports:
        raise SystemExit(f"{directory} が空")
    total = check.merge(reports)
    print(f"層 {args.layer} / {args.period} 足")
    print(check.format_report(total, reports))
    if check.fatal_of(total):
        raise SystemExit("⚠ 止めるべき違反がある")


if __name__ == "__main__":
    main()
