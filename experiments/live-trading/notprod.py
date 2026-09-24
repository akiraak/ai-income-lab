#!/usr/bin/env python3
"""「本番の機械ではない」印 `NOT_PRODUCTION` の操作（⚠ CLI だけ。置くのも外すのも利用者 ＝ 3 台の役割分け Phase 4・5）。

    python notprod.py status                          # 印があるか・中身・この機械の hostname と合うか
    python notprod.py set --reason "本番は 13500t"    # 印を置く（この機械の hostname を書く）。以後この機械では本番の発注を拒む
    python notprod.py clear                           # 印を外す（13500t が落ちた日に titan へ戻すとき ＝ Phase 5）

印があると `run_day.py`・`sample.py` は **本番の発注（--env prod --mode submit）だけ** を許可より前で拒む（rc=7）。
plan・dry-run・cert は通る。詳しくは live-trading.md §0-14。
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mode as modes  # noqa: E402


def show(mark: modes.NotProduction | None) -> int:
    print(f"機械: {modes.hostname()}   印: {modes.not_production_file()}")
    if mark is None:
        print("印なし ＝ この機械は本番の発注ができる（許可の 3 段はそのまま要る）")
        return 0
    print(f"印あり ＝ 本番の発注（--env prod --mode submit）を拒む: {mark.describe()}")
    return 0 if mark.matches_host else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="「本番の機械ではない」印（CLI だけ）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    st = sub.add_parser("set")
    st.add_argument("--reason", default=None)
    sub.add_parser("clear")
    args = ap.parse_args()
    by = getpass.getuser()
    if args.cmd == "set":
        before = modes.read_not_production()
        if before is not None and before.error is None and before.matches_host:
            print(f"既に印がある（{before.describe()}）。置き直さない")
            return show(before)
        mark = modes.set_not_production(by=by, reason=args.reason)
        print(f"印を置いた: {mark.describe()}")
        print("⚠ この機械では本番の発注（--env prod --mode submit）を拒む。外すのは notprod.py clear")
        return 0
    if args.cmd == "clear":
        before = modes.clear_not_production(by=by)
        print("印は無かった（何もしない）" if before is None else f"印を外した: {before.describe()}")
        if before is not None:
            print("⚠ この機械で本番の発注ができるようになった（許可の 3 段を出せば）。⚠ 13500t の cron が同時に発注していないことを確かめる")
        return 0
    return show(modes.read_not_production())


if __name__ == "__main__":
    sys.exit(main())
