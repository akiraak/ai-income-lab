# vibeboard 撤去の後始末（dashboard の Python のコメント・docstring）

## 目的・背景

2026-10-17 に vibeboard を撤去した（`85931e5`）。`dashboard/` の `*.py` と `tests/*.py` のコメント・docstring に「vibeboard」が約 30 か所残っている。
いまは 外部のタブ ＝ project-manager（`~/project-manager`、8200）・中継 ＝ project-manager の別ポートの表示用サーバ（8205）の `/ext/<名前>/…`・起動 ＝ `./run-vibetab.sh`。

## 対応方針

直す場所の一覧で足りる（図にする構造は無い）。

| 対象 | 直し方 |
| --- | --- |
| 各ファイルの先頭の docstring | 「vibeboard の『〇〇』タブ」→「外部のタブ『〇〇』」 |
| 起こし方・中継 | 「vibeboard の sidecar」→ `./run-vibetab.sh`・「vibeboard 本体の中継」→ 8205 の表示用サーバ |
| hash の URL | 「project-manager が読み替える hash（vibeboard の頃と同じ形）」。⚠ 形は project-manager との約束なので値は変えない |
| `DOC_CATEGORIES` のコメント | 出どころ（`vibeboard/src/config.ts`）が消えたことを書く。値は変えない |
| 確かめられない記述（tailnet の閲覧者・ログの [name]） | 事実を足さず vibeboard の名前を外すだけ |

## 影響範囲

コメントと docstring だけ（文字列・URL・関数名・値・テストの関数名は変えない）。執行器・`config/traders`・state は触らない。

## テスト方針

- 旧版と新版の構文木を docstring を除いて比べ、コードが同じこと
- `test_vibetab`・`test_system_tab`・`test_traders_tab`・`test_models_tab`・`test_help`・`test_inventory`
