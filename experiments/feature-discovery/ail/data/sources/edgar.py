"""決算（SEC EDGAR）。⚠ **鍵が要らない**（米政府の公開データ）。規約は rules.md 26 章（2026-10-19）。

⚠ **User-Agent に連絡先が要る**（SEC の決まり）＝ 環境変数 `AIL_EDGAR_CONTACT="<名前> <メール>"`。
   リポジトリに書かない・無ければ取りに行かない（E2 ＝ 利用者が決める）。
⚠ **毎秒 10 回まで**（ここでは 0.25 秒あける）。失敗しても叩き直さない。

置くもの（1 銘柄 3 系列。時刻 ＝ **提出日** の 00:00 UTC ＝ 層は翌日からしか貼らない）:
  ANN_<銘柄>     8-K の Item 2.02（決算発表）の提出日に 1
  EPSYOY_<銘柄>  10-Q ／ 10-K の希薄化後 EPS の前年同期比（対称な変化率）
  REVYOY_<銘柄>  売上の前年同期比（同）
⚠ 同じ期（start・end）の値が複数あれば **最初に提出された値**（後の修正・比較表示は使わない ＝ leak の 2）。
⚠ 前年同期 ＝ 同じ種類（四半期 ／ 年度）で、期末が 358〜372 日前の期。無ければ行を置かない。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date

import pandas as pd

from ail import config
from ail.registry import register

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
CONTACT_ENV = "AIL_EDGAR_CONTACT"
SLEEP_S = 0.25
SERIES = ("ANN", "EPSYOY", "REVYOY")
EARNINGS_ITEM = "2.02"
EPS_TAGS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")
REV_TAGS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax")
EPS_UNIT, REV_UNIT = "USD/shares", "USD"
QUARTER_SPAN = (60, 120)       # 期間（end − start）の日数で四半期と年度を見分ける（事前固定）
YEAR_SPAN = (340, 380)
PRIOR_YEAR_SPAN = (358, 372)   # 前年同期 ＝ 期末がこの日数だけ前の期


def contact() -> str:
    c = os.environ.get(CONTACT_ENV, "").strip()
    if not c:
        raise SystemExit(f"⚠ EDGAR の User-Agent に書く連絡先が無い（環境変数 {CONTACT_ENV}=\"<名前> <メール>\"。rules.md 26-1）")
    return c


def user_agent(c: str) -> str:
    return f"ai-income-lab research {c}"


def _get(url: str, ua: str, timeout: float = 60) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def sec_ticker(symbol: str) -> str:
    """`BRK/B` → `BRK-B`（SEC の一覧の綴り）。"""
    return symbol.replace("/", "-").upper()


def cik_map(doc: dict) -> dict[str, int]:
    return {str(v["ticker"]).upper(): int(v["cik_str"]) for v in doc.values() if "ticker" in v and "cik_str" in v}


def day_ms(d: str) -> int:
    return int(pd.Timestamp(d, tz="UTC").value // 1_000_000)


def announcements(docs: list[dict], start: str) -> list[str]:
    """8-K の Item 2.02 の提出日（YYYY-MM-DD・重複なし・昇順）。`docs` ＝ submissions の本体と古い頁（形は同じ）。"""
    out: set[str] = set()
    for doc in docs:
        rec = (doc.get("filings") or {}).get("recent") if isinstance(doc.get("filings"), dict) else None
        rec = rec if rec is not None else doc
        for form, items, d in zip(rec.get("form") or [], rec.get("items") or [], rec.get("filingDate") or []):
            if form in ("8-K", "8-K/A") and d and d >= start \
                    and EARNINGS_ITEM in [x.strip() for x in str(items or "").split(",")]:
                out.add(d)
    return sorted(out)


def entries_of(facts: dict, tags, unit: str, start: str = "1900-01-01") -> tuple[str | None, list[dict]]:
    """companyfacts から、`tags` のうち **`start` 以降に提出された行が最も多い tag** の単位 `unit` の値の一覧（同数なら `tags` の順）。
    ⚠ 2026-10-19 に「最初に見つかった tag」から改めた（回す前・取得の直後。`Revenues` が 2018 年より前の行しか持たない会社が
    13 社・15 行以下が 23 社あり、売上の系列が落ちていた。tag の集合は足していない ＝ rules.md 26-3）。"""
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    best: tuple[str | None, list[dict], int] = (None, [], -1)
    for tag in tags:
        ents = list(((gaap.get(tag) or {}).get("units") or {}).get(unit) or [])
        n = sum(1 for e in ents if str(e.get("filed") or "") >= start)
        if ents and n > best[2]:
            best = (tag, ents, n)
    return best[0], best[1]


def span_kind(e: dict) -> str | None:
    try:
        n = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
    except (KeyError, TypeError, ValueError):
        return None
    if QUARTER_SPAN[0] <= n <= QUARTER_SPAN[1]:
        return "Q"
    if YEAR_SPAN[0] <= n <= YEAR_SPAN[1]:
        return "Y"
    return None


def symmetric_change(cur: float, prev: float) -> float:
    """(今 − 前) ／ ((|今| ＋ |前|) ／ 2)。−2〜2 に収まる・手で置く閾値なし。両方 0 なら 0。"""
    denom = (abs(cur) + abs(prev)) / 2.0
    return 0.0 if denom == 0 else (cur - prev) / denom


def yoy_rows(entries: list[dict], start: str) -> list[tuple[str, float]]:
    """(提出日, 前年同期比) の一覧（昇順）。同じ期は最初に提出された値・前年同期は同じ種類で期末が 358〜372 日前。
    同じ提出日に四半期と年度があれば四半期を採る（10-K の中の Q4）。"""
    first: dict[tuple[str, str], tuple[str, float, str]] = {}
    for e in entries:
        kind = span_kind(e)
        filed, val = e.get("filed"), e.get("val")
        if kind is None or filed is None or val is None:
            continue
        key = (e["start"], e["end"])
        if key not in first or filed < first[key][0]:
            first[key] = (str(filed), float(val), kind)
    by_kind: dict[str, list[tuple[date, str, float]]] = {"Q": [], "Y": []}
    for (st, en), (filed, val, kind) in first.items():
        by_kind[kind].append((date.fromisoformat(en), filed, val))
    picked: dict[str, tuple[float, str]] = {}
    for kind, rows in by_kind.items():
        rows.sort()
        for end, filed, val in rows:
            if filed < start:
                continue
            prevs = [(abs((end - pe).days - 365), pv) for pe, _pf, pv in rows
                     if PRIOR_YEAR_SPAN[0] <= (end - pe).days <= PRIOR_YEAR_SPAN[1]]
            if not prevs:
                continue
            _, pv = min(prevs)
            cur = picked.get(filed)
            if cur is None or (kind == "Q" and cur[1] == "Y"):
                picked[filed] = (symmetric_change(val, pv), kind)
    return [(filed, v) for filed, (v, _k) in sorted(picked.items())]


def frame(rows) -> pd.DataFrame:
    """(日付, 値) → `time_ms, value`（同じ日は最後の値・昇順）。"""
    d: dict[int, float] = {}
    for day, v in rows:
        d[day_ms(day)] = float(v)
    return pd.DataFrame({"time_ms": sorted(d), "value": [d[k] for k in sorted(d)]})


def company_symbols(universes) -> list[str]:
    seen: list[str] = []
    for u in universes:
        for s in (config.universe(u).get("groups") or {}).get("company") or []:
            if s not in seen:
                seen.append(s)
    return seen


@register("source", "edgar")
def fetch(series: list[str], universes=("us63",), start: str = "2018-01-01", sleep_s: float = SLEEP_S, **_) -> dict[str, pd.DataFrame]:
    """会社ごとに submissions（8-K）と companyfacts（XBRL）を読み、3 系列を返す。ETF は会社の group に無いので読まない。"""
    ua = user_agent(contact())
    syms = company_symbols(universes)
    tick = cik_map(_get(TICKERS_URL, ua))
    out: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    tags_used: dict[str, str] = {}
    for s in syms:
        cik = tick.get(sec_ticker(s))
        if cik is None:
            missing.append(s)
            continue
        time.sleep(sleep_s)
        sub = _get(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"), ua)
        docs = [sub]
        for f in (sub.get("filings") or {}).get("files") or []:
            if str(f.get("filingTo") or "") >= start:
                time.sleep(sleep_s)
                docs.append(_get(SUBMISSIONS_URL.format(name=f["name"]), ua))
        ann = announcements(docs, start)
        time.sleep(sleep_s)
        try:
            facts = _get(FACTS_URL.format(cik=cik), ua)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            facts = {}
        if "ANN" in series and ann:
            out[f"ANN_{s}"] = frame([(d, 1.0) for d in ann])
        for key, tags, unit in (("EPSYOY", EPS_TAGS, EPS_UNIT), ("REVYOY", REV_TAGS, REV_UNIT)):
            if key not in series:
                continue
            tag, ents = entries_of(facts, tags, unit, start)
            rows = yoy_rows(ents, start)
            if rows:
                out[f"{key}_{s}"] = frame(rows)
                tags_used[f"{key}_{s}"] = str(tag)
        print(f"  {s:<6} 発表 {len(ann):>3} 件 ／ EPS {len(out.get(f'EPSYOY_{s}', [])):>3} ／ 売上 {len(out.get(f'REVYOY_{s}', [])):>3}"
              + (f"（{tags_used.get(f'EPSYOY_{s}', '—')} ／ {tags_used.get(f'REVYOY_{s}', '—')}）"), flush=True)
    if missing:
        print(f"  ⚠ CIK が引けない銘柄 {len(missing)} 本: {' '.join(missing)}", flush=True)
    return out
