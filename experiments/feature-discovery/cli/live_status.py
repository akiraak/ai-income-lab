"""実売買用の置き場（`AIL_DATA_DIR` ＝ `data-live/`）の「どこまで入っているか」を出す。`live_update.sh` の最後に呼ぶ。

    AIL_DATA_DIR=data-live python3 -m cli.live_status --dataset daily --exog exog_live --experiment trade_ownex_ridge_a

⚠ **読むだけ**（何も取らない・何も書かない）。rc: 0 ＝ 揃っている ／ 2 ＝ 足が前の営業日に届いていない銘柄がある・
最終日が銘柄で割れている・外部系列が古くて `ex_` の列が欠損になる。
⚠ **市場時間中に取ると、最後の 1 本は「今日の途中の足」**（DXLink は更新中の足も返す）。`cli.predict` は
`asof` 以降の行を落としてから代役の足を足すこと。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

from ail import config
from ail.data import store

SAMPLE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tastytrade-api-sample"))


def previous_session(today):
    """今日（ET）より前の、直近の NYSE の営業日。"""
    if SAMPLE not in sys.path:
        sys.path.insert(0, SAMPLE)
    import market_calendar
    cal = market_calendar.nyse()
    d = today - timedelta(days=1)
    while not cal.is_trading_day(d):
        d -= timedelta(days=1)
    return d


def last_date(path: str):
    df = pd.read_csv(path, usecols=["time_ms"])
    return pd.to_datetime(int(df["time_ms"].max()), unit="ms", utc=True).date() if len(df) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="daily")
    ap.add_argument("--exog", default="exog_live")
    ap.add_argument("--experiment", default="trade_ownex_ridge_a",
                    help="`ex_lag_days` と `ex_max_stale_days` を読む実験（外部系列が古すぎないかの線）")
    args = ap.parse_args()

    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("America/New_York")).date()
    want = previous_session(today)
    ds = config.dataset(args.dataset)
    symbols = config.symbols_of(ds["universe"])
    print(f"置き場 {store.DATA}")
    print(f"今日（ET）{today} ／ 前の営業日 {want}")

    rc = 0
    for layer, directory in (("raw", store.raw_dir(ds["source"], ds["period"])),
                             ("adjusted", store.adjusted_dir(ds["period"]))):
        ends = {}
        for s in symbols:
            p = store.path_of(directory, s)
            ends[s] = last_date(p) if os.path.exists(p) else None
        by: dict = {}
        for s, d in ends.items():
            by.setdefault(d, []).append(s)
        for d in sorted(by, key=lambda x: (x is None, x), reverse=True):
            names = by[d]
            tail = "" if len(names) > 8 else "  " + " ".join(names)
            print(f"  足 {layer:<8} 最終日 {d}: {len(names):>3} 銘柄{tail}")
        late = [s for s, d in ends.items() if d is None or d < want]
        if late:
            print(f"  ⚠ {layer}: 前の営業日 {want} に届いていない: {' '.join(late)}")
            rc = 2
        if len(by) > 1:
            print(f"  ⚠ {layer}: 最終日が銘柄で割れている")
            rc = 2

    f = config.experiment(args.experiment).get("features", {})
    slack = int(f.get("ex_lag_days", 1)) + int(f.get("ex_max_stale_days", 7))
    zero = set(f.get("ex_zero_fill", []))
    for block in config.dataset(args.exog).get("series", []):
        src = block["source"]
        directory = store.series_dir(src)
        ends = {sid: last_date(store.path_of(directory, sid)) for sid in store.symbols_in(directory)}
        if not ends:
            print(f"  ⚠ 外部 {src}: 1 本も無い")
            rc = 2
            continue
        oldest, newest = min(ends.values()), max(ends.values())
        age = (today - oldest).days
        note = ""
        if src in zero:
            note = "（行が無い日 ＝ 0 件。最終日は最後に事象があった日）"
        elif age > slack:
            note = f"  ⚠ 今日から {age} 日前 ＞ ずらし ＋ 引き継ぎの上限 {slack} 日 ＝ `ex_` の列が欠損になる"
            rc = 2
        print(f"  外部 {src:<9} {len(ends):>2} 本  最終日 {oldest} 〜 {newest}{note}")
    print("→ 揃っている" if rc == 0 else "→ ⚠ 揃っていない（rc=2）")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
