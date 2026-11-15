"""文字列リテラルのうち替えないもの: (ファイル名, 置き換え前の言葉)。理由つき。"""
SKIP = {
    # 実行の記録（DB。書き換えできない）の項目名 ＝ 古い記録と新しい記録で名前が割れる
    ("tuned.py", "尻"), ("test_tuned.py", "尻"), ("i20261111_meta_label.py", "尻"), ("i20261113_score_trailing_rank.py", "尻"),
    ("test_ideas_20261113.py", "尻"), ("evolve.py", "尻"),
    # 検証結果一覧の行の項目名「指紋」（catalog が作り ledger が読む）
    ("catalog.py", "指紋"),
    # 閉じる注記（config/catalog_notes.toml。替えない）の「登録簿 `id`」を読む正規表現と、その表示・テストのデータ
    ("inventoryview.py", "登録簿"), ("select_candidates.py", "登録簿"), ("test_inventory_tab.py", "登録簿"),
    # 用語集の語の名前「…旧: 台帳」・テストの中の作り物の TOML
    ("test_vibetab.py", "台帳"), ("test_models_tab.py", "台帳"), ("test_system_tab.py", "台帳"), ("report.py", "台帳"),
    # SQL のスキーマの中のコメント（DDL の文字を変えない）
    ("rundb.py", "指紋"), ("rundb.py", "道"), ("livefs.py", "道"),
}
