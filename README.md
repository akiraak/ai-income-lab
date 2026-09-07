# ai-income-lab

AI を使って収入を稼ぐ方法を体系化し、**机上で検証する**プロジェクト。
一次情報の調査と試算で「その手法が成立するか / 自分の条件で採れるか」を判定するところまでを成果物とする。

## どこから読むか

**→ [docs/specs/overview.md](docs/specs/overview.md)（概要と読み方）**

目的別の読む順、ファイル早見表、記号（L1〜L3 / P1〜P6 / A1〜J4）の早見表、現在地がまとまっている。

| 入口 | 内容 |
| --- | --- |
| [docs/specs/overview.md](docs/specs/overview.md) | **全資料の入口**。読み方と現在地 |
| [docs/specs/income-taxonomy.md](docs/specs/income-taxonomy.md) | 体系本体の入口。Phase 1〜5 の要約と結論 |
| [docs/specs/experiments/](docs/specs/experiments/) | 手法ごとの調査・試算・判定の記録 |
| [TODO.md](TODO.md) / [DONE.md](DONE.md) | タスク管理 |
| [CLAUDE.md](CLAUDE.md) | プロジェクトの方針と作業ルール |

## 管理画面

| 画面 | 用途 | 起動 |
| --- | --- | --- |
| [dashboard/](dashboard/) | **売買システムの管理画面**。tastytrade の口座・注文・認証の監視、API 検証の記録と 6 観点の判定、開発時の検証。仕様は [docs/specs/dashboard.md](docs/specs/dashboard.md) | `cd dashboard && ./run.sh` → http://127.0.0.1:3012 |
| vibeboard | 開発管理画面。`docs/` と `TODO.md` の閲覧・編集 | `node vibeboard/dist/cli.js --root .` → http://localhost:3010 |
