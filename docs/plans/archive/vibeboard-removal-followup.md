# vibeboard 撤去の後始末（テストと CLAUDE.md）

## 目的・背景

2026-10-17 に vibeboard を撤去した（`85931e5`）。タスク・ファイル・依頼は project-manager（`~/project-manager`、8200）、独自タブ 7 本は project-manager の「外部のタブ」（定義は `~/project-manager/projects.yaml`）。
`vibeboard.config.json` が無くなり `dashboard/tests/test_system_tab.py` が落ちる。CLAUDE.md も vibeboard がある前提で書かれている。

## 対応方針

撤去の流れを図にするほどの構造は無い（直す場所の一覧は表で足りる）。

| Step | 内容 |
| --- | --- |
| 1 | `test_system_tab_is_first_in_vibeboard_config` から `vibeboard.config.json` を読む部分を外し、`detail_label` の確認だけ残す（`test_system_detail_label`） |
| 2 | CLAUDE.md: mermaid の表示・「vibeboard の TOML」・`vibeboard/` の説明・タブの案内・3010 と棲み分け・タスクの書き方・「vibeboard のこのプロジェクト固有の運用」→「外部のタブ（project-manager）」 |
| 3 | `dashboard/tests/` と `./run-tests.sh` |

## 影響範囲

- テスト 1 本と CLAUDE.md だけ。`dashboard/` の Python の動き・`docs/` の仕様書・執行器・`prod` は触らない
- タブの並びと「baseUrl に 3012 を指定しない」はこのリポジトリのテストから外れ、`projects.yaml` 側で守る

## テスト方針

`dashboard/.venv/bin/python -m pytest -q tests` と `./run-tests.sh`。
