# 管理画面（dashboard）

売買システムの管理画面。**実運用の監視**と**開発時の検証**の両方。仕様と権限の設計は
[docs/specs/dashboard.md](../docs/specs/dashboard.md)、プランは [docs/plans/dashboard.md](../docs/plans/dashboard.md)。

`experiments/tastytrade-api-sample/` の `ttclient.py` / `record.py` を import して使う（コピーしない）。
記録（`out/*.jsonl`）をそのまま読む。

## 起動（開発機）

```bash
cd dashboard
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # AIL_AUTH_MODE=local（WSL2 → Windows のブラウザなら local、それ以外は loopback でよい）
./run.sh                      # http://127.0.0.1:3012
```

資格情報は `experiments/tastytrade-api-sample/.env` をそのまま読む（`AIL_TT_ENV_FILE` で変更可）。
記録の既定はサンプルの `out/`。監視ログ・操作の履歴・ジョブは `dashboard/data/`（git 管理外）。

## 面（誰が何をできるか）

| `AIL_AUTH_MODE` | 通す接続元 | できること |
| --- | --- | --- |
| `loopback`（既定） | ループバック | 全部 |
| `local` | ループバック ＋ RFC1918 | 全部（ヘッダに「認証なし」と出る） |
| `cloudflare` | Cloudflare Access の JWT を全リクエストで検証 | **監視・記録・判定の閲覧と停止だけ**。`/ops` と `/dev` は 404 |

停止ボタン ＝ `HALT` フラグ（記録ディレクトリ）＋ 働いている注文の全取消。`sample.py` もフラグがあると発注系を拒否する。

## 画面

- `/` 監視: 認証の残り時間と scope・口座・建玉・注文・現在値・websocket 2 本の状態・エラー（5 秒ごとに更新）
- `/records` 記録: 一覧 → 詳細 → 実行間の差分（所要 ms と状態遷移の揺れ）
- `/judge` 判定: 6 観点 × 会場を記録から自動生成（モックは除外）
- `/ops` 操作（ローカル）: 停止 / 解除、dry-run → 発注 → 取消 → 後片付け。prod は 2 段ロック ＋ 確認文
- `/dev` 開発（ローカル）: モックの起動、`selftest.sh`、手順の実行

## テスト

```bash
.venv/bin/python -m pytest -q tests      # 面の判定・JWT・記録と判定・秘密が応答に出ないこと・停止
```

モックに対して全部を動かすには、`experiments/tastytrade-api-sample/mock_server.py --market-data` を立て、
`TT_REST_BASE=http://127.0.0.1:8765 TT_ACCOUNT_STREAMER=ws://127.0.0.1:8766`（と偽の `TT_CLIENT_SECRET` / `TT_REFRESH_TOKEN`）で起動する。
記録には `mock: true` が付き、判定から外れる。

## g3plus に載せる

デプロイ契約は [docs/specs/dashboard.md §7](../docs/specs/dashboard.md)。デプロイ設定と手順は
g3plus-ops（private）の `ail-dashboard/` と `docs/workflows/ail-dashboard.md`。公開ホスト名・Access の設定はあちらにだけ書く。
