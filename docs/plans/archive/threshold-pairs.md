# 買う線と売る線を別に置く（先 10 日の 6 本で机上の検証）

> 2026-09-27 作成（titan の Claude）。利用者の指示（2026-09-27。[forward10-target.md](../../specs/experiments/forward10-target.md) §1〜§6 を読んだ後）: **「ownex × Ridge 60 の判定で買い。ownex × Ridge 50 の判定で売り」→「買いと売りの閾値を別の値にしたもので検証してください」**。
> ✅ **2026-09-27 に完了**（18 検証とも落とす・n_trials 685 → 703。記録 [forward10-target.md §7-4](../../specs/experiments/forward10-target.md)）。
> 親: [new-model-trader.md](../new-model-trader.md) Phase 2（Phase 2-1 の続き）。規約は [rules.md 18 章](../../specs/experiments/feature-discovery/rules.md)（このプランで足した）。

## 0. 目的・背景

いまの閾値売買は θ 1 つで買う線（買い% > θ）と売る線（買い% < 100 − θ）を同時に決める。「60 で買って 50 で売る」（帯 50〜60）は作れなかった。売る線を別に置く口を足し、先 10 日の 6 本（Phase 2-1）で 3 組を回して、線の置き方で結果が変わるかを見る。⚠ 損切り（買値を見る）ではない。

## 1. 対応方針

| Step | 中身 | 場所 |
| --- | --- | --- |
| 1 | 規約を先に書く（変更規約の問い・組の決め方・数え方・名前・失敗モード） | `rules.md` 18 章 |
| 2 | 事前固定と見立てを記録に書く（回す前） | `forward10-target.md` §7-1〜7-3 |
| 3 | `simulate` に任意引数 `exit_threshold`（既定は同一）・`cli/run.py` に `threshold_pairs`（無い config は経路が同一）・`names.py` に `-outN` | `ail/validation/simulate.py`・`cli/run.py`・`ail/names.py`・`config/names.toml` |
| 4 | テスト（既定は同一 ／ 意味 ／ 行が足されるだけ ／ 組の検証 ／ 綴り） | `tests/test_exit_line.py` |
| 5 | config 6 本（`features_from` ＋ `threshold_pairs`）＋ queue → 回す → 検証結果一覧 → 記録 §7-4 | `config/experiment/*_pairs.toml`・`config/queue/fwd10_pairs.toml` |
| 6 | `models.toml` の `fwd10-gate` に経緯の行を足す（`names = ["*.h1-gate10-out*"]`）→ dashboard のテスト | `dashboard/models.toml` |

## 2. 影響範囲

- 研究側のコード 3 か所（既定経路は 1 ビットも変えない ＝ `test_trading_run.py` の指紋）。執行器・管理画面のコードは 0
- 検証結果一覧: n_trials ＋18（6 本 × 3 組）。既存の行は動かない
- 本番: 0（トレーダーの θ は 1 つのまま。売る線を本番に入れるのは 10/20 の後の執行器の変更 ＝ 親プラン §7）

## 3. テスト方針

`tests/test_exit_line.py` ＋ `test_trading_run.py`（指紋）＋ `test_topk.py`・`test_names.py`・`test_pairs.py`・`test_fwd.py`。dashboard は `test_models_tab.py`（経緯の数が DB と一致）。
