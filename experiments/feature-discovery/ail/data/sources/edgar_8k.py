"""SEC EDGAR の 8-K（臨時の開示）の提出日と項目（登録簿 `20261108-edgar-8k-event-flags`。rules.md 26 章の取得元 `edgar` の隣）。

1 銘柄 3 系列（値 ＝ その提出日の件数。時刻 ＝ **提出日** の 00:00 UTC）:
  `K8_<銘柄>`     … 8-K ／ 8-K/A の提出
  `K8I502_<銘柄>` … 項目 5.02（役員の異動）を含む提出
  `K8I202_<銘柄>` … 項目 2.02（決算の発表）を含む提出
⚠ 読むのは submissions の JSON だけ（`edgar` と同じ URL・同じ決まり ＝ User-Agent に連絡先・0.25 秒あける・失敗しても叩き直さない）。
⚠ 連絡先は環境変数 `AIL_EDGAR_CONTACT`（E2 ＝ 取得のたびに利用者から。リポジトリ・記録に書かない）。
⚠ 置き場は `data/raw/edgar_8k/series/`（新しい置き場 ＝ 研究用の日足にも `edgar` の系列にも触らない）。
"""

from __future__ import annotations

import time

import pandas as pd

from ail.data.sources import edgar
from ail.registry import register

SERIES = ("K8", "K8I502", "K8I202")
ITEMS = {"K8I502": "5.02", "K8I202": "2.02"}
FORMS = ("8-K", "8-K/A")


def filings(docs: list[dict], start: str) -> list[tuple[str, list[str]]]:
    """8-K ／ 8-K/A の (提出日, 項目の一覧)。`docs` ＝ submissions の本体と古い頁（形は同じ）。提出日の昇順。"""
    out: list[tuple[str, list[str]]] = []
    for doc in docs:
        rec = (doc.get("filings") or {}).get("recent") if isinstance(doc.get("filings"), dict) else None
        rec = rec if rec is not None else doc
        for form, items, d in zip(rec.get("form") or [], rec.get("items") or [], rec.get("filingDate") or []):
            if form in FORMS and d and d >= start:
                out.append((str(d), [x.strip() for x in str(items or "").split(",") if x.strip()]))
    return sorted(out)


def counts(rows: list[tuple[str, list[str]]]) -> dict[str, dict[str, float]]:
    """系列の頭 → {提出日: 件数}。"""
    out: dict[str, dict[str, float]] = {k: {} for k in SERIES}
    for d, items in rows:
        out["K8"][d] = out["K8"].get(d, 0.0) + 1.0
        for key, item in ITEMS.items():
            if item in items:
                out[key][d] = out[key].get(d, 0.0) + 1.0
    return out


@register("source", "edgar_8k")
def fetch(series: list[str], universes=("us63",), start: str = "2018-01-01", sleep_s: float = edgar.SLEEP_S, **_) -> dict[str, pd.DataFrame]:
    """会社ごとに submissions を読み、8-K の 3 系列を返す。ETF は会社の group に無いので読まない。"""
    ua = edgar.user_agent(edgar.contact())
    tick = edgar.cik_map(edgar._get(edgar.TICKERS_URL, ua))
    out: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for s in edgar.company_symbols(universes):
        cik = tick.get(edgar.sec_ticker(s))
        if cik is None:
            missing.append(s)
            continue
        time.sleep(sleep_s)
        sub = edgar._get(edgar.SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"), ua)
        docs = [sub]
        for f in (sub.get("filings") or {}).get("files") or []:
            if str(f.get("filingTo") or "") >= start:
                time.sleep(sleep_s)
                docs.append(edgar._get(edgar.SUBMISSIONS_URL.format(name=f["name"]), ua))
        c = counts(filings(docs, start))
        for key in SERIES:
            if key in series and c[key]:
                out[f"{key}_{s}"] = pd.DataFrame({"time_ms": [edgar.day_ms(d) for d in sorted(c[key])],
                                                  "value": [c[key][d] for d in sorted(c[key])]})
        print(f"  {s:<6} 8-K {int(sum(c['K8'].values())):>4} 件 ／ 5.02 {int(sum(c['K8I502'].values())):>3} ／ 2.02 {int(sum(c['K8I202'].values())):>3}", flush=True)
    if missing:
        print(f"  ⚠ CIK が引けない銘柄 {len(missing)} 本: {' '.join(missing)}", flush=True)
    return out
