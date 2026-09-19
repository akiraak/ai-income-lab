"""NYSE の暦（experiments/tastytrade-api-sample/nyse_calendar.py）。手で写した日付を、規則から独立に計算した日付と突き合わせる。

⚠ 規則は NYSE Rule 7.2 の慣行（土曜の祝日は金曜へ・日曜は月曜へ。ただし 1 月 1 日が土曜なら振り替えない）。
   暦に年を足したときに写し間違いを拾うためのもので、正本は NYSE の公表（nyse_calendar.py の頭の出典）。
"""

from datetime import date, datetime, timedelta, timezone

import market_calendar as mc


def _nth(year, month, weekday, n):
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last(year, month, weekday):
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _observed(d, saturday_moves=True):
    if d.weekday() == 5:
        return d - timedelta(days=1) if saturday_moves else None
    return d + timedelta(days=1) if d.weekday() == 6 else d


def expected(year):
    hol = {
        _observed(date(year, 1, 1), saturday_moves=False),   # ⚠ 1 月 1 日が土曜なら休場なし（前年の 12-31 は月末の営業日）
        _nth(year, 1, 0, 3), _nth(year, 2, 0, 3), _easter(year) - timedelta(days=2), _last(year, 5, 0),
        _observed(date(year, 6, 19)), _observed(date(year, 7, 4)), _nth(year, 9, 0, 1), _nth(year, 11, 3, 4),
        _observed(date(year, 12, 25)),
    } - {None}
    early = {_nth(year, 11, 3, 4) + timedelta(days=1)}
    for d in (date(year, 7, 3), date(year, 12, 24)):
        if d.weekday() < 5 and d not in hol:
            early.add(d)
    return hol, early


def test_file_matches_the_rules_for_every_covered_year():
    cal = mc.nyse()
    assert cal.years, "対象年が空"
    for y in sorted(cal.years):
        hol, early = expected(y)
        assert {d for d in cal.holidays if d.year == y} == hol, y
        assert {d for d in cal.early_closes if d.year == y} == early, y
    assert all(d.year in cal.years for d in cal.holidays | cal.early_closes), "対象年の外の日付がある"
    assert all(d.weekday() < 5 for d in cal.holidays | cal.early_closes), "土日が混ざっている"
    assert not cal.holidays & cal.early_closes
    assert cal.source.startswith("https://www.nyse.com/") and cal.fetched


def test_trading_days_and_hours():
    cal = mc.nyse()
    assert not cal.is_trading_day(date(2026, 9, 7))            # Labor Day
    assert cal.is_trading_day(date(2026, 9, 8))
    assert not cal.is_trading_day(date(2026, 9, 12))           # 土曜
    assert [d.isoformat() for d in cal.trading_days(date(2026, 9, 4), date(2026, 9, 9))] == ["2026-09-04", "2026-09-08", "2026-09-09"]
    utc = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731
    assert cal.is_open(utc(2026, 9, 8, 14, 0))                 # 火曜 10:00 ET
    assert not cal.is_open(utc(2026, 9, 7, 14, 0))             # 休場日の 10:00 ET
    assert cal.is_open(utc(2026, 11, 27, 17, 59))              # 半日立会 12:59 ET
    assert not cal.is_open(utc(2026, 11, 27, 18, 0))           # 半日立会 13:00 ET（引け）
    assert cal.is_open(utc(2026, 11, 25, 20, 59))              # ふつうの日 15:59 ET


def test_years_outside_the_file_fall_back_to_weekdays_and_say_so():
    cal = mc.nyse()
    last = max(cal.years)
    assert cal.covered(date(last, 12, 31)) and not cal.covered(date(last + 1, 1, 1))
    assert cal.is_trading_day(date(last + 1, 1, 1)) == (date(last + 1, 1, 1).weekday() < 5)   # ⚠ 祝日でも平日なら営業日と答える
    assert cal.days_left(date(last, 12, 1)) == 30 and cal.days_left(date(last + 1, 1, 10)) < 0
