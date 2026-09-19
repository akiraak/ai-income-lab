# 管理画面のデモの記録

`AIL_DEMO=1`（または資格情報なし）で起動し、`AIL_LIVE_DIR` を指定しなかったときに、概要・全体の詳細・トレーダーの詳細が読む
**執行器のモックの記録**（`live/`）。⚠ **数字はモックの値で【実測】ではない**（記録の全行に `mock: true`、トレーダーは試験用の 3 人）。

- 作り方: `docs/plans/assets/dashboard-patterns/fixtures/run.sh <出力先>`（執行器のモックを 3 人で 20 営業日。⚠ 10-19 はわざと起動しない）の出力から、
  管理画面が読まない `quotes.jsonl` を除いて写した（2026-09-18）
- 秘密: 執行器が `Masker` を通して書いたもの。`MOCK-SECRET`・`MOCK-REFRESH`・口座番号・`eyJ` を grep して 0 件
- ⚠ g3plus には COPY しない（デプロイ契約 dashboard.md §7 の COPY の対象は `dashboard/app/` だけ）
