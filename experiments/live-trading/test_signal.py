"""試験用トレーダー `test_a` の合図（`config/signals/test_a.csv`）を 1 行に書き換える。

    python3 test_signal.py buy             # 今日（ET）の行を「買い 100 ／ 出口 0」に
    python3 test_signal.py exit            # 今日（ET）の行を「買い 0 ／ 出口 100」に（同じ日の 2 回目の起動で売る）
    python3 test_signal.py buy --date 2026-09-21

⚠ 合図を書くだけ（発注しない・ネットワークを使わない）。2026-09-20 の利用者決定 D1 ＝ 案 B（朝に買い・窓で売り）のための道具。
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "config", "signals", "test_a.csv")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("side", choices=["buy", "exit"])
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（既定は今日 ET）")
    ap.add_argument("--symbol", default="T")
    ap.add_argument("--path", default=PATH)
    args = ap.parse_args()
    day = args.date or datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    buy, exit_ = (100, 0) if args.side == "buy" else (0, 100)
    with open(args.path, "w", encoding="utf-8", newline="") as f:
        f.write("date,symbol,buy,exit\n")
        f.write(f"{day},{args.symbol},{buy},{exit_}\n")
    print(f"{os.path.relpath(args.path, HERE)}: {day} {args.symbol} buy={buy} exit={exit_}")


if __name__ == "__main__":
    main()
