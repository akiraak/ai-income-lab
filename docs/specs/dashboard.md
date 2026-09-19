# 管理画面（dashboard/）— 仕様とデプロイ契約

作成: 2026-09-05 / プラン: [docs/plans/archive/dashboard.md](../plans/archive/dashboard.md) / コード: [dashboard/](../../dashboard/)

売買システムの管理画面。**実運用の監視**（口座・注文・認証の残り時間・websocket の状態）と、
**開発時の検証**（記録の閲覧・差分・6 観点の自動判定・モックと手順の実行）を 1 つのアプリで持つ。
このプロジェクト最初のアプリ実装で、[tastytrade の API 検証](experiments/tastytrade-api-sample.md)の記録と
クライアント（`experiments/tastytrade-api-sample/`）の上に載る。

⚠ [vibeboard](../../vibeboard/) とは別物（あちらは `docs/` と `TODO.md` を見るローカル開発用）。

## 1. 画面

| 画面 | パス | 面 | 中身 |
| --- | --- | --- | --- |
| **概要** | `/` | 両方 | ⚠ **入口**（2026-09-18。§13・§15）。監視の帯（認証・接続・口座・働いている注文・事象。5 秒ごとに部分更新）・大きな数字（全トレーダーの損益 1 つ・色なし）と差 1・約定・起動しなかった日・損益の推移・執行の差・**トレーダーの段**（1 人 1 段。損益と行動を同じ時間の軸で揃える） |
| **全体の詳細** | `/overall` | 両方 | 日次（起動しなかった日も行を出す）・注文の履歴（全トレーダー）・**口座と接続**（環境ごとに認証の残り秒数と scope・refresh の成否、口座・残高・買付余力、建玉、働いている注文、現在値と遅延、口座ストリーマと DXLink の接続状態・切断と再接続の回数、エラーと 429 の回数、監視のイベント。5 秒ごとに部分更新） |
| **トレーダーの詳細** | `/traders/<name>` | 両方 | 損益の推移（実物 ＋ 紙上 ⚠ 仮データ）・差 1 の推移・行動のマス目（銘柄 × 日）・注文の履歴・設定 |
| 記録 | `/records`、`/records/<run_id>`、`/records/diff?a=&b=` | 両方 | 実行記録（JSONL）の一覧・1 実行の詳細（手順ごとの detail）・**実行間の差分**（所要 ms と状態遷移の時刻を項目ごとに並べ、B − A を出す） |
| 判定 | `/judge` | 両方 | **6 観点 × 会場**の表を記録から自動生成。根拠の run_id と手順つき。モックの記録は除外 |
| **検証** | `/experiments`、`/experiments/<run_id>` | 両方 | **特徴量の発見手法の検証**を、種類ごとのタイトルと**比較できるスコア**で並べる（§10）。⚠ **先読みの対照実験は一覧から外して別枠に出す** |
| **データ** | `/data` | 両方 | **実験（feature-discovery）が保持しているデータの在庫**（§11）。層・取得元・枠（本命 ／ 偽薬）・系列数・行数・期間・ずらし幅・規約の判定・割り当て |
| 実売買 | `/live` | 両方 | ⚠ **`/` へ転送**（2026-09-18 に概要へ移した。プランや手順書が名指ししているので経路だけ残す） |
| 操作 | `/ops` | ローカルのみ | 停止 / 解除、cert の dry-run → 発注 → 取消 → 後片付け、prod の 2 段ロック、操作の履歴 |
| 開発 | `/dev`、`/dev/jobs/<id>` | ローカルのみ | モックの起動・停止、`selftest.sh` の実行、手順を選んで `sample.py` を実行（出力を逐次表示） |
| JSON | `/api/state`、`/api/records`、`/api/judge`、`/api/experiments`、`/api/data`、`/api/live`、`/api/events` | 両方 | 画面と同じ内容（マスク済み）。読み取りだけ |

⚠ **ナビは左ペイン**（2026-09-18。§15-6）: 見る（概要・全体の詳細）／ トレーダー（設定の順・系列の色の点）／ API 検証（観点 A まで。記録・判定）／ ローカル面だけ（操作）。⚠ **検証・データ・開発は左ペインに出さない**（外すと決めた。経路とコードの削除は別タスク）。
右の上には常に **環境バッジ（CERT 緑 / PROD 赤 / MOCK 紫）と scope**、**停止ボタン**が出る（面は左ペインの下）。停止中は赤い帯が全画面に出る。

⚠ **2026-09-18 に要不要と画面の形を決め、同日に実装した**（構成・左ペイン・デザイン 3「数字とグラフが主役」。決定は [dashboard-required-features.md](../plans/archive/dashboard-required-features.md)、実装は [dashboard-design-implement.md](../plans/dashboard-design-implement.md)）。⚠ **外すと決めた 11 行（検証・データ・手動の注文・開発）は左ペインから外しただけで、経路とコードはまだある**（別タスク）

## 2. 権限の設計

> この図の主張: 権限は 4 段で、公開面は 2 段目（読む・止める）で止まる。発注の経路は公開面には存在しない（404）。

```mermaid
flowchart TB
  T0["0 読む<br/>監視・記録・判定"] --> T1["1 止める<br/>HALT ＋ 全取消"]
  T1 --> T2["2 動かす（cert）<br/>解除・発注・取消・開発"]
  T2 --> T3["3 動かす（prod）<br/>dry-run ／ 発注"]
  P["公開面<br/>AIL_AUTH_MODE=cloudflare"] -.->|ここまで| T1
  L["ローカル面<br/>loopback ／ local"] -.->|ここまで| T2
  K["TT_ALLOW_PROD_DRY_RUN=1<br/>TT_ALLOW_PROD_ORDERS=1<br/>＋ 確認文の入力"] -.->|さらに要る| T3
```

| `AIL_AUTH_MODE` | 通す接続元 | 認証 | 面 |
| --- | --- | --- | --- |
| `loopback`（**既定**） | ループバックのみ | なし | ローカル |
| `local` | ループバック ＋ RFC1918 | なし（ヘッダに「認証なし」と出す） | ローカル |
| `cloudflare` | ループバックは免除。それ以外は **全リクエスト（GET 含む）で `Cf-Access-Jwt-Assertion` を検証**（JWKS で RS256、aud・iss・email を照合） | Cloudflare Access | 公開 |

- `cloudflare` は `CF_ACCESS_TEAM` / `CF_ACCESS_AUD` / `CF_ACCESS_EMAIL` が**全部そろわないと起動しない**。他のモードで CF_* が置かれていても起動しない（中途半端な設定で素通りさせない）
- `X-Forwarded-For` は見ない。接続元は cloudflared のコンテナで、そこから先は JWT で決める
- 無認証の `/health` は無い（コンテナの healthcheck はループバックから叩く）
- POST は同一プロセスの CSRF トークン（cookie `SameSite=Strict` ＋ hidden）を要求する

### 停止ボタンの中身

1. `HALT` フラグ（記録ディレクトリの `HALT`、JSON で時刻・誰が・理由）を書く。**`sample.py` はこのファイルがあると発注系の手順（4・5・5limit・6）を拒否する**（exit 3）。`cleanup` は通る
2. 監視している環境ごとに、働いている注文（Received / Routed / In Flight / Live / Contingent）を全部取り消す。
   cert は常に可。**prod は資格情報の scope に `trade` があるときだけ**（無ければ「取消は不可」と結果に出す）
3. 解除はローカル面だけ

### 本番の鍵（`ttclient.py` と同じ 3 段）

| 操作 | cert | prod |
| --- | --- | --- |
| dry-run | 可 | `TT_ALLOW_PROD_DRY_RUN=1` |
| 取消 | 可 | `allow_prod_cancel`（停止ボタンと取消ボタンが使う。**この鍵で発注は開かない**。2026-09-05 に `ttclient.py` へ追加） |
| 発注 | 可（dry-run を必ず先に通す） | `TT_ALLOW_PROD_ORDERS=1` **＋ 確認文 `i-know-this-is-real-money` の入力** ＋ 停止中でない |

画面から本番に出せる注文は 1〜10 株の株式 1 レッグだけ。Phase 6（本番で 1 株）は利用者が CLI で行う前提で、
画面の開発機能は prod に対して `probe` と `dryrun` しか通さない。

## 3. 秘密の扱い

- client secret・refresh token・access token・id token・quote token・口座番号は**ブラウザに送らない**
- すべての画面・JSON は `Redactor`（`record.py` の `Masker` ＋ 口座番号のラベル化）を通す。口座番号は `acct-<sha256 の先頭 4 桁>`
- 未登録の JWT も正規表現で落とす。ジョブの標準出力もマスクしてから保持する
- `Cache-Control: no-store`、CSP `default-src 'self'`、外部リソースなし、`X-Frame-Options: DENY`
- テスト（`dashboard/tests/test_app.py`）は偽の秘密を仕込んで全ページ・全 JSON を grep し、0 件であることを固定している

## 4. 監視ループ

> この図の主張: 環境ごとに REST 1 本と websocket 2 本を持ち、出口は画面の状態と monitor/*.jsonl の 2 つ。

```mermaid
flowchart LR
  R["REST 30 秒ごと<br/>token・口座・建玉・注文・気配"] --> S["状態<br/>（画面・/api/state）"]
  A["口座ストリーマ<br/>常時接続・20 秒 heartbeat"] --> S
  D["DXLink（prod）<br/>気配の購読・30 秒 keepalive"] --> S
  R --> E["monitor/*.jsonl<br/>refresh の成否・接続・切断・429"]
  A --> E
  D --> E
  E --> J["判定 A 認証の寿命"]
```

| 何を | どう |
| --- | --- |
| 認証 | 失効 60 秒前に refresh。失敗は指数バックオフ（15 分 → 30 分 → …）、**3 連続で止める**（IP ブロック回避。再試行はローカル面のボタン）。token 応答の `scope` を保持し、ヘッダに出す |
| 口座 | `AIL_POLL_SECONDS`（既定 30）ごとに残高・建玉・働いている注文。口座番号は最初の照会で決める（`TT_ACCOUNT_NUMBER` があればそれ） |
| 現在値 | prod（とモック）だけ `AIL_SYMBOL`（既定 SPY）の REST 気配。`updated-at` との差を遅延として出す |
| 口座ストリーマ | `Bearer` 付きで connect、heartbeat には最新の access token を載せる。切れたら 5 秒 → 最大 120 秒のバックオフで再接続。通知（Order / AccountBalance / CurrentPosition）は直近 50 件 |
| DXLink | `GET /api-quote-tokens` の `dxlink-url` に繋ぐ。23 時間でトークンを取り直す。Quote / Trade の最新値と件数 |
| イベント | `monitor/<venue>-<env>-<UTC 日付>.jsonl` に `refresh_ok` / `refresh_fail` / `ws_connect` / `ws_disconnect` / `api_error` / `http_429` / `halt` / `resume` を追記 |

## 5. 判定の規則（記録の値だけから）

| 観点 | ✅ | ⚠ | ⏳ 未実測 |
| --- | --- | --- | --- |
| A 認証の寿命 | 手順 1 の成功と監視ログの `refresh_ok` が **2 営業日以上**にまたがる | またがるが `refresh_fail` もある | 同じ営業日の成功しか無い |
| B 常駐 | 同じ実行で手順 1・2・6 が ok | 1・2 だけ ok | 記録なし |
| C 現在値 | prod の手順 3 が**市場時間内**（ET 平日 9:30〜16:00）で `delay_s` < 1 | 市場時間内で 1 秒以上 | 時間外の記録しか無い（遅延は参考値と明記）／本番の資格情報なし |
| D 発注の往復 | 手順 4 と 5（または 51）が ok | 4 だけ ok | 記録なし（4 が失敗なら ❌） |
| E レート制限 | 手順 7 で 60 回/分が通り 429 なし | 429 が出た／60 回に達していない | 記録なし |
| F SDK | 記録の `sdk` 欄が「直接叩き」（OpenAPI 仕様がある） | SDK を使っている | 記録なし |

`mock: true` の行を含む実行は判定に使わない。会場（`venue`）ごとに列を並べる。

## 6. データの置き場

```
<AIL_DATA_DIR>/                  既定 dashboard/data、Docker では /app/data（volume）
├── records/                     実行記録 *.jsonl（AIL_RECORDS_DIR で別の場所も可。開発機の既定はサンプルの out/）
│   └── HALT                     停止フラグ（sample.py の TT_HALT_FILE の既定と同じ場所）
├── monitor/<venue>-<env>-<日付>.jsonl   監視イベント
├── ops/history.jsonl            操作の履歴（マスク済み）
└── jobs/history.jsonl, mock.log ジョブの履歴とモックのログ
```

⚠ **検証（§10）とデータ（§11）だけは `<AIL_DATA_DIR>` の外を読む。**

| | 既定 | 中身 |
| --- | --- | --- |
| `AIL_RUNS_DIR` | `experiments/feature-discovery/runs/` | ⚠ **読むだけ。** 管理画面は 1 バイトも書かない。⚠ **git 管理外なので、別環境では空でよい**（画面は空でも 200 を返す） |
| `AIL_EXP_DIR` | `experiments/feature-discovery/` | ⚠ **読むだけ**（§11 が `data/manifests/`・`data/features/*/…meta.json`・`config/` を読む）。⚠ **無くても画面は 200 を返す** |

## 6-2. デモ（鍵なしで動かす。2026-09-08）

資格情報が 1 つも無いとき、または `AIL_DEMO=1` のとき、本物には繋がず**モックサーバのデータで全画面を出す**。
デザイン確認と、鍵を置く前のサーバ起動のため。この図の主張: **デモは「資格情報の代わりにモックを差す」だけで、画面・監視ループ・記録の経路は本物と同じものを通す**。

```mermaid
flowchart LR
  S["起動（AIL_DEMO=1 か、資格情報が無い）"] --> M["モックサーバを自動起動<br/>127.0.0.1:8765〜8767"]
  M --> MON["監視ループ cert / prod<br/>接続先をモックに（MOCK バッジ）"]
  M --> SEED["記録が無ければ 1 回だけ<br/>sample.py --step all をモックに流す<br/>（ローカル面のみ。ジョブとして開発画面に出る）"]
  MON & SEED --> UI["全画面にデータ ＋ 上部に『デモ』の帯"]
```

| 項目 | 内容 |
| --- | --- |
| 判定 | 通常はモックの記録を除外するが、デモでは含めて表を組み立てる（見出しに DEMO バッジ。本物の判定には使わない） |
| データの置き場 | `data/demo/` 配下（記録・監視ログ・ジョブ・操作履歴）。本物の記録（`out/`・`data/records`）と混ぜない |
| 実売買（概要・全体の詳細・トレーダーの詳細） | ⚠ **2026-09-18 から**、`AIL_LIVE_DIR` を指定しなければ**執行器のモックの記録**（`dashboard/demo/live/`。3 人 × 20 営業日・10-19 は起動しない・`mock: true`）を読む（§13-3） |
| 資格情報があるとき | `AIL_DEMO=1` なら本物には一切繋がない（監視は cert / prod ともモック）。`AIL_DEMO=0` で強制オフ |
| 面の規則 | 変えない。公開面（cloudflare）では操作・開発は出ない。g3plus は `.env` の資格情報が空なら自動でデモになる |

## 7. デプロイ契約（正本）

g3plus-ops 側の `ail-dashboard/`（Dockerfile・compose・手順書）はここに従う。契約が変わったらあちらを追従させる。

| 項目 | 値 |
| --- | --- |
| ベース | `python:3.12-slim`。ネイティブビルドなし（依存は `dashboard/requirements.txt` の 7 つ。`cryptography` は wheel） |
| build context | リポジトリの clone（public なので `git clone` → `git pull`）。COPY するのは `dashboard/app/`・`dashboard/requirements.txt`・`experiments/tastytrade-api-sample/*.py` と `*.sh` |
| 起動 | `cd /app/dashboard && python -m app.main`（uvicorn。`AIL_BIND=0.0.0.0`、`AIL_PORT=3012`） |
| ポート | **3012**。`ports:` でホスト公開しない（到達できるのは同じ docker network の cloudflared だけ） |
| 必須 env | `AIL_AUTH_MODE`（公開時 `cloudflare` ＋ `CF_ACCESS_TEAM` / `CF_ACCESS_AUD` / `CF_ACCESS_EMAIL`）、監視したい環境の資格情報（`TT_PROD_*` は **read スコープの grant を別に切って渡す**のが既定。cert の `TT_*` は任意） |
| 置かない env | `TT_ALLOW_PROD_ORDERS` / `TT_ALLOW_PROD_DRY_RUN`（公開面には発注経路が無いので意味を持たないが、置かない） |
| 永続化 | `/app/data`（§6）。**唯一の永続化対象** |
| 検証の画面 | ⚠ **`experiments/feature-discovery/runs/` は COPY しないので、g3plus では空になる**（画面は空でも 200）。見せたいときだけ `AIL_RUNS_DIR` を volume で差す。⚠ **読み取り専用でよい** |
| データの画面 | ⚠ **`experiments/feature-discovery/` も COPY しないので、g3plus では空になる**（画面は空でも 200）。見せたいときだけ `AIL_EXP_DIR` を volume で差す。⚠ **読み取り専用でよい** |
| 実売買の画面（概要・全体の詳細・トレーダーの詳細） | ⚠ **`experiments/live-trading/` も `dashboard/demo/` も COPY しないので、g3plus では空になる**（画面は空でも 200。デモでも空）。見せるには g3plus へ執行器の記録を届ける経路が要る（F21。未定） |
| TZ | `America/Los_Angeles` |
| healthcheck | コンテナ内ループバックで `GET /` が 200（`python -c "urllib.request.urlopen('http://127.0.0.1:3012/')"`） |
| 外向き通信 | `api.tastyworks.com` / `api.cert.tastyworks.com`（REST）、`streamer.tastyworks.com` / `streamer.cert.tastyworks.com`（口座 websocket）、`*.dxfeed.com`（DXLink。URL は応答の `dxlink-url`）、`<team>.cloudflareaccess.com`（JWKS） |
| 段階的な有効化 | Access の AUD が無いうちは `AIL_AUTH_MODE=loopback`（非ループバックは全部 403 = fail-safe）で起動しておき、Access アプリ → `.env` に 3 変数と `cloudflare` → Tunnel hostname の順で開ける |
| エッジキャッシュ | ホスト全体 Bypass の Cache Rule を入れる（キャッシュ HIT は認証評価前に配信される。アプリ側も `no-store` を返す） |

## 8. 検証（2026-09-05）

| # | 検証 | 結果 |
| --- | --- | --- |
| 1 | 秘密がブラウザに出ない | ✅ `tests/test_app.py`: 偽の client secret・refresh・access token・口座番号を仕込み、12 経路の応答を grep して 0 件。モックに対する実機の全ページでも 0 件 |
| 2 | 面の判定 | ✅ `tests/test_access.py`: loopback / local / cloudflare の 3 モード、JWT の aud・iss・email・署名鍵の不一致で 403、cookie でも通る。公開面で `/ops` `/dev` が 404、停止だけ 303 |
| 3 | 本番ガードの 3 つの鍵 | ✅ `experiments/tastytrade-api-sample/test_guard.py`（selftest.sh に組み込み）: dry-run の鍵・取消の鍵で発注が開かない |
| 4 | HALT | ✅ selftest.sh: フラグがあると `sample.py --step 4` が exit 3、`cleanup` は通る。画面: 停止中の発注は拒否、解除で消える |
| 5 | 判定の再現性 | ✅ `tests/test_records_judge.py`: 市場時間内の成功記録 2 営業日で A〜F すべて ✅、モック除外、部分記録で ⏳ / ⚠ / ❌ |
| 6 | 配線（監視ループ） | ✅ モック（`--market-data`）に対して cert / prod の 2 環境で認証・照会・口座ストリーマ・DXLink が繋がり、dry-run → 発注 → 停止（取消 1 件）→ 解除 → 後片付け、selftest ジョブの実行まで通した |
| 7 | Docker | ✅ 開発機で `docker build`（context = リポジトリ、165 MB）→ 起動（既定 loopback）: コンテナ内ループバックの `/` が 200、ホストから公開ポート経由（非ループバック）は 403、`AIL_AUTH_MODE=cloudflare` で AUD 欠けは `ConfigError` で起動せず、healthcheck は healthy |

## 10. 検証の画面（2026-09-08）

⚠ **`/judge` は「tastytrade の API が使えるか」の判定、`/experiments` は「分析手法の検証」で別物。**
材料も別で、こちらは `experiments/feature-discovery/runs/` を読む。

> この図の主張: ⚠ **重い計算は実験を回すときに 1 度だけ。管理画面は読むだけにする。** だから仕様書の数字と画面の数字がずれない。

```mermaid
flowchart LR
  R["cli/run.py<br/>実験を回す"] --> C["ail/validation/checks.py<br/>fold の符号・t・実効 n・DSR"]
  C --> J["runs/&lt;実行&gt;/checks.json"]
  J --> D["app/experiments.py<br/>⚠ 標準ライブラリだけ"]
  D --> V["/experiments"]
  J --> S["docs/specs/experiments/…<br/>仕様書の数字"]
```

⚠ **管理画面に pandas / scipy を入れない**（`requirements.txt` は増やさない）。

### 10-1. 検証の種類とタイトル

⚠ **タイトルは付けずに設定から組み立てる。** 手で名前を書くと実行のたびにずれる。

| 部品 | どこから | 値 |
| --- | --- | --- |
| 種類 | `feature_layers` に `cs` `rel` `ll` があるか | **プーリング** ／ **断面** |
| 粒度 | `bar_minutes` | 日足 ／ 1 分足 |
| 地平 | `horizon` × `bar_minutes` | 1 日先 ／ 1 取引日先 |
| データの層 | 記録の `layer`（`cli/build.py` が書く sidecar が正） | 調整後 ／ ⚠ **調整前** |
| 対照実験 | 実行名の `_leak` | ⚠ **（先読みの検査）を後ろに付ける** |
| 偽薬 | 実行名の `_shift<S>` か config の `features.ex_shift_days`（2026-09-16） | ⚠ **（偽薬: 日付 −S 日）を後ろに付け、種類は「偽薬（日付ずらし）」** |

⚠ **例**: `2026-09-08T19-42-54_cross_section_h1` → **「断面・日足・1 日先・調整後」**。
⚠ **先読みの実行も「断面・…（先読みの検査）」と書く**（どの検証の対照かが読めないと意味が無い）。

### 10-2. スコア

⚠ **スコア = 最良手法（基準線を除く）の純利 bp**（利用者が 2026-09-08 に決めた）。降順に並べる。

| # | 規約 | ⚠ 理由 |
| ---: | --- | --- |
| 1 | ⚠ **基準線をスコアにしない**（「常に上」「直前リターンの符号」「全部使う」「乱択」） | ⚠ **「常に上」が 1 位になると比較の意味が消える。** 基準線は比べる相手であって採否の対象ではない |
| 2 | ⚠ **先読みの対照実験を一覧に入れない。** 別表に出す | ⚠ **的中率 99% の検証がスコア 1 位に出ると、一覧全体が嘘になる** |
| 2-2 | ⚠ **日付をずらした偽薬も一覧に入れない。** 別表に出す（2026-09-16） | ⚠ **本物と同じ設定・同じタイトルなので、混ぜると同じ名前の行が何十本も並んで本物が埋もれる**（[plan](../plans/archive/exog-shift-placebo.md)） |
| 3 | ⚠ **検査の 5 列を必ず横に並べる** | ⚠ **スコアは 1 つの数字なので、fold の偏りも多重検定も数字自体には出ない** |
| 4 | ⚠ **検査が無い実行は ⏳。** 0 や ✅ で埋めない | ⚠ **「計算していない」と「通らなかった」は別物** |

### 10-3. 横に並べる 5 列（✅ の条件）

| 列 | ✅ | ⚠ | ⏳ |
| --- | --- | --- | --- |
| **層** | `adjusted`（調整後） | ⚠ **日足 × `raw`**（分割調整の誤りを含む＝ 無効） | 1 分足 × `raw`（誤りは日足にしか無い） |
| **純利** | 往復コストを引いて正 | 0 以下 | スコアが無い |
| **fold** | ⚠ **全 fold で正** | 符号が割れる | fold の記録が無い |
| **上乗せ** | 「常に上」への上乗せの **t > 3.0** | 3.0 以下 | 上乗せを測れない |
| **DSR** | ⚠ **実効標本数で割り引いた**デフレーテッド SR > 0.95 | 0.95 以下 | パネルを確かめられず未計算 |

⚠ **DSR は「SR > 0」の検定であって「基準線を超えたか」ではない。** その比較は「上乗せ」の列が担う。
⚠ **`n_trials` は増えていく。** 画面には**そのときの試行数**を出し、正本は
[台帳](experiments/feature-discovery/ledger.md)のままにする。

### 10-4. ⚠ この画面で埋まらないもの

| # | 限界 | ⚠ 効き方 |
| ---: | --- | --- |
| 1 | ⚠ **スコアは 1 つの数字。** fold 1 だけで作られた数字も高く出る | ⚠ **検査の 5 列と、画面の注記で補う。** 隠さない |
| 2 | ⚠ **無効な検証（日足 × `raw`）もスコア順では上に来る** | ⚠ **層の列が ⚠ になるが、並び順では下がらない** |
| 3 | 検証どうしは条件が違う（粒度・地平・層・列の数） | ⚠ **スコアの差が手法の差とは限らない。** 条件を同じ行に出す |
| 4 | ⚠ **後から `checks.json` を書くとき、当時のパネルが残っていないと実効標本数と DSR は出せない** | ⚠ **層と行数の両方が一致する実行だけ計算し、それ以外は鍵ごと省く** |

⚠ **デモ（§6-2）はこの画面に効かない。** 検証の数字は `AIL_RUNS_DIR` の記録をそのまま読むので、
⚠ **デモ中でも本物である。** ⚠ **デモの帯は「モックのデータを表示している」と書くので、この画面では
そのままだと嘘になる**（2026-09-08 に踏んだ）。帯に「この画面はデモの対象外」と足し、
フッタの出所も `records/` ではなく `runs/` を出す。

### 10-5. 閾値つき売買の実行（2026-09-10）

検証方式が**閾値つき売買**（[rules.md 13 章](experiments/feature-discovery/rules.md)）の実行は、
`checks.json` に `style: "threshold"` が付く。⚠ **画面は写しを出すだけ**の原則はそのまま。

| 変わるもの | 中身 |
| --- | --- |
| タイトル | 末尾に **「閾値売買・共通」／「閾値売買・銘柄別」** が付く（形式 (A)(B) を見分ける） |
| 上乗せの列 | ⚠ **相手が「常に上」（粗利）ではなく B&H（純利）**（`edge_vs_bh`。rules 13-7）。✅ の条件（t > 3.0）は同じ |
| fold の列 | ⚠ **fold の符号は対 B&H の上乗せの符号**（純利の符号では「買って持っただけ」と区別できない） |
| 詳細ページ | **「閾値ごとの成績」**を 3 水準とも出す（θ・最良手法・純利・B&H 純利・上乗せ・DSR・**取引回数・保有日率**・**保有日数 中央値（p25–p75）**（2026-09-17。`by_threshold[θ].holding` の写し。1 取引ごとの分布で強制清算を含む。鍵の無い旧実行は「—」。⚠ **採否には使わない**。rules 13-4 の 6）・銘柄別 bp の要約）。⚠ **良かった閾値だけ出さない**（rules 13-3）。⚠ **取引回数を必ず横に置く**（「θ が高いほど良い」＝「取引しないだけ」を見抜くため。rules 13-10） |
| 一覧のスコア | 最良手法 × 最良閾値のポートフォリオ純利 bp（`best.閾値` を併記）。⚠ **3 水準の全体は詳細ページが持つ** |

⚠ **銘柄別 bp（`per_symbol.csv`）は要約（中央値・四分位・勝ち銘柄数）だけ出す。**
成果物であって採否には使わない（rules 13-7）。vibeboard の検証タブ（`vibetab.py`）も同じ写しを出す。

### 10-6. 門前の実行（2026-09-11）

前置きの門（[rules.md 14-5](experiments/feature-discovery/rules.md)）を通らなかった手法は**閾値売買を回さない**。
全手法が門前の実行は `summary.csv` を持たず、`checks.json` に `gate` だけが残る。

> この図の主張: ⚠ **実行は 3 つに分かれる。** 落とすのは「summary も門前の記録も無い」ものだけ。

```mermaid
flowchart TB
  A["runs/&lt;実行&gt;/"] --> Q1{"summary.csv がある"}
  Q1 -->|"ある"| N["一覧（スコアの降順）<br/>leak は別表"]
  Q1 -->|"ない"| Q2{"checks.gate.blocked<br/>かつ forced でない"}
  Q2 -->|"はい"| G["⚠ 別表「門前」<br/>門の 2 値を出す・数に入れない"]
  Q2 -->|"いいえ"| X["落とす（従来どおり）"]
```

| # | 規約 | ⚠ 理由 |
| ---: | --- | --- |
| 1 | ⚠ **門前の実行を画面から落とさない**（`index` の `gated_runs`） | ⚠ **台帳には「門前」の行で残るのに画面から消えると、画面を見て実行の有無を判断できない**（2026-09-11 に踏んだ） |
| 2 | ⚠ **一覧（スコアの降順）には混ぜず、別表に出す。** `total` / `positive` / `kinds` にも数えない | ⚠ **検証を回していないので「検証 N 件」に足すと水増しになる**（n_trials に数えないのと同じ扱い。14-5） |
| 3 | ⚠ **判定の 5 列（§10-3）は出さない** | ⚠ **⏳ を 5 つ並べると「計算待ち」に見える。** 門前は計算していないのではなく**回していない**（§10-2 の規約 4 とはここが違う） |
| 4 | 代わりに**門の 2 値と水準**（訓練内 holdout の AUC・買い% 幅、`auc_min` / `width_min_pt`）を写して出す | 画面だけで「なぜ回っていないか」が読める。⚠ **画面で門を判定し直さない**（`checks.json` の `gate` の写し） |
| 5 | ⚠ **`forced`（門前の手法も回した印）で summary が無い実行は従来どおり落とす**。⚠ **2026-09-14 から門は既定で止めない**ので、門前があれば既定の実行でも `forced` が立つ（`--ignore-gate` は既定の別名）。⚠ **門前の実行が生まれるのは `--gate` で足切りしたときだけ**（rules.md 14-10 規約 2） | ⚠ **「回したのに結果が無い」を門前と混同しない**（拾う条件は台帳 `ail/catalog.py` と同じ） |
| 6 | 一部の手法だけ門前（`summary` あり）の実行は**一覧に残し**、詳細に「回していない手法」の節を出す | ⚠ **一覧のスコアは回した手法のもので正しい。** 隠れるのは回さなかった手法なので、詳細で補う |

⚠ **門前の実行の詳細ページはスコアの 4 枚のカードを出さない**（すべて「—」になるため）。
代わりに帯（回していないこと）・条件・**門の表**（手法ごとの AUC・幅・fold ごとの値・通過 / 門前）を出す。
vibeboard の検証タブ（`vibetab.py`）も同じ写しを出す（目次の「⚠ 門前」・まとめの件数と表・実行ページの門の節）。

## 11. データの画面（2026-09-09）

実験（`experiments/feature-discovery/`）が**何をどれだけ持っているか**を 1 画面にする
（プラン: [docs/plans/archive/dashboard-data-inventory.md](../plans/archive/dashboard-data-inventory.md)）。
§10 と同じ立て方で、⚠ **実験側が書いたものを読むだけ**。CSV / parquet を開いて数え直さない。

> この図の主張: ⚠ **数字は実験側が取得時に書き、画面は読むだけ。** だから manifest と画面の数字がずれない。

```mermaid
flowchart LR
  F["cli/fetch · cli/build<br/>取得と検査"] --> M["data/manifests/*.json<br/>data/features/*/d.meta.json"]
  C["config/dataset/*.toml<br/>config/exposure/*.toml<br/>config/sources.toml"] --> I
  M --> I["app/inventory.py<br/>⚠ 標準ライブラリだけ"]
  I --> V["/data · /api/data"]
```

### 11-1. 何をどこから写すか

| 見せるもの | 正本 | ⚠ 規約 |
| --- | --- | --- |
| 系列数・行数・最古と最新の日・検査の引っかかり | `data/manifests/<層>_<粒度>.json` | ⚠ **manifest の値をそのまま出す**（数え直さない。rules.md 5 章の指紋が正） |
| 特徴量の表（行数・列数・元の層） | `data/features/<実験>/<粒度>.meta.json`（`cli/build.py` の sidecar） | ⚠ **先読み検査用（`_leak`）は別枠に出す** |
| 枠（本命 ／ 偽薬）・仮説 | `config/dataset/*.toml` の `role` `hypothesis` | ⚠ **取得の前の宣言を写すだけ。結果を見て分類しない。** manifest の枠（取得時の写し）と食い違ったら ⚠ を立てて見せる（黙ってどちらかを選ばない） |
| 銘柄の種別（会社株 ／ ETF ／ 実質は株の ETF） | `config/universe/*.toml` の `groups` | ⚠ **宣言を銘柄名で引くだけ**（画面が銘柄名から推測しない）。⚠ **宣言に無い銘柄は「分類なし」で見せる**（黙ってどちらかに寄せない）。集合の限界（生存バイアス・選定日）も一緒に出す |
| ずらし幅・公表の遅れ・規約の判定 | `config/sources.toml`（2026-09-09 新設） | ⚠ **実効値は `ail/features/exog.py` / `impact.py` が持つ。** 一致は実験側の `tests/test_sources_decl.py` が固定する（同じ数字を 2 か所で手管理しない） |
| 割り当て（災害 → 銘柄の重み） | `config/exposure/*.toml` | ⚠ **全部【推測】・後知恵あり（`hindsight`）を画面に明示する**（宣言の写し） |

### 11-2. 規約の判定は 3 分類

| 判定 | 意味 | 例 |
| --- | --- | --- |
| 公有 | 米政府の著作物（17 U.S.C. §105） | 財務省・NOAA・USGS・NCEI |
| robots | robots.txt と規約を実測して問題なし | ECB・EPU・IEM（`Crawl-delay: 120` を守る） |
| 契約 | 口座の規約の範囲で使う | tastytrade |

⚠ **「要判断」の取得元（FRED・Open-Meteo・SILSO）はデータを持っていないので画面に出さない**
（[daily-data-sources.md §2・§4](experiments/daily-data-sources.md) が正本）。

### 11-3. 面とデモ

- 読むだけ・秘密なし（外部系列と足は公開データ）なので**公開面にも出す**。応答は他の画面と同じく `Redactor` を通す
- ⚠ **デモ（§6-2）の対象外**（§10-4 と同じ理由）。帯に「この画面はデモの対象外」と足し、フッタの出所は `AIL_EXP_DIR` を出す
- ⚠ **`AIL_EXP_DIR` が無い・壊れた JSON / TOML でも 200 を返す**（g3plus には COPY しない。§7）

## 12. 用語の画面（2026-09-12）

vibeboard の**用語**タブ（`/ext/glossary`）。⚠ **索引であって解説書ではない。**
1 語 1〜2 行の意味と、⚠ **詳しい定義がある文書へのリンク**だけを出す
（プラン: [docs/plans/archive/vibeboard-glossary.md](../plans/archive/vibeboard-glossary.md)）。

⚠ **この画面は管理画面（3012）には無い。** `vibetab.py` の 3 本目のタブで、
§10・§11 と同じく **vibeboard 本体が `/ext/<name>` で中継する**。

> この図の主張: ⚠ **説明はコードにも画面にも持たない。** 正本は TOML 1 本で、画面はその写しを出すだけ。

```mermaid
flowchart LR
  T["dashboard/glossary.toml<br/>⚠ 用語の正本"] --> V["vibetab.py<br/>/glossary"]
  V --> TAB["vibeboard の「用語」タブ<br/>節ごとの表"]
  TAB -->|"リンク（target=_top）"| DOC["Specs / Plans / Files タブ<br/>⚠ 定義の正本"]
```

### 12-1. 規約

| # | 規約 | ⚠ 理由 |
| ---: | --- | --- |
| 1 | ⚠ **用語の正本は `dashboard/glossary.toml`**（`tomllib`。画面は写し） | ⚠ **説明を Python に埋めない。** §10-2 の「画面は読むだけ」と同じ立て方 |
| 2 | ⚠ **1 語 1〜2 行。詳しい定義を書かない**（テストが長さを固定する） | 定義が 2 か所にあると必ず食い違う。⚠ **食い違ったらリンク先が勝つ** |
| 3 | ⚠ **動く数字を書かない**（試行数・DSR の値・行数） | ⚠ **数字は動く。** 用語表に残ると嘘になる。数字は検証タブ（§10）と spec が持つ |
| 4 | リンクは vibeboard の hash URL（`/#specs/…`・`/#plans/…`・`/#files/…`）へ `target="_top"` | ⚠ **同じ画面の中で定義まで辿れる**。⚠ **節（§）へは飛べない**ので、節は文字で横に置く |
| 5 | ⚠ **リンク先の実在をテストで固定する**（`tests/test_vibetab.py`） | ⚠ **リンク切れは索引の価値を消す。** 文書を移したら赤くなる |
| 6 | 読めない・壊れているときは空で 200（仮の説明で埋めない） | 「用語が無い」と「壊れている」を混ぜない |

### 12-2. 画面

| 経路 | 中身 |
| --- | --- |
| `/glossary/api/sidebar` | 先頭が **すべての用語**（全語を 1 ページに出す。⚠ **ブラウザの検索で引くため**）、以下は分野ごとの節 |
| `/glossary/view?item=<節 id\|all>` | 節の表（用語・意味・詳しく）。知らない id は 404 |
| `/glossary/api/watch` | `glossary.toml` の mtime を見て、編集したらタブが自分で追いつく |

分野は 8 つ（進め方 ／ 検証の単位と台帳 ／ 統計の検査 ／ 閾値つき売買 ／ データ ／ 手法とモデル ／ 口座と API ／ 収入の体系）。
⚠ **語を足すのは TOML だけ**で、画面もサイドバーも追従する。

### 12-3. ⚠ sidecar は vibeboard を再起動しても入れ替わらない（2026-09-12 に踏んだ）

まっさらな状態なら **`./run-vibeboard.sh` だけで 3 タブとも上がる**【実測 2026-09-12】。
sidecar（`vibetab.py`）は customTabs の `command`（**検証の 1 件だけが持つ**。3 タブとも同じ 1 プロセスが出す）で
vibeboard の**子**として起き、⚠ **vibeboard を止めると一緒に止まる。**

⚠ **例外が 1 つあり、そこを踏んだ。** タブに **「接続できません: HTTP 404」** が出たら、⚠ **3015 に古い `vibetab.py` が居座っている。**

| 何が起きるか | なぜ |
| --- | --- |
| vibeboard を入れ直してもタブが 404 | vibeboard は `command` を実行する前に **baseUrl が応答するかを見て、応答したら起動しない**（`vibeboard/src/sidecar.ts` の `startOne`）。⚠ **古い sidecar が答えるので、新しいものは上がらない** |
| 古い sidecar が生き残る | `vibetab.py` は bind できないとき ⚠ **静かに引く**（二重起動を避ける設計）。前の vibeboard の子は親が落ちても残ることがある |

直し方: ⚠ **ポートから引いて**（`pgrep -f` は自分にも当たる）落とし、入れ直す。⚠ **3010 には触らない。**

```bash
pid=$(ss -ltnp | grep ":3015 " | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1) && kill "$pid"
nohup python3 dashboard/vibetab.py &                   # 3 タブとも同じ 1 プロセスが出す
```

⚠ **この罠は用語タブに限らない**（検証・データも同じ 1 プロセスが出している）。⚠ **`vibetab.py` を直したら sidecar を入れ直す**。

## 13. 実売買の画面（2026-09-18）— 概要 ・ 全体の詳細 ・ トレーダーの詳細

⚠ **2026-09-18 に `/live` 1 枚から 3 画面に作り直した**（`/` 概要・`/overall` 全体の詳細・`/traders/<name>`。`/live` は `/` へ転送）。データは `app/live.py` の `board()`（トレーダー別の推移・行動のマス目・差 1・起動しなかった日）、図は `app/charts.py`（§15-5）。`/api/live` は今までどおり `index()`。

⚠ **`/live` は「トレーダー 3 人の実売買」（[プラン](../plans/live-trading-three-models.md)・[記録](experiments/live-trading.md)）の監視で、
`/judge`（API が使えるか）・`/experiments`（分析手法の検証）とは別物。** 材料も別で、`experiments/live-trading/` を読む。

> この図の主張: ⚠ **執行器が書いたものを写すだけ。画面は数え直さない・書かない・発注しない。** 停止だけは既存の停止ボタン（`HALT`）で、執行器がそれを見る。

```mermaid
flowchart LR
  T["config/traders/*.toml<br/>予算・モデル・合成・θ"] --> L["app/live.py<br/>⚠ 標準ライブラリだけ"]
  S["state/&lt;env&gt;/*.json<br/>持ち分・実現損益"] --> L
  O["out/&lt;日付&gt;/*.jsonl<br/>合図・気配・注文・約定・台帳・事象"] --> L
  L --> V["/ 概要 ・ /overall ・ /traders/名前<br/>/api/live（両面・読むだけ）"]
  H["■ 停止 → HALT"] -.->|執行器が見る| R["run_day.py"]
  R --> O
```

### 13-1. 何をどこから写すか

| 画面の項目 | 正本 | ⚠ 注意 |
| --- | --- | --- |
| トレーダー（名前・予算・銘柄集合・モデルの一覧・合成規則・θ・株数の決め方・試験用） | `config/traders/<名前>.toml` | `test = true` のトレーダーは TEST バッジ。⚠ **実際に動かすトレーダーが 0 人なら「属性はまだ設定していない」と出す**（2026-09-17 の利用者決定） |
| 建玉・原価・実現損益・最終日 | `state/<env>/<名前>.json` | env（cert ／ prod）ごとに別の台帳。cert のリハーサルと本番を混ぜない |
| 原価 ／ 実現 ／ 含み・含み損の割合 | `out/<日付>/ledger.jsonl` の最新行 | ⚠ **含み損が予算の 20% を超えたら赤で「停止条件」と出す**（`live-trading.md` §0-2。⚠ 自動では止めない。止めるのは人） |
| 合図（トレーダー × 銘柄の買い% ／ 出口% と入力のモデル） | `out/<日付>/signals.jsonl` | 合成後の値。モデル別は `predict.jsonl`（Phase 1 の後） |
| 注文（数量・誰の分・合図時の気配・約定・状態・試行回数・手数料・所要・エラー） | `out/<日付>/orders.jsonl` | 手数料は dry-run の `fee-calculation` の写し（差 2 の材料。⚠ 実際の規制費は口座の取引履歴でしか確定しない） |
| 内部移転・残高 | `transfers.jsonl` ／ `balances.jsonl` | 内部移転は口座に出ない（差 3 のコスト 0） |
| 日次（1 日 1 行）: 合図・注文・約定・問題・再送・差 1 の中央値と最大・見送り・事象 | 上の全部 | 新しい順に 20 日 |

### 13-2. 画面で計算する唯一の数字 — 差 1

記録には価格しか無いので、**差 1（合図時の気配 → 約定）だけ画面で bp に直す**。買いは (約定 − mid) ÷ mid、売りは (mid − 約定) ÷ mid。⚠ **正 ＝ 不利**。
色は `live-trading.md` §0-2 の閾値（中央値 ✅ ≤ 5bp ／ ⚠ 5〜10 ／ ❌ ＞ 10）。差 2 は dry-run の手数料の写し、差 3 は Phase 3（紙上の対照）の後で埋まる、差 4（無人運転）は問題のあった日と再送の回数。

### 13-3. 面とデモ

| 項目 | 内容 |
| --- | --- |
| 面 | **両面**（公開面でも読める）。POST の経路は無い（405）。停止は既存の `/ops/halt` |
| デモ | ⚠ **2026-09-18 から対象に入れた**: デモ（`AIL_DEMO=1` か鍵なし）で `AIL_LIVE_DIR` を指定しなければ、**執行器のモックの記録**（`dashboard/demo/live/`。3 人 × 20 営業日・10-19 は起動しない・`mock: true`）を読む。`AIL_LIVE_DIR` を指定したときはそれを読む。無ければ空のまま 200（g3plus には `dashboard/demo/` を COPY しないので空。§7） |
| 秘密 | 記録は執行器が `Masker` を通して書き、画面の応答はさらに `Redactor` を通す。テストは口座番号・JWT の不在を固定 |
| 更新 | リクエストごとに読む（監視ループには載せない。1 日 1 回しか増えない） |

### 13-4. ⚠ この画面で埋まらないもの

- 紙上の対照（差 3）と B&H。Phase 3 で `daily.csv` ができたら列を足す（⚠ **新しいキーと列の対応を確かめる**。§10 の手順と同じ）。⚠ **それまでは紙上の損益と差 3 に仮データを出す**（§15-8。本物と取り違えない印を付け、`/api/live` には出さない）
- ✅ 休場日の暦は 2026-09-18 に入れた（NYSE の公表 2026〜2028 年。§15-8）。⚠ **暦に載っていない年だけ「平日＝営業日」に戻り、「仮」の印が出る**
- 「儲かったか」の判定。損益は出すが色を付けない（判定は執行の差と無人運転だけ）
- 執行器の起動。画面からは動かさない（titan の timer ／ cron。`live-trading.md` §0-5）

### 13-5. 画面（2026-09-18、幅 1280px。デモ）

⚠ **写っている数字はモックの値で、【実測】ではない**（`dashboard/demo/live/`。執行器のモックを 3 人で 20 営業日。約定は `--fill-noise 0.003` で気配から散らしたもの ＝ 差 1 が ❌ ばかりなのはそのせい）。

**概要**（`/`）

![概要](../plans/assets/dashboard-overview.png)

**全体の詳細**（`/overall`。上側）

![全体の詳細](../plans/assets/dashboard-overall.png)

**トレーダーの詳細**（`/traders/mock_a`）

![トレーダーの詳細](../plans/assets/dashboard-trader.png)

撮り方（3012 は触らない）:

```bash
cd dashboard && AIL_DEMO=1 AIL_PORT=3019 .venv/bin/python -m app.main     # デモは dashboard/demo/live を読む
# playwright（node）で幅 1280・fullPage。5 秒ごとの部分更新を 1 回通してから撮る
```

- ✅ **titan で pytest が極端に遅かった（144 件で 487 秒【実測 2026-09-18】）のは直した（146 件で 7.7 秒【実測】）**。デモのせいではなかった（当初の「デモがポートを取り合う」は誤り）。原因【実測】: titan の WSL2 は `networkingMode=mirrored` で、⚠ **閉じたループバックのポートへの接続が拒否されずタイムアウトする**（`127.0.0.1:9` へ繋ぐと `timed out`）。監視を要らないテスト 16 本が `create_app(settings)` のまま `TestClient` を開いて監視を起こし、`TT_REST_BASE=http://127.0.0.1:1` への認証で `ttclient` の 30 秒を 1 本ずつ待ち切っていた。直し方: 16 本を `start_monitors=False` にし、⚠ **`tests/conftest.py` に番人を置いた（テストで監視を起こすと落ちる）**。停止のテストだけは取消を実際に送るので、テストの client の待ちを 0.5 秒にした（操作のクライアントは待ち時間を監視のものから借りる。⚠ **`ttclient` の既定 30 秒 ＝ 実運用の値は変えていない**）
- ✅・❌・🧪 が □ で写るのは撮影機に絵文字フォントが無いとき（titan は無い。Sx360 は `fonts-noto-color-emoji` があり、そのまま写る）
- ✅ **CSP 違反は全画面で 0 件【実測 2026-09-18】**（概要・全体の詳細・記録・判定・操作・開発。`tests/browser/confirm.mjs`）。当初は操作（`/ops`）ほかにインラインの style が 9 か所・確認ダイアログの `onsubmit` が 5 か所あり、CSP に止められていた（§15-9 で直した）

## 14. ハードの画面（2026-09-18）

vibeboard の**ハード**タブ（`/ext/hardware`）。検証が何時間も使う機械（titan ＝ WSL2 ＋ RTX 3090 Ti ＋ 32 スレッド）の
⚠ **いまの状態の写し**を出す。⚠ **監視システムではない**（通知・警報・長期の記録は持たない）
（プラン: [docs/plans/archive/vibeboard-hardware-tab.md](../plans/archive/vibeboard-hardware-tab.md)）。

⚠ **この画面は管理画面（3012）には無い。** `vibetab.py` の 4 本目のタブで、§10〜§12 と同じく
**vibeboard 本体が `/ext/<name>` で中継する**。⚠ **vibeboard 本体は改造していない**（このプロジェクト専用）。
⚠ 読み手 `dashboard/hwstat.py` と画面 `dashboard/hwview.py` は **`dashboard/app/` の外**に置く（`app/` は g3plus に載る。これは開発機の話）。

> この図の主張: ⚠ **値を読むのは sidecar の中の見張り 1 本だけ。** 画面は写しを取りに来るだけで、見る人が増えても `nvidia-smi` を叩く回数は変わらない。

```mermaid
flowchart LR
  B["ブラウザ<br/>vibeboard :3010"] -->|"/ext/hardware/..."| V["vibeboard 本体<br/>（中継のみ）"]
  V --> T["vibetab.py :3015<br/>/hardware"]
  S["見張り hwstat.Sampler<br/>5 秒おき・スレッド 1 本"] -->|"最新の 1 件 ＋ 輪 720 点"| T
  S --> N["nvidia-smi<br/>固定の引数・shell なし"]
  S --> P["/proc/loadavg・stat・meminfo<br/>/proc/&lt;pid&gt;/cmdline"]
  S --> D["shutil.disk_usage<br/>/ と /mnt/c"]
```

### 14-1. 規約

| # | 規約 | ⚠ 理由 |
| ---: | --- | --- |
| 1 | ⚠ **読むだけ・写すだけ。** `nvidia-smi` は固定の引数の配列で起こす（shell を通さない・要求の値を混ぜない）。このサーバに「操作」を足さない | 中継後はループバック発に見える（CLAUDE.md の customTabs の注意）。読むだけなら、それで開く面が無い |
| 2 | **値を読むのは見張り（`hwstat.Sampler`）1 本**。`vibetab.py` の `main()` が bind できた後で起こす。⚠ **import しただけ・handler を作っただけでは起きない**（止まっている間は要求のたびにその場で読む） | ⚠ **CPU 使用率は `/proc/stat` の 2 時点の差**でしか出ない。要求のたびに読むと見る人数 × 回数だけ `nvidia-smi` が走る |
| 3 | **更新は画面が自前で行う**: `view` の script が `api/snapshot`・`api/history` を間隔ごとに相対パスで取り、描き直す。`document.hidden` の間は取りに行かない。`api/watch`（SSE）は繋がるだけで何も投げない | `item-changed` で数秒ごとに iframe を作り直すと、ちらつきとスクロール位置の初期化が起きる。vibeboard の README の契約が自前更新を認めている |
| 4 | ⚠ **描く経路は 1 本**: 値は `<script type="application/json">` に埋め（`<` は `\u003c` に逃がす）、最初の表示も更新も同じ関数が描く。⚠ **文字は `textContent` だけで入れる（`innerHTML` に連結しない）** | プロセスの cmdline は他人が決められる文字列で、XSS の入口になる。テストが「生の `<script>` が HTML に出ない」「`innerHTML` を使っていない」を固定する |
| 5 | ⚠ **読めないものは欄を作らず、理由を 1 行書く。** `nvidia-smi` が無い ／ 3 秒で返らない ／ 終了コード ≠ 0 → GPU の節が「読めない（理由）」になり、⚠ **CPU・メモリ・ディスクは出し続ける**。`[N/A]`・`[Not Supported]` は `None`（「—」）で、⚠ **0 にしない** | 「GPU が無い」と「画面が壊れている」を混ぜない（§12-1 の規約 6 と同じ立て方）。仮の数字で埋めない |
| 6 | ⚠ **色を付ける閾値を自分で作らない。** GPU の異常は `nvidia-smi` の `clocks_throttle_reasons.*`（熱・電力で絞られているか）を写す。例外はディスクだけで、**使用率 90% 以上を警告色 ＋「⚠ 残りわずか」の文字**にする（⚠ 90% は【推測】の目安で、出典は無い） | 「83℃ で危険」のような数字に出典が無い。ドライバが言っている事実を写すほうが確か。警告は色だけに頼らない |
| 7 | ⚠ **cmdline は 160 字で切り、`token`・`secret`・`passw`・`credential`・`key` を含む引数は値を伏せる**（`--token=x`・`API_KEY=x`・`--password x`） | ⚠ **このタブは tailnet の閲覧者にも見える**（`http://titan-income-vibeboard`）。引数に秘密が紛れても出さない |
| 8 | 履歴は ⚠ **メモリ上の輪だけ**（720 点 ＝ 5 秒 × 1 時間）。⚠ **ディスクに書かない。** sidecar を入れ直すと消える | 知りたいのは「席を外している間 GPU は回っていたか」。置き場・保持期間・git 管理外の設定が要らない |
| 9 | 間隔は `AIL_HW_INTERVAL_S`（既定 5 秒・下限 1 秒） | `nvidia-smi` 1 回 0.058 秒【実測 2026-09-18】× 2 本 ÷ 5 秒 ≒ 1 コアの約 2%【実測からの計算】。0 ではないので伸ばせる形にしておく |
| 10 | 画面の `api/snapshot`・`api/history` の要求は sidecar のログに出さない | 数秒おきに来るので、vibeboard のログが埋まる |

### 14-2. 何をどこから写すか

| 節 | 出す値 | 読む場所 |
| --- | --- | --- |
| GPU | 使用率 % ／ メモリ（使用・全体 MiB・%）／ 温度 ℃ ／ 電力 W と上限 ／ ファン % ／ P-state ／ 絞りの理由（立っているものだけ） | `nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed,pstate,clocks_throttle_reasons.{hw_slowdown,hw_thermal_slowdown,sw_thermal_slowdown,hw_power_brake_slowdown,sw_power_cap} --format=csv,noheader,nounits` |
| GPU を使っているプロセス | PID ／ 経過 ／ RSS ／ cmdline（規約 7） | `nvidia-smi --query-compute-apps=pid` ＋ `/proc/<pid>/{cmdline,stat,status}`・`/proc/uptime` |
| CPU | 全体の使用率 % ／ load average ／ スレッド別の使用率（細い縦棒 ＋ 表） | `/proc/stat`（2 時点の差。busy ＝ total − idle − iowait）・`/proc/loadavg` |
| メモリ | 使用（＝ 全体 − `MemAvailable`）／ 空き ／ swap | `/proc/meminfo`。⚠ **`MemFree` は使わない**（キャッシュを空きに数えない） |
| ディスク | マウントごとの 使用 %・使用・空き・全体（`/`・`/mnt/c`。無いものは飛ばす） | `shutil.disk_usage` |
| この 1 時間（`history`） | GPU 使用率・GPU メモリ %・GPU 温度・GPU 電力・CPU 使用率・メモリ % の 6 本。⚠ **1 系列 1 枚**（単位が違うものを 1 枚に重ねない ＝ 縦軸を 2 本にしない）。横軸は貯まったぶん（下限 5 分）。値の無い回は線を切る（直線で埋めない）。表（最新・最小・平均・最大）を併置 | 見張りの輪 |

⚠ **列名は旧名 `clocks_throttle_reasons.*` で引く。** ドライバ 610.62 は新名 `clocks_event_reasons.*` と両方を受ける【実測 2026-09-18】。古いドライバは旧名しか知らない。

| 経路 | 中身 |
| --- | --- |
| `/hardware/api/sidebar` | `now`「いまの状態」・`history`「この 1 時間」 |
| `/hardware/view?item=<now\|history>` | 枠 ＋ 埋め込みの JSON ＋ script。知らない id は 404 |
| `/hardware/api/snapshot` | 最新の 1 件（JSON） |
| `/hardware/api/history` | 輪の中身（JSON。系列の定義と点） |
| `/hardware/api/watch` | SSE（hello と ping だけ） |

### 14-3. ⚠ この画面で読めないもの（WSL2 の制限。【実測 2026-09-18】）

| 読めないもの | なぜ | 代わり |
| --- | --- | --- |
| プロセス別の GPU メモリ | `--query-compute-apps` の `used_memory` が `[N/A]` | 合計（`memory.used`）だけ出す。⚠ **合計には Windows 側の使用分も混ざる**と画面に書く |
| GPU のプロセス名 | 同じく `process_name` が `[Not Found]` | ⚠ **PID は WSL 側の PID と一致する**ので `/proc/<pid>/cmdline` から引く |
| Windows 側で GPU を使っているプロセス | WSL からは見えない | Windows のタスクマネージャー |
| CPU の温度 | WSL2 に `sensors` が無い | Windows 側の HWiNFO など |
| `temperature.gpu.tlimit`（熱の上限までの余裕） | `[N/A]` | 絞りの理由（規約 6）を写す |

### 14-4. ⚠ 反映には vibeboard の入れ直しが要る

vibeboard は `vibeboard.config.json` を**起動時にしか読まない**。タブを足した・`vibetab.py` / `hwstat.py` / `hwview.py` を直したときは
`./run-vibeboard.sh` を入れ直す（sidecar は vibeboard の子なので一緒に入れ替わる）。⚠ **3015 に古い sidecar が居座る罠は §12-3 と同じ**。
手元で確かめるだけなら、別のポートにもう 1 本立てれば 3010・3015 に触らずに済む。

```bash
python3 dashboard/vibetab.py --port 3016               # http://127.0.0.1:3016/hardware/view?item=now
python3 dashboard/hwstat.py                            # 読み手だけを 1 回（JSON を標準出力へ）
```

## 15. デザイン規約（2026-09-18）

⚠ **管理画面の見た目の決めごとの正本は本節である。** 黒ベースに作り直した経緯と当時の 5 画面は [dashboard-dark-design.md](../plans/archive/dashboard-dark-design.md) に残す（経緯であって正本ではない）。
⚠ **値は `app/static/app.css` の変数名で書く**（色コードを本節に写さない。食い違ったらコードが正で、本節の変数名を直す）。

> この図の主張: ⚠ **意味を持つ色は「環境」と「状態」の 2 系統で、互いに流用しない。トレーダーを見分ける系列の色（§15-2 の 5）はそのどちらとも取り違えない色相から取る。** それ以外の区別は枠や影ではなく、面の明るさの段で付ける。

```mermaid
flowchart LR
  subgraph layers["面の明るさの段"]
    BG["--bg<br/>地"] --> SF["--surface<br/>帯・環境ブロック"] --> RS["--raised<br/>カード"]
  end
  ENV["環境の色<br/>--cert / --prod / --mock"] --> E1["左の帯・バッジ<br/>停止ボタン"]
  ST["状態の色<br/>--ok / --warn / --ng ＋ 灰"] --> S1["チップ・セル・数字の色"]
  SR["系列の色<br/>--series-1〜3"] --> R1["トレーダーの線・点・段<br/>⚠ 名前を必ず添える"]
```

### 15-1. 面と文字

| 変数 | 用途 |
| --- | --- |
| `--bg` | 画面の地・上部の帯。`code` ／ `pre` の地も同じ段 |
| `--surface` | 環境の帯（`.envbar`）・環境ブロック（`.env`） |
| `--raised` | カード（`.card`）・スクロール枠の固定見出し |
| `--hover` | 行のホバー・灰のチップ |
| `--line` ／ `--line-strong` | 罫線 ／ 強い罫線（ボタン・入力の枠・環境ブロックの帯の既定） |
| `--ink` ／ `--muted` ／ `--sub` | 文字 ／ 薄い文字（表の見出し・補足） ／ さらに薄い文字（フッタ・版） |
| `--accent` ／ `--accent-bg` | リンク・選択中のナビ・`/experiments` の最良の行 ／ 主ボタン。⚠ **状態の意味を持たせない**（青は「押せる」「選ばれている」だけ） |
| `--mono` | 等幅（`code`・口座の項目名・事象名） |

### 15-2. 色の 2 系統（＋ トレーダーの系列の色）

| 系統 | 変数 | 部品（クラス） | 意味 |
| --- | --- | --- | --- |
| 環境 | `--cert`（`--cert-bg`） | `.badge-cert`・`.env-cert`（左の帯 4px ＋ 見出しの地） | sandbox（cert） |
| 環境 | `--prod`（`--prod-bg`） | `.badge-prod`・`.env-prod`・`.btn-danger`（■ 停止） | 本番（実弾）。⚠ **停止ボタンも同じ赤** |
| 環境 | `--mock`（`--mock-bg`） | `.badge-mock`（MOCK ／ TEST）・`.demobar` | 実物ではない（接続先を差し替えた記録・デモ・試験用のトレーダー） |
| 状態 | `--ok`（`--ok-bg` ／ `--ok-ink`） | `.st-*` のチップ・`.cell-ok`・`.ok` | 良い・通った・つながっている |
| 状態 | `--warn`（同） | `.cell-warn`・`.warn`・`.note`・`.flash`・`.badge-gate`（門前） | 注意・途中 |
| 状態 | `--ng`（同） | `.cell-ng`・`.ng`・`.danger` | 悪い・失敗・停止条件 |
| 状態 | 灰（`--hover`・`.cell-na` の薄い白） | `.st-off` ／ `.st-Cancelled` ／ `.st-done`・`.cell-na` | 該当なし・取消・対象外 |

1. ⚠ **環境の色を状態に使わない。状態の色を環境に使わない。** `--prod` と `--ng` はどちらも赤だが別の変数で、意味が違う（prod ＝ どこに繋がっているか、ng ＝ 結果が悪い）
2. 状態の色は **半透明の地（`-bg`）＋ 明るい文字（`-ink`）** で使う。塗りつぶすのは環境のバッジと停止ボタンだけ
3. 門前（`.badge-gate`）は琥珀。⚠ **紫（MOCK・配線の検査）と取り違えない**
4. 損益の色: ⚠ **実売買の画面（概要・トレーダーの詳細）の損益には色を付けない**（§13-4。判定は執行の差と無人運転）。`/experiments` のスコアは正を `--ok-ink`、負を `--muted` にする（⚠ 負を赤にしない）

5. ⚠ **系列の色（トレーダー。2026-09-18）**: `--series-1` 水色 ／ `--series-2` 藍 ／ `--series-3` くすんだ薔薇。**状態（緑・琥珀・赤）・環境（cert 緑・prod 赤・MOCK 紫）・アクセントの青と取り違えない色相**から総当たりで選び、dataviz の `validate_palette.js --pairs all`（面 `--surface`）で全項目 PASS【実測】。⚠ 予約色との差は正常視で 10 前後しか取れないので、⚠ **名前を必ず添える**（凡例・端のラベル・段の見出し）。⚠ **この条件で見分けられるのは 3 色まで** → 4 人目からは同じ色を線の形（`.dash`）で分ける。並び順は設定の順（色は順に付く。損益の順にしない）
6. ⚠ **仮データ（2026-09-18）**: 本物の数字ではないものは `.chip.placeholder` ／ `.badge-placeholder`（紫の破線の枠。MOCK と同じ「実物ではない」）と点線で示す（§15-8）

### 15-3. 数字と表

| 項目 | 決め | クラス |
| --- | --- | --- |
| 等幅数字 | 数字は `tabular-nums` で桁を揃える | `.num`・`td.num`・`.kvt`・`.exp-cards .v` |
| 右寄せ | 数量・価格・bp・件数は右寄せ | `td.num` ／ `th.num` |
| 折り返し（表全体） | 1 行 1 件で横に読む表（注文・日次）は折り返さない | `table.orders` |
| 折り返し（列だけ） | 短い値の列（予算・合成・株数・損益・最終日）だけ止める | `td.nw` ／ `th.nw` |
| 横にはみ出す表 | 折り返さない表は横スクロールの枠に入れる | `.scroll-x` |
| 縦に伸びる一覧 | 通知・事象は高さ 240px の枠でスクロールし、見出しを固定する | `.scroll` |
| 数字のカード | 項目名（小さい大文字）・値（24px）・補足の 3 段 | `.exp-cards .card` の `.k` ／ `.v` ／ `.small` |
| 見出しの中の項目 | 監視の環境ブロックの見出しの中だけで使う（inline-flex） | `.kv`。⚠ **表やカードの中で使わない**（§15-4） |

### 15-4. 実売買の画面のクラスと差 1 の色分け（2026-09-18 に 3 画面へ作り直した）

⚠ **差 1 の閾値の正本は [プラン §2-4](../plans/live-trading-three-models.md) の表（[live-trading.md §0-2](experiments/live-trading.md) の「判定の閾値」が参照するもの）。本節は「どのクラスに写すか」だけを書く。**

| 部品 | クラス | 規則 |
| --- | --- | --- |
| 監視の帯（概要） | `.strip`（`strip_panel.html`） | 5 秒ごとに `/?partial=strip` で差し替え。詳しくは全体の詳細の「口座と接続」 |
| 大きな数字 ＋ 3 つの数字（概要） | `.hero-row` の `.hero` ／ `.stat` | ⚠ **大きな数字は 1 画面に 1 つだけ**（全トレーダーの損益。色なし）。差 1 は `.chip` の状態の色 |
| 損益の推移（概要） | `.panel` ＋ `charts.pnl_overview` | 凡例 ＋ 端のラベル（名前つき）。0 の基準線。勝ち負けの色なし |
| 執行の差（概要） | `.panel` ＋ `charts.diff1_dots` | 背景は閾値の帯（状態の色）。点はトレーダーの色。差 3 は `.chip.placeholder`（仮データ） |
| トレーダーの段（概要） | `.lanes` ／ `.lane.bl-s*`（左の帯がトレーダーの色）＋ `charts.pnl_lane` ＋ `charts.action_grid` | ⚠ **縦軸は全員で揃える**。時間の軸も段どうしで揃える。試験用は `.badge-mock`「TEST」 |
| 行動のマス目 | `c-hold` ／ `c-buy` ／ `c-sell`（トレーダーの色の濃淡 ＋ 「買」「売」）・`c-skip`（見）・`c-none`・`c-nostart`（✕。状態の赤） | ⚠ **買い・売りに状態の色を使わない**（良い悪いではない） |
| 日次の表（全体の詳細） | `table.small.orders` ・起動しなかった日は `tr.row-missing` | 約定: 全部約定 `cell-ok` ／ 一部 `cell-warn` ／ 注文なし `cell-na`。問題: 1 件以上 `cell-ng` ／ 0 件 `cell-na`。差 1 中央値: 下の表 |
| 注文の履歴（全体の詳細・トレーダーの詳細） | `orders_history.html` | 状態のセルは `cell-*`。対応は `live.py` の `STATUS_CLASS`（Filled → ok ／ Cancelled・Expired・guarded・halted → warn ／ Rejected・error・not_submitted → ng ／ dry-run・planned → na）。内部移転は `cell-na` |
| トレーダーの詳細のタイル | `.tiles` ／ `.tile` | 差 3 は `.chip.placeholder`（仮データ） |
| 含み損が予算の 20% 超（トレーダーの詳細） | `.danger`「⚠ 含み損 …（停止条件）」 | 損益そのものには色を付けない（§15-2 の 4） |

| 差 1 の中央値 m（bp。⚠ 正 ＝ 不利） | カード（`.v`） | 日次のセル |
| --- | --- | --- |
| m ≤ 5 | `ok`（✅） | `cell-ok` |
| 5 ＜ m ≤ 10 | `warn`（⚠） | `cell-warn` |
| m ＞ 10 | `ng`（❌） | `cell-ng` |
| 無い（約定が無い日） | 色なし（「—」） | `cell-na` |

- ⚠ **色を付けるのは中央値だけ**（1 件ごとの差 1 と日次の「差 1 最大」には付けない）。判定の閾値が中央値に対するものだから
- ⚠ **閾値の 5 と 10 はテンプレートに直に書いてある**（`overview.html`・`overall.html`・`trader.html`・`charts.py` の帯）。変えるときは プラン §2-4 → §13-2 → テンプレートと `charts.py` の順に直す
- `kv`（inline-flex）は監視の環境ブロックの見出しの中だけで使う（表やカードの中では項目名と値がくっついて崩れた。2026-09-18）

### 15-5. グラフ（サーバで組む SVG。2026-09-18）

| 決め | 中身 |
| --- | --- |
| 描き方 | ⚠ **`app/charts.py` がサーバで `<svg>` を組む**。スクリプトもインラインの style も使わない（CSP の内。幾何は SVG の属性、色はクラス） |
| 呼び方 | テンプレートから Jinja の関数（`charts.*`）として呼ぶ。⚠ **`Redactor` を通した後のデータから描く** |
| hover | SVG の `<title>` だけ（日ごとの透明な帯に全系列の値。十字線は無い） |
| 軸 | ⚠ **1 軸だけ**。トレーダーを並べる小さな図は縦軸を揃える（`pnl_domain`）。損益は予算に対する %（$ はツールチップと見出し） |
| 欠けた日 | 起動しなかった日は線を切り、`--ng-bg` の帯で示す |
| 見本 | `docs/plans/assets/dashboard-patterns/`（`build.py` の関数を移した） |

### 15-6. 左ペイン（2026-09-18）

`.shell`（2 列の grid）・`.side`（上に張り付く縦のナビ）・`.side-nav .grp`（見出し）・`.side-nav a.on`（いまのページ。左に `--accent` の線）。
トレーダーは系列の色の点つきで並べ、押すと `/traders/<name>`。⚠ **ローカル面だけの項目（操作）は公開面では出さない**。停止ボタンは右の上（`.envbar`）に残す。

### 15-7. 監視の帯と大きな数字（2026-09-18）

- 監視のタイル 6 枚は、概要では **帯 1 本**（`.strip`）に畳む。⚠ 異常のときに目立ちにくいので、未認証・切断は ⚠ ／ ❌ の記号と文字で出す
- 大きな数字（`.hero .v` 52px）は 1 画面に 1 つだけ。並べる数字は `.stat .v`（30px）。大きな数字は proportional の数字、表の列だけ `tabular-nums`

### 15-8. 仮データの印（2026-09-18。利用者の指示「データが無いものは仮データを入れ、後で実装する」）

| 項目 | 仮データ | 印 | 本物にするタスク |
| --- | --- | --- | --- |
| 紙上の損益（トレーダーの詳細） | 実物の損益に 1 営業日あたり 2bp（予算に対して）を足した線（`live.PAPER_PLACEHOLDER_BP_PER_DAY`） | 点線 ＋ 凡例「紙上 仮データ」＋ 端のラベル「紙上・仮」 | 「紙上の損益と差 3 を本物にする」 |
| 差 3 紙上 − 実物（概要・トレーダーの詳細） | 上の傾き（2.0 bp/日） | `.chip.placeholder`「仮データ」。⚠ **状態の色（✅ など）を付けない** | 同上 |
| 営業日の暦（起動しなかった日） | ✅ **2026-09-18 に本物にした**（下の「営業日の暦」）。⚠ 暦に載っていない年だけ、平日をすべて営業日とみなす | 暦の外のときだけ `.chip.placeholder`「仮」＋「休場日の暦の外」 | —（年に 1 度、次の年を足す） |

⚠ **仮データは `/api/live` に出さない**（`board()` だけが持つ。テストで固定）。⚠ 本物に差し替えたら印を外す。

**営業日の暦（2026-09-18）。主張: 暦は 1 つのファイルに持ち、管理画面と執行器が同じ読み手を通して使う。**

```mermaid
flowchart LR
  N["NYSE の公表（2026〜2028）"] -->|"手で写す ＋ 規則と突き合わせ"| T["nyse_calendar.py"]
  T --> M["market_calendar.py"]
  M --> D["管理画面: 起動しなかった日・判定の営業日と市場時間"]
  M --> E["執行器: 執行の窓（休場・半日は拒否）"]
  M -.->|"D16 で使う"| C["timer ／ cron の起動日"]
```

- 正本は `experiments/tastytrade-api-sample/nyse_calendar.py`【公表値】NYSE "Holidays & Trading Hours"（https://www.nyse.com/markets/hours-calendars 。取得 2026-09-18）。休場 29 日（2026 年 10・2027 年 10・2028 年 9。⚠ 2028-01-01 は土曜で振替なし）と半日立会 5 日（13:00 ET 引け）
- ⚠ **データを `.py` に置くのは §7 の契約のため**（コンテナはサンプルの `*.py` と `*.sh` しか COPY しない。`.toml` にすると公開面に載らない）
- 読み手は同じ場所の `market_calendar.py`（標準ライブラリだけ）。管理画面は `live.business_days`・`live.calendar_info`・`judge.in_market_hours`・`judge.business_date` から、執行器は `run_day.window_refusal` から使う
- ⚠ **手で写した**ので、テスト（`tests/test_market_calendar.py`）が規則（第 n 月曜・聖金曜日・土日の振替）から独立に計算した日付と突き合わせる。⚠ **WebFetch の要約は誤っていた**（2026-07-03 を半日・2026-12-25 を欠落など）ので、生の HTML の表と脚注を読んだ
- ⚠ **年に 1 度、次の年を足す**。暦の終わり（2028-12-31）まで 90 日を切ると、全体の詳細に注意が出る（`live.CALENDAR_WARN_DAYS`）。切れた年は「仮」の印つきで平日扱いに戻る（⚠ 黙って戻さない）
- ⚠ 暦の情報（`board()["calendar"]`）も `/api/live` には出さない


### 15-9. インラインを書かない（2026-09-18）

**主張: CSP は緩めない。スクリプトは `app.js`、見た目は `app.css` に置き、templates には属性で意図だけを書く。**

CSP は `script-src 'self'; style-src 'self'`（`app/main.py` の `SecurityHeaders`）。⚠ **templates に `on*="…"` や `style="…"` を書くと、ブラウザが黙って止める**（画面は崩れず、エラーはコンソールにしか出ない）。2026-09-18 までは確認ダイアログ 5 か所（停止 × 2・解除・発注・後片付け）が `onsubmit="return confirm(…)"` で書かれており、⚠ **押すと確かめずに送られていた**。

```mermaid
flowchart LR
  B["button を押す"] --> E["form の submit"]
  E --> J["app.js: data-confirm があるか"]
  J -->|"なし"| G["そのまま送る"]
  J -->|"あり"| D["confirm(文面)"]
  D -->|"OK"| G
  D -->|"キャンセル"| X["送らない"]
```

| 書きたいもの | 書き方 |
| --- | --- |
| 送る前に確かめる | form に `data-confirm="文面"`（`app.js` が `document` の `submit` で捕まえる。⚠ 部分更新で差し替わった form にも効く） |
| 余白・枠・幅 | `app.css` のクラス（`.mt-12`・`.mb-10`・`.card-thick`・`th.w-40`。足りなければクラスを足す） |
| グラフ | サーバで組む SVG（§15-5。属性だけで描く。`style=` を使わない） |

- ⚠ **確認ダイアログは 2 つ目の歯止め**（押し間違いを防ぐ）。1 つ目はサーバ側（CSRF・面の規則・本番の発注の確認文）で、こちらは変えていない
- テスト: pytest が templates の全ファイルと描画した画面を走査し、インラインが 1 つでもあれば落ちる（`test_app.py`）。ブラウザでの動き（出る ／ 断ると送られない ／ 受けると送られる ／ CSP 違反 0 件）は `tests/browser/confirm.mjs`（playwright・node。⚠ **pytest には入れない** ＝ dashboard の依存に playwright を足さない。⚠ **POST はブラウザ側で止める**ので `HALT` は書かれない）


## 9. 更新履歴

- 2026-09-05: 初版（Phase 1〜4 の実装、デプロイ契約）
- 2026-09-07: g3plus で初回起動（§7 の契約どおり。loopback 面・資格情報なしで healthy、ホストポート非公開、非ループバック 403）。資格情報と Cloudflare は未（利用者）
- 2026-09-08: デモ（§6-2）。鍵なしでモックのデータを全画面に出す。pytest 24 件（デモ 5 件を追加）
- 2026-09-08: **検証の画面**（§10）。`/experiments` に特徴量の発見手法の検証を、種類ごとのタイトルと比較できるスコア（最良手法の純利 bp）で並べる。検査（fold の符号・上乗せ t・実効標本数・デフレーテッド SR）は**実験側が `checks.json` に書いたものを読むだけ**。プランは [docs/plans/archive/dashboard-experiments.md](../plans/archive/dashboard-experiments.md)
- 2026-09-08: 黒ベースに作り直し（`app/static/app.css` 全面。環境の色 cert 緑 / prod 赤 / MOCK 紫 と、状態の色 ok / warn / ng の 2 系統。監視は幅があれば cert と prod を横に並べ、注文表は折り返さず、口座ストリーマの通知は枠の中でスクロール）。プランと画面は [docs/plans/archive/dashboard-dark-design.md](../plans/archive/dashboard-dark-design.md)
- 2026-09-09: **データの画面**（§11）。`/data` に実験が保持しているデータの在庫（足・外部系列・特徴量・規約・割り当て）を出す。数字は実験側の manifest / config の写しで、ずらし幅と規約の判定は `config/sources.toml`（新設。コードとの一致は実験側のテストが固定）。プランは [docs/plans/archive/dashboard-data-inventory.md](../plans/archive/dashboard-data-inventory.md)
- 2026-09-18: **実売買の画面**（§13）。`/live` にトレーダー別の予算・モデル・建玉・損益と、執行の差（差 1〜4）の直近 20 営業日を出す。執行器（`experiments/live-trading/`）の記録を写すだけで、画面で計算するのは差 1 の bp だけ。両面で読める・POST は無い。pytest 7 件
- 2026-09-12: **用語の画面**（§12）。vibeboard に「用語」タブを足し、8 分野 87 語の索引を出す。⚠ **正本は `dashboard/glossary.toml`** で、画面は写し。語からその定義がある spec へ `target="_top"` のリンクで飛ぶ（⚠ **節へは飛べないので節は文字で併記**）。⚠ **リンク先の実在はテストが固定する**。プランは [docs/plans/archive/vibeboard-glossary.md](../plans/archive/vibeboard-glossary.md)
- 2026-09-18: **ハードの画面**（§14）。vibeboard に「ハード」タブを足し、GPU（`nvidia-smi`）・CPU・メモリ・ディスクのいまの状態と、この 1 時間の折れ線 6 枚を出す。⚠ **値を読むのは sidecar の見張り 1 本**で、画面は JSON を自前で取りに来る（iframe を作り直さない）。⚠ **vibeboard 本体は改造していない**。読み手は `dashboard/hwstat.py`・画面は `dashboard/hwview.py`（`app/` の外）。pytest 25 件（読み手 23 ＋ HTTP 2）
- 2026-09-18: **デザイン規約**（§15）。黒ベースの決めごとをアーカイブしたプランから移し、正本を本節にした（⚠ 値は `app.css` の変数名で書く。コードが正）。`/live` を撮って崩れ 3 つを直し（差 1 中央値の二進の端数を 0.01bp に丸める・「執行の差」を `kv` から `exp-cards` の 5 枚へ・短い列と注文 ／ 日次の表を折り返さない）、画面を §13-5 に貼った。`/live` のクラスと差 1 の色分けは §15-4。プランは [docs/plans/archive/dashboard-design-spec.md](../plans/archive/dashboard-design-spec.md)
- 2026-09-18: **画面を作り直した**（§1・§13・§15-4〜15-8）。入口を概要（`/`。監視の帯・大きな数字・損益の推移・執行の差・トレーダーの段）にし、`/overall`（全体の詳細: 日次・注文の履歴・口座と接続）と `/traders/<name>`（トレーダーの詳細）を足した。`/live` は `/` へ転送。ナビは左ペイン。見た目はデザイン 3「数字とグラフが主役」。図は `app/charts.py`（サーバで組む SVG）、データは `live.board()`。⚠ **紙上の損益・差 3・休場日の暦は仮データ**（印を付け、`/api/live` に出さない）。デモは執行器のモックの記録を読む。監視の 1 件取消のボタンを外した。pytest 144 件（新しい画面・転送・仮データの印・起動しなかった日・公開面・デモの記録）。プランは [dashboard-design-implement.md](../plans/dashboard-design-implement.md)
- 2026-09-18: **g3plus を `809104f` に更新した**（前回は `eec106c`・2026-09-10。titan から `ssh -i ~/.ssh/id_rsa_nopass g3plus` で pull → `docker compose build` → `up -d`。前のイメージは `ail-dashboard-ail-dashboard:prev` に残した）。確認【実測】: healthy ／ コンテナ内で `/`・`/overall`・`/records`・`/judge`・`/api/live`・`/api/state` が 200、`/live` は `/` へ 302、`/ops` は 404（公開面）／ docker network 越しの JWT なしは 403 ／ 監視は cert に再接続（refresh 1 回成功・エラー 0）。⚠ 実売買の部分は契約（§7）どおり空
- 2026-09-18: **確認ダイアログとインラインの style を直した**（§15-9）。`onsubmit="return confirm(…)"` 5 か所が CSP（`script-src 'self'`）に止められ、停止・解除・発注・後片付けが確かめずに送られていた → `data-confirm` ＋ `app.js`。インラインの `style=` 9 か所は `app.css` のクラスへ。⚠ CSP は緩めていない。ブラウザで 3 つの form（概要の停止・操作の停止・後片付け）が「出る ／ 断ると送られない ／ 受けると送られる」・CSP 違反 0 件【実測】。**pytest の 487 秒も直した**（146 件で 7.7 秒。§13-5。監視を要らないテスト 16 本が監視を起こしていた。`conftest.py` に番人）。⚠ **g3plus は未デプロイ**（停止ボタンは公開面にもある）。プランは [dashboard-pytest-speed-and-confirm.md](../plans/archive/dashboard-pytest-speed-and-confirm.md)
- 2026-09-18: **休場日の暦を入れた**（§15-8「営業日の暦」）。起動しなかった日の「平日＝営業日」の仮を外し、NYSE の公表（2026〜2028 年）を `experiments/tastytrade-api-sample/nyse_calendar.py` に持った。読み手 `market_calendar.py` は管理画面（起動しなかった日・判定の営業日と市場時間）と執行器（執行の窓）が共有する。⚠ 暦の外の年だけ「仮」の印に戻る。pytest 151 件。プランは [nyse-calendar.md](../plans/archive/nyse-calendar.md)
