"""`cal` 層 — ⚠ **暦の列（曜日 ＋ 前の足からの日数）**。利用者の指示（2026-09-17）「データに曜日を含めたものを検証する」
（[プラン](../../../../docs/plans/archive/weekday-feature.md)）。

⚠ **config の `feature_layers` に書いたときだけ表に入る**（既定の表は 1 ビットも変わらない）。
⚠ **接頭辞は `cal_`**（`own_` にしない: 値は全銘柄で同じで、その銘柄の履歴から作っていない。`contracts.FEATURE_PREFIXES`）。

⚠ **足 i の列は足 i の日付だけで決まる。** ラベル `y` は足 i の終値 → 足 i+1 の終値なので、
   `cal_dow_fri` ＝ 1 の行が「週末をまたぐリターン」を当てる行になる（いわゆる月曜効果は金曜の行に出る）。
⚠ **次の足までの日数（連休の前か）は入れない。** 暦からは前もって分かる量だが、表の上では `ts.shift(-1)` でしか作れず、
   「`shift(-k)` は 1 つも使わない」（rules.md 7 章）を破る。NYSE の暦（`tastytrade-api-sample/nyse_calendar.py`）は 2026 年からしか無い。
⚠ **数値は 1 つも手で置かない**（窓も閾値も無い。rules.md 14-9）。
"""

from __future__ import annotations

import pandas as pd

from ail.registry import register

PREFIX = "cal_"
DAYS = ("mon", "tue", "wed", "thu", "fri")     # ⚠ 土日の足は日足には無い（あっても 5 列とも 0 になるだけ）
COLUMNS = tuple(f"{PREFIX}dow_{d}" for d in DAYS) + (f"{PREFIX}gap_prev",)


def build_one(df: pd.DataFrame) -> pd.DataFrame:
    """1 銘柄の足から `cal` 層を作る。返り値は df と同じ行数・同じ並び。

    | 列 | 定義 |
    | --- | --- |
    | `cal_dow_mon` 〜 `cal_dow_fri` | 足 i の日付の曜日（one-hot）。⚠ 日足の `ts` は取引日の 00:00 UTC なので、そのまま曜日を読む |
    | `cal_gap_prev` | 前の足からの暦日数（ふつう 1・週明け 3・連休明け 4 以上）。⚠ **最初の行は NaN** |
    """
    ts = df["ts"]
    dow = ts.dt.dayofweek
    x = pd.DataFrame(index=df.index)
    for k, d in enumerate(DAYS):
        x[f"{PREFIX}dow_{d}"] = (dow == k).astype(float)
    x[f"{PREFIX}gap_prev"] = ts.diff().dt.total_seconds() / 86400.0
    return x


@register("feature", "cal")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    return {s: build_one(df) for s, df in panel.items()}
