# 選ぶ段に「切れ目ずらしで残るか」を併記する

2026-11-11 titan（利用者「推す案で進めて」）。派生元: [fold-cut-shift.md](fold-cut-shift.md)（上位 3 は 3 行とも「切れ目に依る」）・記録 [fold-cut-shift.md §3 の 5](../../specs/experiments/fold-cut-shift.md)。

## 1. 目的・背景

切れ目ずらし（rules.md 33 章）で、11/11 の選ぶ段の上位 3 は 3 行とも「切れ目に依る」と分かった。登録簿 `20261111-fold-cut-shift` の `learn_if_null` どおり「選ぶ段の物差しに足すか」を利用者に出し、推す案（門にはしない・新顔の晩だけ併記）で進めると決まった。

## 2. 対応方針

決めごとの正本は [rules.md 33-6](../../specs/experiments/feature-discovery/rules.md)（⚠ 次の新顔が出る前に書いた ＝ 事前固定）。

| 決めたこと | 中身 |
| --- | --- |
| 物差し | **変えない**（howto §3-1 の 6 つ・順位・上位 3・判定 13-7）。足すのは併記（診断）の 1 列だけ |
| 併記の中身 | 上位の行と識別項目が同じで検証方式だけが `閾値売買（切れ目ずらし1/3）` ／ `（切れ目ずらし2/3）` の行を検証結果一覧から引き、33-4 の 2 の読み（残る ／ 切れ目に依る）を出す。2 行そろっていなければ「—」 |
| いつ回すか | 新しく上位 3 に入った行だけ（31-3 の 3a と同じ「新顔」）。次の晩の queue に、20 章の机上（`dev_tr_*`）と一緒に `dev_cut13_*` ／ `dev_cut23_*` を足す |
| 数 | 回した行は全部 `n_trials` に数える（1 本の手法の行 ＝ ＋2・合成の行 ＝ ＋2 ＋ この実行の中のメンバーのぶん）。1 晩 ≤ 12 に数える |
| 書けない形 | 〔…〕の付いた行（上位 K・出口の線など）は機械では書かない（日誌に ⚠ ・併記は「—」） |

> この図の主張: 新顔が出た晩に config を書き、次の晩に回し、その次から併記の列に読みが出る。順位と採否には入らない。

```mermaid
flowchart LR
  N["段 4: 新顔<br/>（前の晩の上位に無い行）"] --> C["config を書く<br/>dev_tr_* ＋ dev_cut13_* ／ dev_cut23_*"]
  C --> Q["次の晩の段 3<br/>queue の先頭で回す"]
  Q --> L["検証結果一覧に<br/>~cut1of3 ／ ~cut2of3 の行"]
  L --> S["段 4 の併記の列<br/>残る ／ 切れ目に依る"]
```

## 3. 影響範囲

- `experiments/feature-discovery/cli/cut_shift.py`（新規。検証名 → 切れ目ずらしの config を機械で書く・数える行の数）
- `cli/select_candidates.py`（併記の列「切れ目ずらし」・JSON）
- `cli/devloop.py`（`select_stage` ＝ 新顔の config・`plan_queue` ＝ 持ち越しの数え方と「入りきらなければ翌晩」・日誌の 1 行）
- `config/devloop.toml`（`[select] cut_new = true`。無ければ今までと同じ）
- 文書: rules.md 33-6・31-3 の 2 ／ 3・31-5・howto §3-1・dev-loop.md・CLAUDE.md
- ⚠ 共有の経路（`cli/run.py`・`ail/validation`）は触らない。本番（13500t）には関係しない

## 4. テスト方針

- 併記: 2 行とも正で保留以上 → 残る ／ 1 行でも外れる → 切れ目に依る ／ 片方だけ・無し → 「—」／ 順位と上位は併記の有無で変わらない
- config: 本物の `sel_small4_1995` ・ `trader_mix_t4` から機械で書いたものが、手で書いた `cut13_*` と意味が同じ（`name` と絞った手法以外）／ 検証方式の変種になる ／ 〔…〕の行・変種の config は拒む
- 数: 1 本の手法 ＝ 1・合成 ＝ 1 ＋ 中のメンバー
- 運転手: `cut_new` が無ければ今までと同じ ／ 新顔で 2 本書いて持ち越しに入る ／ 既に行があれば書かない ／ 持ち越しは 1 晩 ≤ 12 に入るぶんだけ
- `./run-tests.sh` ・研究側 pytest

## 5. Step

- [x] Step 1: 事前固定（rules.md 33-6）・プラン・TODO
- [x] Step 2: `cli/cut_shift.py` ＋ 併記の列 ＋ 運転手 ＋ テスト
- [x] Step 3: 文書（31-3・31-5・howto §3-1・dev-loop.md・CLAUDE.md）・DONE・archive

## 6. 結果（2026-11-11 titan）

- 併記の列は本物の DB で出た: いまの上位 3 は 3 行とも「切れ目に依る」
- `cli.cut_shift` が機械で書いた config は、手で書いた `cut13_*` ／ `cut23_*` 4 本と意味が同じ（`name` と絞った手法以外）
- ⚠ 効くのは次に新顔が出た晩から（いまの上位 3 は新顔ではない ＝ 何も回らない）。止めるには `config/devloop.toml` の `[select] cut_new = false`
- テスト: `tests/test_cut_shift.py` 23 本

