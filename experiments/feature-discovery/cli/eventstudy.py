"""発表日と株価の関連（event study）。

    python3 -m cli.eventstudy                          # FOMC × SPY（調整後の日足）
    python3 -m cli.eventstudy --symbol QQQ --seed 7

⚠ **仮説は「方向」ではなく「分布が変わる」**（発表日はボラが上がる）。だから見るのは
平均リターンと **平均 |リターン|** の両方で、判定は |リターン| 側で行う。

⚠ **「発表日 vs 全日」では曜日効果と区別できない。** FOMC の発表はほぼ水曜（実測 65/68）なので、
⚠ **対照は「同じ曜日の非発表日」から、発表日と同じ曜日構成で引く**（並べ替え検定）。

⚠ **未来の予定は使わない**（暦には残っているが、足がある日だけが対象になる）。
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from ail.data import store

BP = 1e4
DRAWS = 10_000


def returns_of(symbol: str) -> pd.Series:
    """調整後の日足 → 終値の対数リターン（bp）。index は UTC の日付。"""
    df = store.read_bars(store.adjusted_dir("d"), symbol)
    idx = pd.to_datetime(df["time_ms"], unit="ms", utc=True).dt.normalize()
    close = pd.Series(df["close"].values, index=idx)
    return (np.log(close).diff() * BP).dropna()


def event_days(calendar: pd.Series, index: pd.DatetimeIndex, offset: int) -> pd.DatetimeIndex:
    """暦の日を取引日に写し、`offset` 取引日ずらす。⚠ **足の無い日（休場・未来）は落とす。**"""
    pos = index.get_indexer(calendar, method=None)
    pos = pos[pos >= 0] + offset
    pos = pos[(pos >= 0) & (pos < len(index))]
    return index[np.unique(pos)]


def matched_permutation(r: pd.Series, days: pd.DatetimeIndex, pool: pd.DatetimeIndex,
                        seed: int) -> dict:
    """⚠ **曜日構成をそろえた並べ替え検定。** 対照は pool から、days と同じ曜日の数だけ引く。

    返り値: 平均（両側 p）と 平均 |r|（片側 p: 発表日のほうが大きい）。
    """
    obs_mean = float(r[days].mean())
    obs_abs = float(r[days].abs().mean())
    by_wd = {wd: pool[pool.dayofweek == wd] for wd in set(days.dayofweek)}
    counts = {wd: int((days.dayofweek == wd).sum()) for wd in by_wd}
    for wd, cand in by_wd.items():
        if len(cand) < counts[wd]:
            return {"mean_bp": obs_mean, "abs_bp": obs_abs, "p_mean": None, "p_abs": None}

    rng = np.random.default_rng(seed)
    means = np.empty(DRAWS)
    absmeans = np.empty(DRAWS)
    values = {wd: r[by_wd[wd]].values for wd in by_wd}
    for i in range(DRAWS):
        take = np.concatenate([rng.choice(values[wd], size=counts[wd], replace=False)
                               for wd in by_wd])
        means[i] = take.mean()
        absmeans[i] = np.abs(take).mean()
    return {
        "mean_bp": obs_mean, "abs_bp": obs_abs,
        "ctrl_mean_bp": float(means.mean()), "ctrl_abs_bp": float(absmeans.mean()),
        # ⚠ +1 は「観測自体を 1 標本に数える」保守的な形（p が 0 にならない）
        "p_mean": float((np.sum(np.abs(means) >= abs(obs_mean)) + 1) / (DRAWS + 1)),
        "p_abs": float((np.sum(absmeans >= obs_abs) + 1) / (DRAWS + 1)),
    }


def run(symbol: str, series_id: str, source: str, start: str, seed: int) -> list[dict]:
    r = returns_of(symbol)
    r = r[r.index >= pd.Timestamp(start, tz="UTC")]
    cal = store.read_series(store.series_dir(source), series_id)
    days0 = pd.to_datetime(cal["time_ms"], unit="ms", utc=True).dt.normalize()

    sets = {off: event_days(pd.DatetimeIndex(days0), r.index, off) for off in (-1, 0, 1)}
    tainted = sets[-1].union(sets[0]).union(sets[1])
    pool = r.index.difference(tainted)      # ⚠ 対照から発表の前後 1 日も抜く（にじみを混ぜない）

    rows = []
    label = {-1: "前日", 0: "発表日", 1: "翌日"}
    for off in (-1, 0, 1):
        days = sets[off]
        rows.append({"日": label[off], "n": len(days),
                     **matched_permutation(r, days, pool, seed * 10 + off + 1)})
    rows.append({"日": "その他", "n": len(pool),
                 "mean_bp": float(r[pool].mean()), "abs_bp": float(r[pool].abs().mean()),
                 "p_mean": None, "p_abs": None})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="発表日と株価の関連（event study）")
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--source", default="fomc")
    ap.add_argument("--series", default="FOMC_DECISION")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rows = run(args.symbol, args.series, args.source, args.start, args.seed)
    print(f"\n{args.series} × {args.symbol}（調整後の日足・{args.start}〜・終値の対数リターン bp・"
          f"並べ替え {DRAWS:,} 回・seed {args.seed}）")
    print(f"{'日':　<4}{'n':>6}{'平均':>9}{'平均|r|':>9}{'対照の平均':>11}{'対照の|r|':>10}"
          f"{'p(平均)':>9}{'p(|r|)':>9}")
    for row in rows:
        p1 = "—" if row.get("p_mean") is None else f"{row['p_mean']:.4f}"
        p2 = "—" if row.get("p_abs") is None else f"{row['p_abs']:.4f}"
        c1 = f"{row['ctrl_mean_bp']:+9.2f}" if "ctrl_mean_bp" in row else " " * 9 + "—"
        c2 = f"{row['ctrl_abs_bp']:9.2f}" if "ctrl_abs_bp" in row else " " * 8 + "—"
        print(f"{row['日']:　<4}{row['n']:>6}{row['mean_bp']:+9.2f}{row['abs_bp']:9.2f}"
              f"{c1:>11}{c2:>10}{p1:>9}{p2:>9}")
    print("⚠ 対照は「同じ曜日の非発表日」から曜日構成をそろえて引いたもの。前後 1 日は対照から除外。")


if __name__ == "__main__":
    main()
