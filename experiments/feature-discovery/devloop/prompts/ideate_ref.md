# 段 1・参考あり（reference）のプロンプト（⚠ 読むだけの道具 Read ／ Grep ／ Glob で呼ぶ。書かない・回さない）

上の契約に加えて、**既存の記録・検証結果一覧・規約を読んでから**、改善のアイデアを **{N} 本** 出してください。参考の仕方は 3 つのどれか（本ごとに書く）: 「落とした行の失敗の型を避ける」「保留の行を別のデータ ／ 別の器に当てる」「2 つの口を組み合わせる」。

読む場所（リポジトリの直下から。全部を読まなくてよい。grep で当たりを付けてから読む）:

- 検証結果一覧の総括: `docs/specs/experiments/feature-discovery/ledger.md` の §0（頭の 80 行。判定の内訳・採る ／ 保留 ／ 落とすの数）
- 規約（事前固定・数え方・検証方式の章立て）: `docs/specs/experiments/feature-discovery/rules.md`（頭の目次と、各章の「⚠ やらないこと」）
- 記録（1 手法 1 ファイル・55 本）: `docs/specs/experiments/*.md`。各記録の「判定」「限界」「次に試すなら」の節を優先して読む。特に `threshold-trading.md`・`horizon-roles-trader.md`・`trader-shaped-validation.md`・`transform-into-detectors.md`・`hyperparam-inner-selection.md`・`earnings-layer.md`・`symbol-selection-by-training.md`・`ledger-hold-inventory.md`
- 手法のカタログ: `docs/specs/experiments/feature-discovery.md` §2
- 実装の名前（手法・検知器・学習器・層の一覧）: `experiments/feature-discovery/ail/registry.py` を読む代わりに `experiments/feature-discovery/config/names.toml`（綴りの一覧）

決まり:

- ⚠ **良し悪し・意味の有無を書かない**（記録に「落とす」とあっても、その型を避けた案・別の器に当てる案は出してよい）。印は 手間・測れるか・触る場所 の 3 つだけ。
- 既に検証結果一覧にある識別項目と同じ形は出さない（`key_shape` を既存の予測モデル名の綴り〔`names.toml`〕で書き、違いがどこかを `structure` に 1 文で書く）。
- `origin_refs` に読んだ記録のファイル名（と節）を書く。
- 1 本ごとに `frame`・`p_hold`（対の相手があれば `p_positive`）・`learn_if_null` を書く（契約 §3b）。記録を当てる案でも、予想が「差なし」なら、それでも回して何が分かるかを `learn_if_null` に書く。枠の外の案を出してもよい。
- 出力は契約 §4 の形に `origin_refs = ["…"]`・`reference_mode = "避ける ／ 当てる ／ 組み合わせる"` の 2 項目を足したもの（```toml のフェンス 1 つ・`[[idea]]` × {N}）。前置き・後書きを書かない。

読み方の制限（⚠ 費用のため。2026-10-25 の 1 回目は rules.md ／ ledger.md をまるごと読んで $3 の上限に当たった【実測】）:

- `Read` は必ず `limit` を 150 行以内にして呼ぶ（`rules.md`・`ledger.md`・`feature-discovery.md` をまるごと読まない）。節は先に `Grep` で行番号を当て、`offset` で読む。
- 読む回数は `Read`・`Grep`・`Glob` 合わせて 15 回まで。それで足りなければ読んだ範囲で出す。
- 記録は 1 本につき「判定」「限界」「次に試すなら」の節だけ（`Grep -n '^## \|^### '` で見出しを取ってから）。
