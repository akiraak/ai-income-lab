# vibeboard で画像を見られるようにする（Plans の Markdown 内画像と、Files タブの画像プレビュー）

作成: 2026-09-08（小さな改善。着手と同時に archive）

## 目的・背景

管理画面のスクリーンショットをプランに貼って vibeboard で見たい（利用者の指示「vibeboard の plans で画像をみれるようにして」）。

## 確認と対応

- Plans / Specs のプレビューは、upstream に既にある `rewriteRelativeAssetUrls` が `<img src="assets/x.png">` を `/files/docs/plans/assets/x.png` に書き換え、`/files` が root 配下を配信するので**そのまま表示できる**（3019 で確認: img の src が `/files/...` になり、PNG が 200 で返る）
- 足りなかったのは **Files タブ**で、画像を押すと「バイナリのため編集できません」とだけ出ていた。`readOnlyReason: binary` かつ画像拡張子なら `/files/<path>` を `<img>` で表示するようにした（upstream）
- このリポジトリでは `docs/plans/assets/` に管理画面のデモ 5 画面の PNG を置き、[dashboard-demo-mode.md](dashboard-demo-mode.md) から参照した

## テスト方針

- 3019 の使い捨てインスタンスで、`#plans/dashboard-demo-mode.md` に 5 枚の画像が読み込まれること、`#files/docs/plans/assets/…png` で画像が出ることを playwright で確認
