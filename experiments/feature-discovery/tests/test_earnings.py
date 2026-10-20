"""決算の層 `ern_`（SEC EDGAR。rules.md 26 章・記録 earnings-layer.md）。ネットワークを使わない。

⚠ **落としたいのは 6 つ。**
  1. 連絡先が無ければ取りに行かない（26-1）
  2. 発表日は 8-K の Item 2.02 だけ（8-K/A も。古い頁も読む・start より前は捨てる）
  3. 前年同期比は「最初に提出された値」で、同じ種類の期末 358〜372 日前と比べる対称な変化率。同じ提出日の四半期と年度は四半期
  4. 層は提出日の翌日からしか貼らない（lag ≥ 1・未来を切っても前の行は変わらない）・古い値は欠損・系列の無い銘柄は欠損
  5. `fetch` が偽の HTTP で 3 系列を組み立てる（CIK の綴り・tag の順）
  6. 登録と並び（`im` の後・`cal` の前）・接頭辞
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import contracts, registry
from ail.data.sources import edgar
from ail.features import earnings
from cli import build
import ail.bootstrap  # noqa: F401

SUB = {"cik": 732717, "filings": {
    "recent": {"form": ["8-K", "8-K", "4", "8-K/A", "8-K"], "items": ["2.02,9.01", "5.02", "", "2.02", "2.02,7.01"],
               "filingDate": ["2025-04-23", "2025-03-01", "2025-02-02", "2025-01-29", "2017-12-01"]},
    "files": [{"name": "CIK0000732717-submissions-001.json", "filingFrom": "2016-01-01", "filingTo": "2024-12-31"}]}}
SUB_OLD = {"form": ["8-K", "8-K"], "items": ["2.02", "2.02"], "filingDate": ["2024-10-23", "2024-10-23"]}


def _e(start, end, filed, val, fp="Q1", form="10-Q"):
    return {"start": start, "end": end, "filed": filed, "val": val, "fy": int(filed[:4]), "fp": fp, "form": form}


FACTS = {"facts": {"us-gaap": {
    "EarningsPerShareDiluted": {"units": {"USD/shares": [
        _e("2024-01-01", "2024-03-31", "2024-04-25", 1.0),                      # 2024Q1（最初の提出）
        _e("2024-01-01", "2024-03-31", "2025-04-24", 1.2),                      # 同じ期の後の提出（比較表示・修正）＝ 使わない
        _e("2025-01-01", "2025-03-31", "2025-04-24", 1.5),                      # 2025Q1 → (1.5 − 1.0) ／ 1.25 ＝ 0.4
        _e("2024-01-01", "2024-12-31", "2025-02-15", 4.0, fp="FY", form="10-K"),  # FY2024
        _e("2025-01-01", "2025-12-31", "2026-02-14", 5.0, fp="FY", form="10-K"),  # FY2025 → 1 ／ 4.5
        _e("2024-10-01", "2024-12-31", "2025-02-15", 1.1, fp="FY", form="10-K"),  # 2024Q4（10-K の中）
        _e("2025-10-01", "2025-12-31", "2026-02-14", 0.9, fp="FY", form="10-K"),  # 2025Q4 → 同じ提出日の年度より四半期
        {"end": "2025-06-30", "filed": "2025-07-24", "val": 2.0, "fy": 2025, "fp": "Q2"},   # start 無し ＝ 捨てる
    ]}},
    "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
        _e("2024-01-01", "2024-03-31", "2024-04-25", 100.0),
        _e("2025-01-01", "2025-03-31", "2025-04-24", 120.0),
    ]}},
}}}
TICKERS = {"0": {"cik_str": 732717, "ticker": "T", "title": "AT&T"}, "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire"}}


def test_contact_is_required(monkeypatch):
    monkeypatch.delenv(edgar.CONTACT_ENV, raising=False)
    with pytest.raises(SystemExit, match="連絡先"):
        edgar.contact()
    monkeypatch.setenv(edgar.CONTACT_ENV, "Name mail@example.com")
    assert edgar.user_agent(edgar.contact()).endswith("mail@example.com")


def test_announcements_are_item_202_only_including_old_pages():
    assert edgar.announcements([SUB, SUB_OLD], "2018-01-01") == ["2024-10-23", "2025-01-29", "2025-04-23"]
    assert edgar.announcements([SUB], "2025-04-01") == ["2025-04-23"]


def test_yoy_uses_first_filed_value_and_symmetric_change():
    tag, ents = edgar.entries_of(FACTS, edgar.EPS_TAGS, edgar.EPS_UNIT)
    assert tag == "EarningsPerShareDiluted" and len(ents) == 8
    rows = edgar.yoy_rows(ents, "2018-01-01")
    assert rows[0] == ("2025-04-24", pytest.approx(0.4))                       # 2024Q1 の後の提出（1.2）は使わない
    assert rows[1][0] == "2026-02-14" and rows[1][1] == pytest.approx(edgar.symmetric_change(0.9, 1.1))   # 四半期が年度に勝つ
    assert len(rows) == 2
    assert edgar.symmetric_change(0.0, 0.0) == 0.0 and edgar.symmetric_change(-1.0, 1.0) == -2.0
    tag, ents = edgar.entries_of(FACTS, edgar.REV_TAGS, edgar.REV_UNIT)
    assert tag == "RevenueFromContractWithCustomerExcludingAssessedTax"     # Revenues が無ければ次の tag
    assert edgar.yoy_rows(ents, "2018-01-01") == [("2025-04-24", pytest.approx(20 / 110))]
    assert edgar.yoy_rows(ents, "2025-05-01") == []                           # start より前の提出は置かない
    f = edgar.frame([("2025-04-24", 0.4), ("2025-04-24", 0.5), ("2024-04-25", 0.1)])
    assert list(f["value"]) == [0.1, 0.5] and f["time_ms"].is_monotonic_increasing


def test_fetch_builds_three_series_with_fake_http(monkeypatch):
    monkeypatch.setenv(edgar.CONTACT_ENV, "Name mail@example.com")
    monkeypatch.setattr(edgar.time, "sleep", lambda *_: None)
    seen = []
    def fake_get(url, ua, timeout=60):
        seen.append((url, ua))
        if url == edgar.TICKERS_URL:
            return TICKERS
        if url.endswith("companyfacts/CIK0000732717.json"):
            return FACTS
        if url.endswith("submissions/CIK0000732717.json"):
            return SUB
        if url.endswith("submissions-001.json"):
            return SUB_OLD
        raise AssertionError(url)
    monkeypatch.setattr(edgar, "_get", fake_get)
    monkeypatch.setattr(edgar.config, "universe", lambda name: {"groups": {"company": ["T"], "etf": ["SPY"]}})
    out = edgar.fetch(["ANN", "EPSYOY", "REVYOY"], universes=["x"], start="2018-01-01")
    assert set(out) == {"ANN_T", "EPSYOY_T", "REVYOY_T"}
    assert list(out["ANN_T"]["value"]) == [1.0, 1.0, 1.0] and out["ANN_T"]["time_ms"].iloc[0] == edgar.day_ms("2024-10-23")
    assert list(out["EPSYOY_T"]["time_ms"]) == [edgar.day_ms("2025-04-24"), edgar.day_ms("2026-02-14")]
    assert all(ua == "ai-income-lab research Name mail@example.com" for _, ua in seen)
    assert edgar.sec_ticker("BRK/B") == "BRK-B" and edgar.cik_map(TICKERS)["BRK-B"] == 1067983


# --- 層 -------------------------------------------------------------------

def _bars(dates):
    ts = pd.to_datetime(dates, utc=True)
    return pd.DataFrame({"ts": ts, "close": np.arange(len(ts), dtype=float)})


def _s(pairs):
    idx = pd.to_datetime([d for d, _ in pairs])
    return pd.Series([v for _, v in pairs], index=idx, dtype=float)


SERIES = {"ANN": _s([("2024-04-25", 1.0), ("2024-07-24", 1.0)]),
          "EPSYOY": _s([("2024-04-25", 0.4), ("2024-07-24", -0.2)]),
          "REVYOY": _s([("2024-04-25", 0.1)])}
DATES = ["2024-04-24", "2024-04-25", "2024-04-26", "2024-04-29", "2024-07-24", "2024-07-25", "2025-09-10"]


def test_layer_pastes_from_the_day_after_filing_and_marks_stale():
    x = earnings.build_one(_bars(DATES), SERIES)
    assert tuple(x.columns) == earnings.COLUMNS and len(x) == len(DATES)
    d = list(x["ern_days_since_ann"])
    assert np.isnan(d[0]) and np.isnan(d[1])                 # 提出日の当日は使わない（翌日から）
    assert d[2:6] == [0.0, 3.0, 89.0, 0.0]                   # 4/26 ＝ 前日の提出・4/29 ＝ 3 日・7/24 ＝ as-of 7/23 で 89 日・7/25 ＝ 0
    assert np.isnan(d[6])                                    # 400 日より古い ＝ 欠損
    assert list(x["ern_eps_yoy"].iloc[2:6]) == [0.4, 0.4, 0.4, -0.2] and np.isnan(x["ern_eps_yoy"].iloc[1])
    assert x["ern_rev_yoy"].iloc[2] == 0.1 and np.isnan(x["ern_rev_yoy"].iloc[6])
    # 長くずらせば、その分だけ遅れて貼る
    y = earnings.build_one(_bars(DATES), SERIES, lag_days=3)
    assert np.isnan(y["ern_days_since_ann"].iloc[2]) and y["ern_days_since_ann"].iloc[3] == 1.0


def test_no_lookahead_and_missing_series():
    full = earnings.build_one(_bars(DATES), SERIES)
    cut = {k: s[s.index < "2024-07-01"] for k, s in SERIES.items()}
    pd.testing.assert_frame_equal(earnings.build_one(_bars(DATES), cut).iloc[:4], full.iloc[:4])
    empty = earnings.build_one(_bars(DATES), {})
    assert empty.isna().all().all() and tuple(empty.columns) == earnings.COLUMNS


def test_layer_rejects_zero_lag_and_uses_the_loader(monkeypatch):
    monkeypatch.setattr(earnings, "load_symbol_series", lambda s: SERIES if s == "AAA" else {})
    out = earnings.layer({"AAA": _bars(DATES), "BBB": _bars(DATES)}, {})
    assert out["AAA"]["ern_eps_yoy"].iloc[2] == 0.4 and out["BBB"].isna().all().all()
    with pytest.raises(ValueError, match="1 以上"):
        earnings.layer({"AAA": _bars(DATES)}, {"ern_lag_days": 0})


def test_registered_between_im_and_cal_and_prefixed():
    assert registry.resolve("feature", "ern") is earnings.layer
    assert build.ORDER.index("ern") == build.ORDER.index("im") + 1 and build.ORDER[-1] == "cal"
    assert contracts.layer_of("ern_eps_yoy") == "ern_" and "ern_" in contracts.FEATURE_PREFIXES
    assert registry.resolve("source", "edgar") is edgar.fetch
