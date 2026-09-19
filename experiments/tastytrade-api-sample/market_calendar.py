"""NYSE の営業日の暦（休場日・半日立会）。管理画面と執行器が同じものを使う。標準ライブラリだけ。

正本は同じディレクトリの `nyse_calendar.py`（【公表値】NYSE。出典と取得日はファイルの頭）。

⚠ **載っていない年は「平日＝営業日」に戻る**。黙って戻さないために、呼ぶ側は `covered()` を見て「仮」と出す。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN_ET = time(9, 30)
CLOSE_ET = time(16, 0)


@dataclass(frozen=True)
class Calendar:
    source: str
    fetched: str
    years: frozenset[int]
    holidays: frozenset[date]
    early_closes: frozenset[date]
    early_close_et: time

    # ---------------- 暦が答えられる範囲

    def covered(self, d: date) -> bool:
        """その年の休場日が載っているか。False なら `is_trading_day` は平日かどうかしか見ていない。"""
        return d.year in self.years

    @property
    def last_covered(self) -> date:
        return date(max(self.years), 12, 31)

    def days_left(self, today: date) -> int:
        """暦の終わりまでの残り日数（負なら切れている）。年に 1 度の更新に気づくため。"""
        return (self.last_covered - today).days

    # ---------------- 営業日

    def is_holiday(self, d: date) -> bool:
        return d in self.holidays

    def is_early_close(self, d: date) -> bool:
        return d in self.early_closes

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def close_et(self, d: date) -> time:
        return self.early_close_et if d in self.early_closes else CLOSE_ET

    def is_open(self, dt: datetime) -> bool:
        """通常取引の時間内か（9:30〜引け。半日立会は 13:00 まで）。dt は tz つき。"""
        et = dt.astimezone(ET)
        return self.is_trading_day(et.date()) and OPEN_ET <= et.time() < self.close_et(et.date())

    def trading_days(self, first: date, last: date) -> list[date]:
        out, d = [], first
        while d <= last:
            if self.is_trading_day(d):
                out.append(d)
            d += timedelta(days=1)
        return out


def _dates(values) -> frozenset[date]:
    return frozenset(date.fromisoformat(v) for v in values)


def load(raw=None) -> Calendar:
    """`nyse_calendar`（か、同じ名前の属性を持つもの）から暦を組む。"""
    if raw is None:
        import nyse_calendar as raw
    hh, mm = raw.EARLY_CLOSE_ET.split(":")
    return Calendar(
        source=raw.SOURCE,
        fetched=raw.FETCHED,
        years=frozenset(int(y) for y in raw.YEARS),
        holidays=_dates(raw.HOLIDAYS),
        early_closes=_dates(raw.EARLY_CLOSES),
        early_close_et=time(int(hh), int(mm)),
    )


@lru_cache(maxsize=1)
def nyse() -> Calendar:
    return load()
