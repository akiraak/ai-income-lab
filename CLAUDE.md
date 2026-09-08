# ai-income-lab

## プロジェクトの目的

AI を使って収入を稼ぐ方法を体系化し、**机上で検証する**ためのプロジェクト。

**2026-08-27 に方針を変更した。実際に資金・機材を動かす実行（購入・口座開設・出品・登録・リリース）は行わない。**
一次情報の調査と試算にもとづいて「その手法が成立するか / 自分の条件で採れるか」を判定するところまでを成果物とする。

**2026-09-05 に tastytrade の API 検証だけ例外を設けた（[プラン §3](docs/plans/tastytrade-api-sample.md) の選択肢 (c)）。**
この 1 手法に限り、sandbox ユーザーの作成・本口座の開設・入金・本番での 1 株の発注まで行う。ただし、

- **口座開設・入金・本番発注を実行するのは利用者**。Claude は手順とコードを示すところまでで、実行しない
- 実測するのは **API の挙動**（認証の寿命・遅延・レート制限・往復時間・約定価格と気配の差）。
  収入・費用の【実測】を取りにいくものではない
- 他の手法には広げない。同じことをしたくなったら、その手法ごとにここへ追記する

## 進め方

- 収益化の手法を洗い出し、一次情報（公表単価・稼働率・規約・法令・税制）を当たって成立条件を詰める
- 各手法について、初期コスト・投下時間・想定収入を【推測】として試算し、根拠となる出典を残す
- 判断は「実行して収入が出たか」ではなく「**一次情報と試算が成立条件を満たすか**」で行う。満たさないものは理由を残して打ち切る
- 収入・費用の【実測】は今後取得しない。過去に取得済みの【実測】（`docs/specs/experiments/i7-dataset.md` のパイロット等）はそのまま残す
  - 例外: **API の挙動**（認証の寿命・遅延・レート制限・往復時間）は 2026-09-05 から【実測】を取る（tastytrade の検証。上の例外を参照）

## Claude への依頼方針

- アイデアを出すときは、必要な作業量・初期コスト・収益化までの想定期間もあわせて示す
- **金銭・契約が発生する行動は提案と試算までとし、実行しない**。手順を示すのは可、代行や実行は不可
- 数値（収入、費用、期間）を書くときは、**【実測】/【公表値】/【推測】** のどれかを必ず明示する。根拠のない金額を断定しない
  - 【公表値】は出典（URL・取得日）を併記する。出典が取れないものは【推測】として扱う
- 法規制・各サービスの利用規約（AI 生成物の扱い、アフィリエイト規約など）に触れる施策は、その旨を先に指摘する

## ドキュメントの書き方

- 説明が構造・流れ・位置関係を含むときは、文章だけで済ませず **図を使う**（Mermaid で書く）
  - vibeboard が mermaid@11 でレンダリングするので、` ```mermaid ` フェンスで書けばそのまま閲覧できる
  - プランや仕様書では、各層・各フェーズに最低 1 枚は図を置く
- 図の原則
  - **1 図 1 主張**。その図で何を言いたいのかを図の直前に 1 行書く
  - **一覧や属性は表**、図は関係・流れ・位置を示すときだけ使う
  - ノードは 12 個以内。超えたら図を分割する
  - 本文と図が食い違ったら本文を正として図を直す

## 現状

ドキュメント中心。アプリ実装は `dashboard/`（売買システムの管理画面。2026-09-05）が最初の 1 つ。

- `TODO.md` / `DONE.md` — タスク管理
- `docs/plans/` — 作業プラン（完了したものは `docs/plans/archive/` へ）
- `docs/specs/` — 成果物となる仕様・体系
- `docs/specs/experiments/` — 検証タスクごとの調査結果・試算・判定の記録（1 手法 1 ファイル）
- `experiments/` — 調査用コード（1 手法 1 ディレクトリ）。2026-08-27 の方針変更以降は新規追加の予定なし
- `dashboard/` — **売買システムの管理画面**（実運用の監視 ＋ 開発時の検証）。仕様は `docs/specs/dashboard.md`
- `vibeboard/` — 開発管理画面（vendor 済み）。`docs/` と `TODO.md` を見るためのもので、`dashboard/` とは別物

## 管理画面 (dashboard)

```bash
cd dashboard
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                                   # AIL_AUTH_MODE=local（既定は loopback）
./run.sh                                               # http://127.0.0.1:3012
.venv/bin/python -m pytest -q tests                    # 面の判定・JWT・記録と判定・秘密が応答に出ないこと
```

- `experiments/tastytrade-api-sample/` の `ttclient.py` / `record.py` を import し、記録（`out/*.jsonl`）をそのまま読む。資格情報もサンプルの `.env` を読む
- 画面: 監視（`/`）・記録と差分（`/records`）・6 観点の自動判定（`/judge`）・操作（`/ops`）・開発（`/dev`）。**操作と開発はローカル面だけ**
- 面は `AIL_AUTH_MODE`: `loopback`（既定）/ `local`（＋ LAN）/ `cloudflare`（公開面。Access の JWT を全リクエストで検証。**監視と停止だけ**）
- **停止ボタン** ＝ 記録ディレクトリに `HALT` を書き、働いている注文を全部取り消す。`sample.py` も `HALT` があると発注系の手順を拒否する
- 本番の鍵はサンプルと同じ 3 段（dry-run `TT_ALLOW_PROD_DRY_RUN=1` / 取消 `allow_prod_cancel` / 発注 `TT_ALLOW_PROD_ORDERS=1` ＋ 確認文）。**取消の鍵で発注は開かない**
- 秘密（client secret・トークン・口座番号）はブラウザに送らない。全応答が `Redactor` を通る
- g3plus に載せる契約は `docs/specs/dashboard.md` §7。デプロイ設定・公開ホスト名・Access は **g3plus-ops（private）側にだけ書く**
- vibeboard との棲み分け: vibeboard はこのリポジトリの文書とタスクを見る**開発用**、dashboard は tastytrade の口座と記録を見る**運用用**。ポートも別（3010 / 3012）

## 実験コード

### experiments/i7-dataset（I7 案 B: 日本語評価セットの生成）

```bash
ollama pull qwen3:8b                                   # 生成モデル（Apache 2.0、自前ホスト）
python3 experiments/i7-dataset/generate.py --n 10      # 生成 → out/pilot.jsonl
python3 experiments/i7-dataset/validate.py             # スキーマ・重複・実在名の検査
```

- 本文は Apache 2.0 / MIT モデルか DeepSeek / Mistral API で生成する。**Claude・OpenAI・Gemini で本文を書かない**（権利処理の結論。`experiments/i7-dataset/README.md`）
- `out/` は git 管理外。生成物は AWS Data Exchange 等の認証付き経路でのみ配布する
- I7 の検証は 2026-08-26 に打ち切り。パイプラインは他の合成データ実験に再利用できる。Ollama サーバは停止中（使うときは `nohup ollama serve &`）

### experiments/tastytrade-api-sample（tastytrade の API 取引サンプル）

```bash
cd experiments/tastytrade-api-sample
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./selftest.sh                                          # 資格情報なしでモックに 6 手順を通す
.venv/bin/python sample.py --step all                  # cert（sandbox）で 6 手順 → out/*.jsonl
```

- 既定は sandbox（cert）。**本番では発注系（手順 4・5）を拒否する**。実弾は `TT_ALLOW_PROD_ORDERS=1` と `--i-know-this-is-real-money` の両方が要る。取消だけの鍵 `allow_prod_cancel` は管理画面の停止ボタン用（発注は開かない）
- 資格情報（`.env`）と実行記録（`out/`）は git 管理外。記録はトークン・口座番号をマスクしてから書く。接続先を差し替えた実行（selftest）は `mock: true` が付く
- `out/HALT` があると発注系の手順（4・5・5limit・6）を拒否する（管理画面の停止ボタンが置く。`TT_HALT_FILE` / `TT_OUT_DIR` で場所を変えられる）
- sandbox は相場データを配信しない（`/market-data` が全経路 502）ので、気配は本番の資格情報で読む
- 記録と判定は `docs/specs/experiments/tastytrade-api-sample.md`

## Git 運用ルール

- **作業ブランチは作らず、常に `main` 上で直接作業・コミットする**（個人プロジェクトのため、レビュー用のブランチ分岐は不要）
- Claude は「デフォルトブランチでは先にブランチを切る」という既定の挙動を持つが、**このプロジェクトではそれを行わない**
- コミット・push はユーザーから依頼されたときだけ行う

<!-- vibeboard:begin -->
## 開発管理画面 (vibeboard)

ローカル開発時のタスク・プラン管理は [vibeboard](https://github.com/akiraak/vibeboard) で行う。
プロジェクト直下に degit で vendor してある（`./vibeboard/`）。

```bash
# 親プロジェクト直下から
node vibeboard/dist/cli.js --root .
```

`http://localhost:3010` でプロジェクト直下の `docs/plans/`・`docs/specs/`・`TODO.md`・`DONE.md`・`CLAUDE.md`・`README.md` を閲覧・編集できる。

- `Files` タブでプロジェクト内のファイル（`TODO.md` / `DONE.md` / `CLAUDE.md` / `README.md` を含む）をプレビュー表示・編集できる。`TODO.md` はツリー表示つき
  - 編集は楽観ロック（mtime チェック）付き。外部で先に更新されていた場合は保存時に 409 を返し、リロード / 手元維持 / 強制上書き を選べる
  - `fs.watch` + 2 秒ポーリングで外部変更を検知し、SSE でクライアントへ即時反映する
- `Tasks` タブで `TODO.md` のタスクを、このプロジェクトで動いている Claude Code のセッションへ渡して実行できる（実行 / プラン作成 / 説明 / 削除）。
  プラン作成は `docs/plans/` のプランファイルと `TODO.md` へのリンク・子タスクだけを作らせる（実装はしない）。
  送り先は `claude agents` の一覧から選ぶ。セッションは起動時の hook（`vibeboard init` が `.claude/settings.json` に書く）で
  自分の受信口を vibeboard に登録し、vibeboard がそこへ文面を投函する。登録が無くても Linux なら `claude agents` の pid から
  受信口（`$XDG_RUNTIME_DIR/cc-socks/<pid>.sock`）を引いて投函する。hook が使えない環境では
  `node vibeboard/dist/cli.js listen --name <画面の名前>` を回す
- ローカル開発専用（本番管理画面とは独立）
- ポート変更は `--port` または `VIBEBOARD_PORT` 環境変数で指定可能
- 本体の更新は `node vibeboard/dist/cli.js update --restart`（再 degit → `npm install` → `init` → 同じ root の vibeboard の起動し直し、を 1 コマンドで）

## タスク管理ルール

- タスクは `TODO.md` で管理する
- **`TODO.md` に書くのはタスク（`- [ ]`）だけ。** メモや決定事項を残すときは、関係するタスクの
  下に字下げして付ける（タスクに関連付ける）。タスクに属さないメモの節（「決まったこと」「備考」など）は
  作らない。プロジェクトとしての決定は `CLAUDE.md` へ、済んだ経緯は `DONE.md` へ書く
- 字下げが親子。vibeboard はこれをツリーとして表示する。進行中は `[~]`、中止は `[-]` で表せる
- タスク同士の関係は、そのタスクの下に字下げした **`依存:` / `派生元:` / `関連:`** の行で書く。
  相手のタスクは `「文面」` で（例: `依存: 「スキーマに tags 列を追加」`）、プランや仕様は
  Markdown リンクで（例: `関連: [spec](docs/specs/api.md)`）示す。vibeboard のツリーで両方向に辿れる
- タスクが完了したら `TODO.md` から該当項目を削除し、`DONE.md` に移動する
- `DONE.md` には完了日を `YYYY-MM-DD` 形式で付けて記録する
- 新しいタスクが発生したら `TODO.md` の適切なセクションに追加する
- タスクの実施前に `TODO.md` を確認し、優先度の高いものから着手する
- コミット時に `TODO.md` を確認し、実装した機能に対応するタスクがあれば `DONE.md` に移動する

## 作業着手ルール

作業（実装・調査いずれも）を始めるときは、コードに手を入れる前に以下を行う。

1. **プランファイルを作成する**: `docs/plans/<task-name>.md` に実装プラン or 調査プランを作成する
   - 目的・背景、対応方針、影響範囲、テスト方針を最低限記載する
   - 複数 Phase / Step に分かれる場合はファイル内でも Phase / Step を明示する
2. **`TODO.md` に該当項目があるか確認する**
   - 無ければ適切なセクションに追加する
   - 既存項目があれば、その項目に作成したプランファイルへのリンクを追記する（例: `[plan](docs/plans/<task-name>.md)`）
3. **複数 Phase / Step がある場合は `TODO.md` に子タスクとして追加する**
   - 親項目の下にインデントしたチェックボックスで Phase / Step を列挙する
   - Phase / Step が完了するごとにチェックを入れ、全完了で親項目を `DONE.md` に移す
4. **作業完了時の後片付け**
   - 親タスクを `DONE.md` に移動する
   - 対応するプランファイルは `docs/plans/archive/` に移動する
<!-- vibeboard:end -->
