"""調整 → `data/adjusted/`。⚠ **`raw/` は読むだけ。書き換えない**（rules.md 2 章）。

    python3 -m cli.adjust --period d              # 継ぎ目を直して adjusted/ へ書く
    python3 -m cli.adjust --period d --report     # ⚠ 書かずに継ぎ目の一覧だけ出す（Phase 1 の確認）

⚠ **「実際の変動」と判定した日は直さない。** AAPL 2000-09-29 の −52% や PG 2000-03-07 の −31% は
⚠ **実在した損失で、消すと「その日に何も起きなかった」ことになる。**
"""

from __future__ import annotations

import argparse
import os

import pandas as pd

from ail.data import adjust as adj
from ail.data import check, store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="d")
    ap.add_argument("--source", default="tastytrade")
    ap.add_argument("--report", action="store_true", help="⚠ 書かずに継ぎ目の一覧だけ出す")
    ap.add_argument("--symbols", help="カンマ区切り。省略時は raw/ にある全部")
    args = ap.parse_args()

    src = store.raw_dir(args.source, args.period)
    symbols = args.symbols.split(",") if args.symbols else store.symbols_in(src)
    if not symbols:
        raise SystemExit(f"{src} が空。先に `python3 -m cli.fetch` を回す")

    dst = store.adjusted_dir(args.period)
    all_breaks, entries, reports, summaries = [], {}, {}, {}
    for s in symbols:
        raw = store.read_bars(src, s)
        out, breaks, summary = adj.adjust(raw)
        if len(breaks):
            b = breaks.copy()
            b.insert(0, "symbol", s)
            all_breaks.append(b)
        summaries[s] = summary
        if not args.report:
            store.write_bars(dst, s, out)
            out["ts"] = pd.to_datetime(out["time_ms"], unit="ms", utc=True)
            reports[s] = check.check_bars(out)
            entries[s] = {**store.fingerprint(out), "check": reports[s],
                          "adjust": {k: v for k, v in summary.items() if k != "events"},
                          "events": summary["events"]}

    table = (pd.concat(all_breaks, ignore_index=True) if all_breaks
             else pd.DataFrame(columns=["symbol", "ts", "gap", "ratio", "kind"]))
    if len(table):
        show = table.assign(日付=table["ts"].dt.date.astype(str)).round(
            {"gap": 3, "ratio": 3, "dollar_vol_x": 2, "range_x": 2, "intraday": 3, "volume_x": 2})
        cols = ["symbol", "日付", "kind", "gap", "ratio", "dollar_vol_x", "range_x", "intraday"]
        print(show[cols].to_string(index=False))
        print()
        print(table["kind"].value_counts().to_string())
    else:
        print("継ぎ目なし")

    if args.report:
        print("\n⚠ --report なので何も書いていない。")
        return

    total = check.merge(reports)
    print()
    print(check.format_report(total, reports))
    fatal = check.fatal_of(total)
    if fatal:
        raise SystemExit(f"⚠ 調整後に不変条件へ違反した: {fatal}")
    rescaled = sum(v["rows_rescaled"] for v in summaries.values())
    path = store.write_manifest("adjusted", args.period, entries, {
        "totals": total, "source_layer": "raw", "source": args.source,
        "dividend_adjusted": False,      # ⚠ 既定は価格リターン（rules.md 2 章 規約 4）
        "repaired_breaks": int(table["kind"].str.startswith("直す").sum()) if len(table) else 0,
        "kept_as_real_move": int((table["kind"] == adj.KIND_REAL).sum()) if len(table) else 0,
        "rows_rescaled": int(rescaled),
    })
    print(f"→ {os.path.relpath(path, store.ROOT)}")


if __name__ == "__main__":
    main()
