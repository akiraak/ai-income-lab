# tastytrade API 取引サンプル

tastytrade の Open API を **6 手順**（認証 → 口座照会 → 現在値 → 指値・取消 → 約定・反対売買 → ストリーミング）で
動かし、無人運転の設計に効く 6 観点を【実測】として JSON Lines に記録する。

プラン: [docs/plans/tastytrade-api-sample.md](../../docs/plans/tastytrade-api-sample.md) ／
記録先: [docs/specs/experiments/tastytrade-api-sample.md](../../docs/specs/experiments/tastytrade-api-sample.md)

> ⚠ **金銭が動く操作は利用者が行う。** このコードは既定で sandbox（cert）にしか繋がらず、
> 本番環境では発注系（手順 4・5）を拒否する。鍵は 2 段:
> **dry-run**（注文をルーティングしない検証）は `--allow-prod-dry-run` の 1 本、
> **本発注**は `TT_ALLOW_PROD_ORDERS=1` と `--i-know-this-is-real-money` の **両方**。
> dry-run を許しても本発注は開かない。

## ファイル

| ファイル | 中身 |
| --- | --- |
| `sample.py` | 6 手順の本体。`--step` で個別実行 |
| `ttclient.py` | REST / websocket の薄いクライアント。本番ガードもここ |
| `record.py` | JSONL の書き出しとマスク |
| `mock_server.py` | 資格情報なしで配線を確かめるためのモック。**tastytrade の【実測】には使えない** |
| `test_record.py` | 記録とマスクのテスト（ネットワーク不要） |
| `selftest.sh` | モックを立てて 6 手順 ＋ 本番ガードを通す自己検査 |

## 0. 準備

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./selftest.sh            # 資格情報なしで配線を確認（モックに対して 6 手順を通す）
```

## 1. sandbox の資格情報を作る（利用者が行う）

ブラウザで [sandbox account tool](https://developer.tastytrade.com/docs/sandbox/tools/) を開き、上から順に:

| # | パネル | 入れるもの | 出てくるもの |
| --- | --- | --- | --- |
| 1 | Create a sandbox user | メール・ユーザー名（英小文字と数字のみ）・12 文字以上のパスワード | sandbox ユーザー |
| — | **メール確認** | 届いたリンクを踏む。**3 日以内**（過ぎると `unconfirmed_user`） | — |
| 2 | Sign in | 上のユーザー名とパスワード | ツール内のセッション |
| 3 | Create a customer record | 姓・名 | customer（これが無いと大半の API が通らない） |
| 4 | Create a funded account | Individual / Margin or Cash / 初期資金 | 口座番号と残高 |
| 6 | Create an OAuth application | 名前・redirect URI（完全な URI）・スコープ `read` `trade` `openid` | **client id / client secret** |
| 6 | 同じパネルの Create Grant | — | **refresh token** |

- **client secret は 1 回しか表示されない。** その場で `.env` に貼る
- sandbox ユーザーは tastytrade の本口座とは**別**。契約・入金・本人確認は伴わない
- ⚠ ページ末尾に「Account creation, funding and OAuth application management are coming next」という記述が
  残っているが、**パネルは実装済み**。ページの JS は下の順で `api.cert.tastyworks.com` を直接叩いている
  （2026-09-05 に `_next/static/chunks/app/docs/sandbox/tools/page-*.js` を読んで確認）

  ```
  POST /users                     {email, username, password}
  POST /sessions                  {login, password}            → session token
  POST /sandbox/customers         {first-name, last-name}
  POST /sandbox/customers/me/accounts {account-type, margin-or-cash, funding-amount}
  POST /sandbox/accounts/{acct}/deposits {amount}
  POST /users/me/oauth/clients    {name, redirect-uris, scopes}
  POST /users/me/oauth/grants     {client-id}                  → refresh token
  ```

  リクエストのキーは camelCase → kebab-case に変換して送られる。`Authorization` にはセッショントークンを
  そのまま（`Bearer` を付けずに）入れている。⚠ これは公式リファレンスに載っていない経路で、
  予告なく変わりうる

## 2. 本番の資格情報を作る（手順 3・6 の気配と Phase 6 のときだけ）

sandbox は**相場データを配信しない**（`/market-data`・`/market-metrics` が全経路 502）。
公式が勧めるのは「**本番で読み、sandbox に発注する**」形で、資格情報を 2 組持つ。

1. [my.tastytrade.com](https://my.tastytrade.com) → Manage → My Profile → API → OAuth Applications → **+ New OAuth client**
2. スコープ `read` `trade` `openid`。⚠ `read` と `trade` は **2FA 必須**（My Profile → Security）
3. 作った application の **Manage → Create Grant** で refresh token
4. `.env` の `TT_PROD_*` に入れる（このコードは本番の資格情報を**読み取りにしか使わない**）

⚠ 入金なしの本口座で `GET /api-quote-tokens` が通るかは公式文書に記載がない。
「fully onboarded customer」でないと `quote_streamer.customer_not_found_error` になる、とだけ書かれている。

## 3. 設定

```bash
cp .env.example .env    # 埋める。.env と out/ は git 管理外
```

## 4. 実行

```bash
.venv/bin/python sample.py --step all              # 1〜6 を順に（既定 60 秒のストリーミング込み）
.venv/bin/python sample.py --step 4                # 手順 4 だけ
.venv/bin/python sample.py --step rate             # レート制限の当たり方
.venv/bin/python sample.py --step 1 --verify-expiry # access token が 15 分で 401 になるかを実測
.venv/bin/python sample.py --step probe            # 入金前の本番口座で気配が取れるか（読み取りのみ）
```

```bash
.venv/bin/python sample.py --step dryrun --allow-prod-dry-run  # 本番で dry-run だけ（注文は出ない）
```

`--step probe` と `--step dryrun` は **`TT_PROD_*` だけで動く**（sandbox の準備を待たずに実行できる）。
本番口座に対して読み取りしか行わず、発注はしない。⚠ **入金が着く前に 1 回だけ**通す
（着金後は「入金なしで気配が取れるか」を二度と測れない）。

記録は `out/tastytrade-<env>-<実行時刻>.jsonl` に 1 手順 1 行。

- 手順 5（成行の約定）は **米国市場時間**（ET 9:30〜16:00 ＝ PT 6:30〜13:00）に行う。
  cert が時間外にも約定するかは記録に残す
- sandbox は **24 時間ごとにリセット**される（ユーザー・口座は残り、注文・建玉・履歴が消える）。
  手順 2〜6 は同じ日に通す
- ⚠ 認証に失敗しても**再試行しない**。失敗ログインを繰り返すと IP が約 8 時間ブロックされる

## 5. sandbox の疑似約定

板を模していない。**注文種別と価格だけ**で約定が決まる。

| 注文 | 結果 |
| --- | --- |
| 成行 | 必ず **$1** で約定 |
| 指値 **$3 未満** | 即約定 |
| 指値 **$3 以上** | `Live` のまま約定しない |

手順 4 が指値 **$10**（約定させない）、手順 5 が成行（約定させる）を使うのはこのため。

## 6. 使った版と出典

すべて 2026-09-05 取得。公式 SDK は使わず、OpenAPI 3.1 仕様どおりに REST を直接叩いている。

| 項目 | 値 | 出典 |
| --- | --- | --- |
| REST（sandbox / 本番） | `api.cert.tastyworks.com` / `api.tastyworks.com` | developer.tastytrade.com/docs/sandbox |
| 口座ストリーマ | `wss://streamer.cert.tastyworks.com` / `wss://streamer.tastyworks.com` | 同 /docs/concepts/streaming |
| 気配ストリーマ | DXLink。URL は `GET /api-quote-tokens` の応答に入る。トークンは 24 時間 | 同 /docs/guides/stream-market-data |
| 認証 | OAuth2。`POST /oauth/token`（`grant_type=refresh_token`）。**access token 15 分**、refresh token は無期限で更新時に回転しない | 同 /docs/authentication/oauth2 |
| 必須ヘッダ | `User-Agent: product/version`。無いと nginx が HTML の 401 を返す | 同 /docs/faq |
| レート制限 | REST は非公開（429 のみ、ヘッダ無し）。DXLink は明示: 同時 5 セッション・25,000 購読/セッション・Candle/Order/Series は各 100・購読変更 10,000/分 | 同 /docs/guides/rate-limits-and-backoff |
| 重複排除 | **無い**。idempotency ヘッダも無い。`external-identifier` を付けて再送前に照会する | 同 /docs/guides/idempotency-and-retries |
| API の版 | 日付版（`Accept-Version`）。廃止は 6 か月猶予、消えた版は 406 | 同 /docs/concepts/api-versions |
| OpenAPI 3.1 | 17 本を配布（`/openapi/orders.json` など）。Orders は `info.version: 11.124.10` | developer.tastytrade.com/llms.txt |
| 公式 Python SDK | `tastytrade-sdk` 1.2.0（2025-05-20）。**GitHub リポジトリは archived**（最終 push 2026-03-13） | pypi.org/pypi/tastytrade-sdk/json、api.github.com/repos/tastytrade/tastytrade-sdk-python |
| 非公式 Python SDK | `tastytrade`（tastyware）13.2.3（2026-08-07）、MIT、Python ≥3.11 | pypi.org/pypi/tastytrade/json |

## 7. 他社を足すとき

記録は会場名（`venue`）の列を持ち、`ttclient.py` の関数名は会場に依存しない
（`authenticate` / `list_accounts` / `get_quote` / `dry_run_order` / `submit_order` / `cancel_order`）。
moomoo・IBKR を足すときは同じ関数名で別モジュールを書き、`sample.py` の 6 手順をそのまま使う。
