# 段 4: 上場廃止銘柄の日足は取れない — ⚠ **生存バイアスは有償データでしか消せない**【実測 2026-09-16】

⚠ **結論は 3 つある。**

1. ⚠ **instruments は廃止銘柄を知っている**（6/6・cert と prod の両方）。⚠ **`active=false` で機械的に区別できる。**
2. ⚠ **だが日足は 6 本とも 0 本。** 対照（AAPL・SPY）は同じ接続で 8,001 本ずつ返った ＝ ⚠ **API も資格情報も生きている。** ⚠ **この道は閉じる。**
3. ⚠ **消すには有償の point-in-time データが要る。** 要件（1990 年以降 ＋ 廃止銘柄 ＋ 過去の指数構成）に合うのは Norgate の Platinum 以上【公表値】で、⚠ **価格は US$630/年【推測・二次情報】。** ⚠ **契約はしない**（提案と試算で止める）。

プラン: [universe-delisted.md](../../plans/archive/universe-delisted.md) ／ 親: [universe-widening.md](universe-widening.md)（段 4）／ 記録した実行: `experiments/tastytrade-api-sample/out/tastytrade-both-20260916T052532Z.jsonl`
⚠ **これは「API の挙動」の実測**（[CLAUDE.md](../../../CLAUDE.md) の 2026-09-05 の例外）。⚠ **読み取りだけで、金銭は 1 円も動かしていない。**

> ⚠ **これは投資助言ではなく調査資料である。** 数字は【実測】【公表値】【推測】を明示する。

---

## 1. 何を引いたか — ⚠ **銘柄は結果を見る前に固定した**

⚠ **「取れた銘柄だけ」を後から並べると、取れる確率を過大に見せることになる**ので、8 本を先に決めた（プラン §0-1）。

| 銘柄 | 種別 | 廃止の事由 | 出典 |
| --- | --- | --- | --- |
| TWTR | 廃止 | NYSE 上場廃止 **2022-11-08**（買収・非公開化） | [SEC 8-K](https://www.sec.gov/Archives/edgar/data/1418091/000119312522244289/d403306dex991.htm)【公表値・取得 2026-09-15】 |
| ATVI | 廃止 | Nasdaq 上場廃止 **2023-10-13**（Microsoft の買収完了） | [Nasdaq Trader ECA2023-588](https://nasdaqtrader.com/TraderNews.aspx?id=ECA2023-588)【公表値・同】 |
| SIVB | 廃止 | FDIC 管財人・S&P 500 から **2023-03-15** 除外 | [S&P Global](https://press.spglobal.com/2023-03-10-Insulet-Set-to-Join-S-P-500)【公表値・同】 |
| FRC | 廃止 | FDIC 管財人（2023-05） | 【推測】（日付は測定に影響しない） |
| XLNX | 廃止 | AMD が買収（2022-02） | 【推測】 |
| CERN | 廃止 | Oracle が買収（2022-06） | 【推測】 |
| **AAPL** | ⚠ **対照** | 上場中 | — |
| **SPY** | ⚠ **対照** | 上場中の ETF | — |

---

## 2. instruments — ⚠ **知っている。しかも「もう取引できない」と分かる**

`GET /instruments/equities/{symbol}`。⚠ **16 回（8 銘柄 × 2 環境）とも HTTP 200。**

| 銘柄 | cert の `active` / `listed-market` | prod の `active` / `listed-market` | prod の `description` |
| --- | --- | --- | --- |
| TWTR | false / OTC | **false / XNYS** | TWITTER INC |
| ATVI | false / OTC | **false / XNAS** | （Activision Blizzard） |
| SIVB | false / OTC | **false / XNAS** | （SVB Financial） |
| FRC | false / OTC | **false / XNYS** | （First Republic） |
| XLNX | false / OTC | false / OTC | XILINX INC |
| CERN | false / OTC | false / OTC | （Cerner） |
| AAPL | **true / XNAS** | **true / XNAS** | Apple Inc. |
| SPY | **true / ARCX** | **true / ARCX** | （SPDR S&P 500 ETF） |

| # | 分かったこと |
| ---: | --- |
| 1 | ⚠ **`active` が false になっている**ので、⚠ **廃止銘柄を機械的にふるい落とせる**（銘柄集合を組むときの検査に使える） |
| 2 | ⚠ **廃止日を示す項目は無い。** 返る 24 項目は `active`・`borrow-rate`・`cusip`・`description`・`id`・`instrument-type`・`is-closing-only`・`is-etf`・`is-fractional-quantity-eligible`・`is-fraud-risk`・`is-illiquid`・`is-index`・`is-options-closing-only`・`lendability`・`listed-market`・`market-time-instrument-collection`・`option-tick-sizes`・`overnight-trading-permitted`・`pre-ipo`・`short-description`・`streamer-symbol`・`symbol`・`tick-sizes`【実測】 |
| 3 | ⚠ **廃止銘柄には `country-of-incorporation` と `country-of-taxation` が無い**（対照との差はこの 2 項目だけ） |
| 4 | ⚠ **cert と prod で中身が違う**: cert は 6 本とも `OTC` と言うが、prod は TWTR を XNYS・ATVI を XNAS と、⚠ **最後に上場していた市場**を返す。⚠ **cert の値を本番の値と読み替えない** |
| 5 | ⚠ **CUSIP が返る**（TWTR = 90184L102・XLNX = 983919101）ので、⚠ **ティッカーの再利用を突き合わせで検出できる**（プラン §6 の 2 の備え） |

---

## 3. 日足 — ⚠ **本題。0 本だった**

DXLink（`{=d}`・`fromTime` は 12,000 日前・45 秒待ち）。⚠ **認証も購読も成功している**（`AUTHORIZED` → `CHANNEL_OPENED` → `FEED_CONFIG` まで 0.7 秒）。

| 銘柄 | 種別 | ⚠ **本数** | 最古 | 最新 |
| --- | --- | ---: | --- | --- |
| TWTR ・ ATVI ・ SIVB ・ FRC ・ XLNX ・ CERN | 廃止 | ⚠ **0** | — | — |
| AAPL | 対照 | **8,001** | 1993-11-08 | 2026-09-15 |
| SPY | 対照 | **8,001** | 1993-11-08 | 2026-09-15 |

⚠ **対照が 2 本ともちょうど 8,001 本**なのは、⚠ **1 購読あたりの上限に当たっている可能性が高い**【推測】（`cli/fetch.py` が 12,000 日を要求しても 8,001 本で頭打ち ＝ 約 32 年ぶんの日足に相当するので、いまの検証期間には効かない）。
⚠ **「廃止銘柄は 0 本」と「対照は上限まで返る」が同じ接続で同時に起きている**ので、⚠ **配信側が上場中の銘柄しか持っていない**と読める【推測】。

---

## 4. 判定 — ⚠ **この道は閉じる**

| 問い | 答え |
| --- | --- |
| broker（tastytrade）から廃止銘柄の日足が取れるか | ⚠ **取れない**【実測】 |
| 廃止銘柄の存在と状態は取れるか | ✅ **取れる**（`active=false`・CUSIP・最後の上場市場） |
| 生存バイアスを消せるか | ⚠ **この経路では消せない。** ⚠ **価格が無ければ検証に使えない** |

⚠ **これで [rules.md 12 章 限界 1](feature-discovery/rules.md)（生存バイアス）は「消せない限界」として確定した** — ⚠ **無償の経路では、という条件つきで。**

⚠ **限界**: (a) ⚠ **1 社の挙動しか測っていない**（他社は 2026-09-04 に対象から外した。[trading-fee-comparison.md](../trading-fee-comparison.md)）／ (b) 8 銘柄だけ ／
(c) ⚠ **DXLink 以外の経路（REST のヒストリカル）は試していない** — 公式に日足の REST 経路は無いと見ている【推測】。

---

## 5. 有償データの試算 — ⚠ **提案と試算で止める（契約しない）**

⚠ **要件は 3 つ**: (1) ⚠ **廃止銘柄を含む日足**、(2) ⚠ **過去の指数構成**（当時の S&P 500 が分からないと集合を組めない）、(3) 1990 年代まで遡れること（1995 表に合わせる）。

| 提供元 | 要件 (1) | 要件 (2) | 要件 (3) | 価格 |
| --- | :-: | :-: | :-: | --- |
| **Norgate Data**（Silver / Gold / **Platinum** / Diamond） | ⚠ **Platinum 以上で含む**【公表値】 | ⚠ **Platinum 以上で「過去の指数構成」つき**【公表値】 | Platinum は **1990 年以降**・Diamond は **1950 年以降**【公表値】 | ⚠ **Silver US$270 ／ Gold US$360 ／ Platinum US$630 （年額）**【推測・二次情報】（[Alvarez Quant Trading](https://alvarezquanttrading.com/blog/norgate-data-review/)・[Enlightened Stock Trading](https://enlightenedstocktrading.com/norgate-data/)） |
| **Massive（旧 Polygon.io）** | ⚠ **明記なし**（`active=false` で廃止銘柄を引けるが、⚠ **過去データは「まばら」と評され推奨されないという二次情報**） | ⚠ **無し** | Advanced で **20 年以上** | **$0 ／ $29 ／ $79 ／ $199（月額）**【公表値・[massive.com/pricing](https://massive.com/pricing) 取得 2026-09-16】 |

【実測から計算】⚠ **要件を満たすのは Norgate の Platinum 以上だけ**で、⚠ **年額は US$630 前後【推測】＝ 月あたり約 US$53。**
⚠ **この金額は「検証のために払う額」であって、稼いだ額ではない。** ⚠ **いまの検証結果（採る 0 件）から見て、⚠ **先に払う理由は無い**。

| # | ⚠ 判断 |
| ---: | --- |
| 1 | ⚠ **いまは買わない。** 採用できる手法が 1 つも出ていない段階で、生存バイアスを消しても順位は変わらない（[ledger.md](feature-discovery/ledger.md) の「採る」は 0 件） |
| 2 | ⚠ **買うとしたら条件は 1 つ**: ⚠ **「採る」が出て、その手法の成績が生存バイアスで説明できるかを詰める段階になったとき** |
| 3 | ⚠ **無償で部分的に埋める道**（ティッカーの再利用の検出・`active=false` の除外）は ⚠ **今日の probe で手に入った** |

---

## 6. 検算

| # | 検算 | 結果 |
| ---: | --- | --- |
| 1 | ⚠ **対照（AAPL・SPY）が両環境で引け、日足も返る** | ✅（API と資格情報の問題ではない） |
| 2 | 記録にトークン・口座番号が出ていない | ✅ `record.py` の `Masker` を通している |
| 3 | 叩いた回数 | ✅ REST 16 回 ＋ quote token 1 回 ＋ DXLink 1 本（レート制限 60 回/分の内側） |
| 4 | ⚠ **結論が「取れない」でも応答をそのまま残した** | ✅ `out/tastytrade-both-20260916T052532Z.jsonl`（項目一覧・handshake・本数） |
| 5 | ⚠ **発注系に触れていない** | ✅ GET だけ。⚠ **`Client` に発注の鍵を 1 つも渡していない** |
