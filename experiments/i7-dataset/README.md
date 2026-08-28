# I7 案 B: ソフトウェア開発の日本語評価セット — 生成パイプライン

実験ドキュメント: [docs/specs/experiments/i7-dataset.md](../../docs/specs/experiments/i7-dataset.md)
プラン: [docs/plans/archive/i7-dataset-validation.md](../../docs/plans/archive/i7-dataset-validation.md)

## 権利処理の原則（Phase 2 の結論をコードに落としたもの）

| 原則 | 理由 |
| --- | --- |
| 設問・解答の本文は **Apache 2.0 / MIT の重みを自前ホストしたモデル**（既定: Qwen3 on Ollama）か、DeepSeek / Mistral API で生成する | 出力の販売・買い手の学習利用に規約上の制約が無い |
| **Claude（Anthropic）で本文を書かない**。Claude はツール・スキーマ・プロンプト設計のみ | Anthropic 規約は「第三者の競合モデル学習の支援」も禁止 |
| OpenAI / Gemini で本文を書かない | 「競合モデル開発」への使用禁止。買い手の用途を限定できない |
| プロンプトに第三者の著作物（書籍・記事・OSS のコード）を貼らない | 享受目的の併存で 30 条の 4 が外れる |
| 全件に provenance（生成モデル・ライセンス・生成日時・プロンプト版）を付ける | 買い手への開示と限定提供データの管理 |
| 配布は認証付き（AWS Data Exchange）のみ。公開 URL に置かない | 限定提供データの電磁的管理性 |
| 人による品質検収は行わない（2026-08-26 決定）。validate.py の機械検査のみ通し、`reviewed_by_human` は false のまま **出品時にその旨を開示する** | 実験の目的は「売れるか」の実測であり、品質の作り込みではない |

## 構成

```
experiments/i7-dataset/
├── README.md          このファイル
├── topics.json        カテゴリ × 小トピック × 難易度（メタデータ。本文ではない）
├── generate.py        OpenAI 互換エンドポイントに投げて JSONL を出力する
├── validate.py        スキーマ検査・長さ・近似重複の検出とレポート
├── LICENSE-DATASET.md 販売時のライセンス条項ドラフト（⚠ 専門家確認前）
└── out/               生成物（git 管理外）
```

## 使い方

```bash
# 1. 生成モデルを用意する（既定は Ollama + Qwen3）
ollama pull qwen3:8b

# 2. パイロット 10 件
python3 experiments/i7-dataset/generate.py --n 10 --out experiments/i7-dataset/out/pilot.jsonl

# 3. 検証
python3 experiments/i7-dataset/validate.py experiments/i7-dataset/out/pilot.jsonl

# 別エンドポイント（DeepSeek / Mistral / 借りた GPU 上の vLLM など）
OPENAI_BASE_URL=https://api.deepseek.com/v1 OPENAI_API_KEY=... \
  python3 experiments/i7-dataset/generate.py --model deepseek-chat --license MIT --n 200
```

## 記録するもの（【実測】）

| 項目 | どこに書くか |
| --- | --- |
| 生成にかかった時間・件数・失敗数 | generate.py が末尾に出す summary を docs/specs/experiments/i7-dataset.md の実測ログへ |
| API 利用料 / GPU 時間 | 同上 |
| 検収にかかった人の時間 | 検収は行わないので 0 |
