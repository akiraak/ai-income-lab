"""FOMC の暦の解析。⚠ **一番危ないのは月またぎと「予定でない会合」の混入。**

⚠ **月またぎ（`Jul/Aug 31-1`）を 1 つ目の月で読むと、発表日が 1 か月ずれる。**
⚠ **緊急会合・notation vote は事前に公表されない**ので、暦に混ぜると
「事前に知れる」という前提ごと壊れる（黙って混ぜず、落として数える）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ail.data.sources import fomc


def d(y, m, day):
    return datetime(y, m, day, tzinfo=timezone.utc)


CURRENT = """
<a id="1">2025 FOMC Meetings</a>
<div class="fomc-meeting__month"><strong>January</strong></div>
<div class="fomc-meeting__date">28-29</div>
<div class="fomc-meeting__month"><strong>Oct/Nov</strong></div>
<div class="fomc-meeting__date">31-1</div>
<div class="fomc-meeting__month"><strong>December</strong></div>
<div class="fomc-meeting__date">9-10*</div>
<div class="fomc-meeting__month"><strong>July</strong></div>
<div class="fomc-meeting__date">22 (notation vote)</div>
<a id="2">2026 FOMC Meetings</a>
<div class="fomc-meeting__month"><strong>March</strong></div>
<div class="fomc-meeting__date">17-18*</div>
"""

HISTORICAL = """
<h5>January 30-31 Meeting - 2018</h5>
<h5>Jul/Aug 31-1 Meeting - 2018</h5>
<h5>March 15 (unscheduled) Meeting - 2020</h5>
<h5>December 18-19 Meeting - 2018</h5>
"""


def test_current_page_reads_month_spans_and_drops_notation_votes():
    dates, skipped = fomc.parse_current(CURRENT)
    assert dates == [d(2025, 1, 29), d(2025, 11, 1), d(2025, 12, 10), d(2026, 3, 18)]
    assert skipped == 1                       # ⚠ notation vote は暦に入れない（落として数える）


def test_historical_page_reads_month_spans_and_drops_unscheduled():
    dates, skipped = fomc.parse_historical(HISTORICAL)
    assert dates == [d(2018, 1, 31), d(2018, 8, 1), d(2018, 12, 19)]
    assert skipped == 1                       # ⚠ 緊急会合は事前に公表されない


def test_end_date_is_the_second_day_of_the_second_month():
    """⚠ **発表日は最終日。** 月またぎは 2 つ目の月・2 つ目の日。"""
    assert fomc._end_date("Jul/Aug", "31-1", 2018) == d(2018, 8, 1)
    assert fomc._end_date("January", "28-29", 2025) == d(2025, 1, 29)
    assert fomc._end_date("December", "9-10*", 2025) == d(2025, 12, 10)
    assert fomc._end_date("March", "3", 2020) is None          # 単日（緊急）は通さない
    assert fomc._end_date("Smarch", "1-2", 2020) is None       # 知らない月は通さない
