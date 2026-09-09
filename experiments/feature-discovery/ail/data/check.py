"""不変条件の検査。⚠ **取得と調整のたびに自動で通す**（rules.md 5 章）。

    時刻が単調増加 ／ 重複なし ／ `high >= max(open,close)` ／ `low <= min(open,close)` ／
    価格 > 0 ／ 出来高 >= 0 ／ ⚠ NaN 番兵が残っていないこと ／ ⚠ 目盛りの断層が残っていないこと

⚠ **検査は「止める（fatal）」と「数える（warn）」に分ける。**
提供元の粗（OHLC の綻び 808 件）で全部止まると誰も回さなくなるので、
⚠ **止めるのは「その先の計算が嘘になるもの」だけ**にし、残りは件数を manifest に残す。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.contracts import BAR_COLUMNS, SERIES_COLUMNS

# ⚠ **止める**（先の計算が嘘になる）
FATAL = ("time_not_monotonic", "time_duplicated", "nonpositive_price",
         "negative_volume", "nan_in_bars")
# 数えるだけ（提供元の粗。件数を記録して先へ進む）
WARN = ("ohlc_inconsistent", "zero_volume", "scale_break")


def check_bars(df: pd.DataFrame, break_threshold: float = 0.25) -> dict[str, int]:
    """1 系列を検査し、{違反の名前: 件数} を返す。⚠ **例外は投げない**（判断は呼び手）。"""
    n = len(df)
    if n == 0:
        return {"empty": 1}
    t = df["time_ms"]
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    hi, lo = np.maximum(o, c), np.minimum(o, c)
    r = np.log(c.where(c > 0)).diff()

    return {
        "rows": n,
        "time_not_monotonic": int((t.diff().dropna() <= 0).sum()),
        "time_duplicated": int(t.duplicated().sum()),
        "nonpositive_price": int((df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()),
        "negative_volume": int((v < 0).sum()),
        "nan_in_bars": int(df[list(BAR_COLUMNS)].isna().sum().sum()),
        "ohlc_inconsistent": int(((h < hi - 1e-12) | (l > lo + 1e-12) | (h < l)).sum()),
        "zero_volume": int((v == 0).sum()),
        # ⚠ 調整の誤りが残っていないか。**調整後はここが 0 になるはず**（実際の暴落は除く）
        "scale_break": int((r.abs() > break_threshold).sum()),
    }


def check_series(df: pd.DataFrame) -> dict[str, int]:
    """外部系列（1 値の日次）の検査。⚠ **足と違って価格でも出来高でもないので、条件を分ける。**

    ⚠ **止めるのは「時刻が壊れている」ときだけ。** 値の中身（気温が負など）は正常なので止めない。
    """
    n = len(df)
    if n == 0:
        return {"empty": 1}
    t, v = df["time_ms"], df["value"]
    return {
        "rows": n,
        "time_not_monotonic": int((t.diff().dropna() <= 0).sum()),
        "time_duplicated": int(t.duplicated().sum()),
        "nan_in_bars": int(df[list(SERIES_COLUMNS)].isna().sum().sum()),
        # ⚠ 数えるだけ。⚠ **飛びは休日なので異常ではない**が、多すぎたら経路を疑う材料になる
        "gap_over_7d": int((t.diff().dropna() > 7 * 86_400_000).sum()),
        "zero_volume": 0, "negative_volume": 0, "nonpositive_price": 0, "ohlc_inconsistent": 0,
        "scale_break": int((v.astype(float).diff().abs() > 0).sum() * 0),   # 系列では判定しない
    }


def fatal_of(report: dict[str, int]) -> dict[str, int]:
    """⚠ **止めるべき違反だけを抜き出す。** 空なら先へ進んでよい。"""
    return {k: v for k, v in report.items() if k in FATAL and v}


def merge(reports: dict[str, dict[str, int]]) -> dict[str, int]:
    """銘柄ごとの検査結果を合計する（manifest に載せる形）。"""
    out: dict[str, int] = {}
    for rep in reports.values():
        for k, v in rep.items():
            out[k] = out.get(k, 0) + int(v)
    return out


def format_report(total: dict[str, int], per_symbol: dict[str, dict[str, int]]) -> str:
    lines = [f"検査 {len(per_symbol)} 銘柄 / {total.get('rows', 0):,} 行"]
    for k in FATAL:
        if total.get(k):
            who = sorted(s for s, r in per_symbol.items() if r.get(k))[:6]
            lines.append(f"  ⚠ 止める {k}: {total[k]} 件  {' '.join(who)}")
    for k in WARN:
        if total.get(k):
            who = sorted(s for s, r in per_symbol.items() if r.get(k))
            lines.append(f"  警告 {k}: {total[k]} 件 / {len(who)} 銘柄  {' '.join(who[:6])}"
                         + (" …" if len(who) > 6 else ""))
    if len(lines) == 1:
        lines.append("  違反なし")
    return "\n".join(lines)
