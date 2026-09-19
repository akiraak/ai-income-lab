"""NYSE の休場日と半日立会（13:00 ET 引け）。読み手は market_calendar.py（管理画面と執行器が同じものを使う）。

【公表値】NYSE "Holidays & Trading Hours" https://www.nyse.com/markets/hours-calendars （取得 2026-09-18）

⚠ **手で写した**。写し間違いは dashboard/tests/test_market_calendar.py が規則（第 n 月曜・聖金曜日・土日の振替）から
   独立に計算した日付と突き合わせて拾う。
⚠ **年に 1 度、NYSE が次の年を公表したら足す**（YEARS に年を足し、HOLIDAYS / EARLY_CLOSES に日付を足す）。
   載っていない年は「平日＝営業日」に戻り、管理画面に「仮」の印が出る。
⚠ **データを .py に置くのは、g3plus のコンテナがこのディレクトリの `*.py` と `*.sh` しか COPY しないため**
   （dashboard.md §7 の契約。.toml にすると公開面に載らない）。
"""

SOURCE = "https://www.nyse.com/markets/hours-calendars"
FETCHED = "2026-09-18"
YEARS = [2026, 2027, 2028]          # ⚠ この年は「休場日が全部載っている」と読む
EARLY_CLOSE_ET = "13:00"            # 半日立会の引け（株）。オプションは 13:15

HOLIDAYS = [
  # 2026
  "2026-01-01",  # New Year's Day
  "2026-01-19",  # Martin Luther King, Jr. Day
  "2026-02-16",  # Washington's Birthday
  "2026-04-03",  # Good Friday
  "2026-05-25",  # Memorial Day
  "2026-06-19",  # Juneteenth
  "2026-07-03",  # Independence Day（土曜 → 金曜に振替）
  "2026-09-07",  # Labor Day
  "2026-11-26",  # Thanksgiving Day
  "2026-12-25",  # Christmas Day
  # 2027
  "2027-01-01",  # New Year's Day
  "2027-01-18",  # Martin Luther King, Jr. Day
  "2027-02-15",  # Washington's Birthday
  "2027-03-26",  # Good Friday
  "2027-05-31",  # Memorial Day
  "2027-06-18",  # Juneteenth（土曜 → 金曜に振替）
  "2027-07-05",  # Independence Day（日曜 → 月曜に振替）
  "2027-09-06",  # Labor Day
  "2027-11-25",  # Thanksgiving Day
  "2027-12-24",  # Christmas Day（土曜 → 金曜に振替）
  # 2028（⚠ 01-01 は土曜。NYSE は前年の金曜へ振り替えない ＝ New Year's Day の休場なし）
  "2028-01-17",  # Martin Luther King, Jr. Day
  "2028-02-21",  # Washington's Birthday
  "2028-04-14",  # Good Friday
  "2028-05-29",  # Memorial Day
  "2028-06-19",  # Juneteenth
  "2028-07-04",  # Independence Day
  "2028-09-04",  # Labor Day
  "2028-11-23",  # Thanksgiving Day
  "2028-12-25",  # Christmas Day
]

EARLY_CLOSES = [
  "2026-11-27",  # Thanksgiving の翌日
  "2026-12-24",  # Christmas の前日
  "2027-11-26",  # Thanksgiving の翌日
  "2028-07-03",  # Independence Day の前日
  "2028-11-24",  # Thanksgiving の翌日
]
