"""Wikipedia の記事ごとの日次の閲覧数（登録簿 `20261112-wiki-pageview-layer`）。Wikimedia の Analytics API（pageviews per-article）。

1 銘柄 1 系列 `PV_<銘柄>`（値 ＝ その暦日の閲覧数。時刻 ＝ その日の 00:00 UTC）。
  - 記事名は `config/dataset/wiki_articles.toml` の対応表（銘柄 → 題名の一覧）。⚠ 閲覧数は **題名ごと** に数えられ、記事が改名されると
    系列が題名で割れる ＝ いまの題名 ＋ 前の題名を並べ、日ごとに足す（対応表は回す前に固定する）。
  - `en.wikipedia.org`・`all-access`・`agent = user`（機械の巡回を除いた閲覧）・`daily`。
⚠ 利用の決まり（2026-11-13 に読んだ）: データは CC0 1.0。全リクエストに連絡先つきの User-Agent が要る（無いと予告なしに止められうる）。
   1 本ずつ順に送る（前の応答を待ってから次・0.5 秒あける）。失敗しても叩き直さない（404 ＝ その題名に閲覧数が無い、は空として続ける）。
⚠ 連絡先は環境変数 `AIL_WIKIMEDIA_CONTACT`（取得のたびに利用者が自分で置く。リポジトリ・記録に書かない）。
⚠ 置き場は `data/raw/wikimedia/series/`（新しい置き場 ＝ 研究用の日足には触らない）。
"""

from __future__ import annotations

import json
import os
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from ail.registry import register

URL = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
       "{project}/all-access/user/{article}/{granularity}/{start}/{end}")
PROJECT = "en.wikipedia.org"
CONTACT_ENV = "AIL_WIKIMEDIA_CONTACT"
SLEEP_S = 0.5
ARTICLES = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "dataset", "wiki_articles.toml")


def contact() -> str:
    c = os.environ.get(CONTACT_ENV, "").strip()
    if not c:
        raise SystemExit(f"⚠ Wikimedia の User-Agent に書く連絡先が無い（環境変数 {CONTACT_ENV} に利用者が自分で置く）")
    if not c.isascii() or "<" in c or ">" in c:
        # HTTP のヘッダは半角の英数字だけ ＝ 見本の「<名前> <メール>」をそのまま渡すと 1 本も送れずに落ちる
        raise SystemExit(f"⚠ {CONTACT_ENV} は半角の英数字で、実際の名前と連絡先を書く（見本の「<名前> <メール>」のままでは送れない）")
    return c


def user_agent(c: str) -> str:
    return f"ai-income-lab-research/0.1 ({c}) python-urllib"


def articles() -> dict[str, list[str]]:
    with open(ARTICLES, "rb") as f:
        return {str(k): [str(x) for x in v] for k, v in tomllib.load(f)["articles"].items()}


def url_of(title: str, start: str, end: str, granularity: str = "daily") -> str:
    """`start` ／ `end` は YYYY-MM-DD。題名は空白を `_` にして URL の 1 部品として符号化する。"""
    art = urllib.parse.quote(title.replace(" ", "_"), safe="")
    return URL.format(project=PROJECT, article=art, granularity=granularity,
                      start=start.replace("-", "") + "00", end=end.replace("-", "") + "00")


def parse(doc: dict) -> dict[str, float]:
    """応答 → {YYYY-MM-DD: 閲覧数}。"""
    out: dict[str, float] = {}
    for it in doc.get("items") or []:
        t = str(it.get("timestamp") or "")
        if len(t) >= 8:
            out[f"{t[:4]}-{t[4:6]}-{t[6:8]}"] = float(it.get("views") or 0.0)
    return out


def get_views(title: str, start: str, end: str, ua: str, granularity: str = "daily", timeout: float = 60) -> dict[str, float]:
    req = urllib.request.Request(url_of(title, start, end, granularity), headers={"User-Agent": ua, "Accept": "application/json"})
    try:
        return parse(json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:               # その題名 ・ 期間に閲覧数が無い
            return {}
        raise


def merged(parts: list[dict[str, float]]) -> dict[str, float]:
    """題名ごとの {日: 閲覧数} を日ごとに足す。"""
    out: dict[str, float] = {}
    for p in parts:
        for d, v in p.items():
            out[d] = out.get(d, 0.0) + v
    return out


@register("source", "wikimedia")
def fetch(series: list[str], universes=("us63",), start: str = "2017-10-01", end: str = "2026-09-09",
          sleep_s: float = SLEEP_S, **_) -> dict[str, pd.DataFrame]:
    """対応表の銘柄ごとに、題名の一覧の閲覧数を取り、日ごとに足した 1 系列を返す。対応表に無い銘柄（ETF）は読まない。"""
    ua = user_agent(contact())
    out: dict[str, pd.DataFrame] = {}
    for s, titles in articles().items():
        parts = []
        for t in titles:
            time.sleep(sleep_s)
            parts.append(get_views(t, start, end, ua))
        m = merged(parts)
        if "PV" in series and m:
            days = sorted(m)
            out[f"PV_{s}"] = pd.DataFrame({"time_ms": [int(pd.Timestamp(d, tz="UTC").value // 1_000_000) for d in days],
                                           "value": [m[d] for d in days]})
        print(f"  {s:<6} {len(m):>5} 日 ／ 題名ごと（日数 ・ 1 日の平均）: "
              + " ・ ".join(f"{t} {len(p)} {int(sum(p.values()) / max(len(p), 1))}" for t, p in zip(titles, parts)), flush=True)
    return out
