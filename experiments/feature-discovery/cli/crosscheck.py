"""⚠ **検証で見た値と運用で見る値が同じかを確かめる。**

    python3 -m cli.crosscheck --day 2026-09-14

⚠ **検証は保管庫（IEM）、運用は配信（NWS の API）から取る**（[記録 §11-3](../../../docs/specs/experiments/daily-data-sources.md)）。
⚠ **2 経路を使うなら、同じ日を両方から取って突き合わせないと、
検証の数字と運用の数字が別物になっていても気づけない。**

⚠ **1〜2 日前の日なら事象の単位で一致する**【実測 2026-09-16。9/13〜9/15 の 118 事象が発表の時刻まで一致】。
⚠ **NWS の API はメッセージを 7 日しか持たない**ので、⚠ **消えかけの日は API 側が欠ける**。
⚠ **川の洪水（`FL`）は始まりの時刻が延長で動き、どちらの経路でも日がぶれる。** ⚠ **差がどちら向きかまで見て判断する。**
"""

from __future__ import annotations

import argparse
import json
import os

import pandas as pd

from ail.data import store
from ail.data.sources import iem, nws
import ail.bootstrap  # noqa: F401

IDS = ["national", "category", "region", "region_category"]


def _counts(out: dict[str, pd.DataFrame], day: str) -> dict[str, float]:
    ms = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
    got = {}
    for sid, df in out.items():
        hit = df[df["time_ms"] == ms]
        if len(hit):
            got[sid] = float(hit["value"].iloc[0])
    return got


def compare(day: str) -> dict:
    a = pd.Timestamp(day, tz="UTC")
    b = a + pd.Timedelta(days=1)
    # ⚠ **配信側は窓を前後に広げて取り、始まりの日で絞る。** ⚠ **その日に始まった事象の `NEW` は
    # 前日以前に出ていることがある**（川の洪水は 3 日前に予告された。`TBW FL 2` 実測 2026-09-16）
    lo = a - pd.Timedelta(days=3)
    hi = min(b + pd.Timedelta(days=1), pd.Timestamp.now(tz="UTC"))
    print(f"NWS の API（配信）を取る: {day}（窓 {lo.date()} 〜 {hi:%Y-%m-%d %H:%M}Z）", flush=True)
    api = _counts(nws.fetch(IDS, start=lo.isoformat(), end=hi.isoformat()), day)
    print(f"IEM の保管庫（検証）を取る: {day}", flush=True)
    arc = _counts(iem.fetch(IDS, start=str(a.date()), end=str(b.date()), crawl_delay=0.0), day)

    sids = sorted(set(api) | set(arc))
    rows = [{"系列": s, "配信(NWS)": api.get(s, 0.0), "保管庫(IEM)": arc.get(s, 0.0),
             "差": api.get(s, 0.0) - arc.get(s, 0.0)} for s in sids]
    same = sum(1 for r in rows if r["差"] == 0)
    doc = {"day": day, "series": len(rows), "同じ": same, "違う": len(rows) - same,
           "配信の合計": sum(api.values()), "保管庫の合計": sum(arc.values()), "行": rows}
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True,
                    help="UTC の日（YYYY-MM-DD）。⚠ 配信側は 3 日前から取るので、4 日以内の日が確実"
                         "（API は 7〜14 日で消える）")
    ap.add_argument("--out", default=os.path.join(store.ROOT, "out"))
    args = ap.parse_args()

    doc = compare(args.day)
    df = pd.DataFrame(doc["行"])
    print()
    print(df.to_string(index=False) if len(df) else "⚠ どちらの経路も 0 件だった")
    print(f"\n系列 {doc['series']} 本 / ⚠ **一致 {doc['同じ']} ・ 不一致 {doc['違う']}**"
          f" ／ 合計 配信 {doc['配信の合計']:.0f} 対 保管庫 {doc['保管庫の合計']:.0f}")
    if doc["違う"]:
        print("⚠ **不一致がある。** ⚠ API は 7〜14 日で消えるので、"
              "⚠ **配信 < 保管庫 なら「消えかけ」、配信 > 保管庫 なら「保管庫の遅れ」を疑う**")
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"crosscheck_{args.day}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"→ {os.path.relpath(path, store.ROOT)}")


if __name__ == "__main__":
    main()
