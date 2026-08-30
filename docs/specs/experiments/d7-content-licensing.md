# D7 既存コンテンツの AI 学習許諾 — 調査と試算

調査日: 2026-08-27 / プラン: [docs/plans/archive/d7-content-licensing.md](../../plans/archive/d7-content-licensing.md)
方針: 机上調査のみ。登録・アップロード・許諾は行っていない。
読み方: [../overview.md](../overview.md) ／ 体系: [../income-taxonomy.md](../income-taxonomy.md)

> ⚠ **2026-08-28 追記: 以下は日本居住前提で書かれている。** 体系の前提が米国（WA 州シアトル）居住に変わったため、
> **[米国居住前提での再判定](#米国居住前提での再判定2026-08-28) が本ファイルの現行の結論**である。
> 要点: **最大リスクだった「日本語話者の実例ゼロ」と「Troveo の対応国が非公表」が両方とも消え、成立側に動いた。**
> 一方で比較対象 B14 の水準が $31〜65/時 → $65〜110/時 に上がり、⚠ ElevenLabs 側の BIPA 訴訟という新しいリスクが加わった。

## 結論: **声（ElevenLabs）は成立。動画（Troveo）は保留。写真は不成立**

> 2026-08-27 追記（残り論点の調査）: **ElevenLabs は日本が対応国リストに明示されていることを一次情報で確認**した。
> 同時に、初版で書いた Troveo の「独占＝在庫を失う」は**行き過ぎた解釈だったため訂正**する（詳細は権利条件の節）。

I7・I8・C4/C5 と違い、**時間あたりの収入は判定条件を満たす**。声・動画のどちらも、
投下時間あたりで B14（AI 開発者向け受託 $31〜65/時）を上回る。

### 判定条件との照合

| # | 条件 | 結果 |
| --- | --- | --- |
| 1 | 時間あたりで B14（$31〜65/時）を上回るか | **○ 上回る**。声 $96〜1,280/時、動画 $236/時。ただし**写真は不成立**（$0.0069/枚） |
| 2 | 日本から実際に受け取れるか | **声 ○ / 動画 ⚠**。ElevenLabs は対応 56 か国のリストに **Japan を明示**。Troveo は対応国・支払方法とも**依然として非公表** |
| 3 | ⚠ 許諾の不可逆性・独占性が許容できるか | **声 ○ 条件付き**（撤回可、ただし Notice Period との引き換え）。**動画は確定できない**（後述の訂正） |
| 4 | 継続収入か一度きりか | **どちらも継続分配**。Troveo も所有権保持のロイヤリティ方式と公式に明記されている（初版の記述を訂正） |

**種類別の結論**

| 種類 | 判定 | 残る条件 |
| --- | --- | --- |
| 声（ElevenLabs） | **成立** | 有料プラン（Starter 以上）の維持が必須。30 分の音声と voice captcha による本人確認。⚠ 日本語需要の裏付けは無い |
| 動画（Troveo） | **保留** | 対応国・支払方法・単価・最低要件が**すべて非公表**。契約は NDA 下のパートナーシップ契約で、条件が事前に見えない |
| 写真 | **不成立** | — |

## 試算

比較対象は B14（$31〜65/時）。1$ = 159.38 円。再計算: `experiments/d7-content-licensing/calc.py`

### 【声】ElevenLabs Voice Library — 成立する

前提: 月 $80〜320（稼いでいる creator の典型レンジ、第三者情報）。録音・登録は一度きり。

| 登録に要する時間 | 初年度収入 | 時間あたり | 判定 |
| ---: | ---: | ---: | :--: |
| 3 時間 | $960〜3,840 | $320〜1,280 | ○ |
| 10 時間 | $960〜3,840 | $96〜384 | ○ |
| 30 時間 | $960〜3,840 | $32〜128 | △（下限が B14 と拮抗） |

2 年目以降は**追加作業ゼロで継続**するため、時間あたりはさらに上がる。L2 の定義に合う。

### 【動画】Troveo — 数字は成立するが、在庫を失う

前提: 個人例 1,400 時間で $33,000/年 = **$23.6/時間・年**。作業を在庫 10 時間あたり 1 時間と仮定。

| 在庫 | 年収入 | 作業 | 時間あたり | 判定 |
| ---: | ---: | ---: | ---: | :--: |
| 10 時間 | $236（37,568 円） | 1.0h | $236 | ○ |
| 50 時間 | $1,179（187,841 円） | 5.0h | $236 | ○ |
| 100 時間 | $2,357（375,681 円） | 10.0h | $236 | ○ |
| 500 時間 | $11,786（1,878,407 円） | 50.0h | $236 | ○ |

収入も作業も在庫量に比例するため、**時間あたりは在庫量に依存しない**。少量でも比率は同じ。

**単価の乖離を検証した**: 第三者情報の $1〜4/分（= $60〜240/時間）と、個人例からの逆算 $23.6/時間・年 は
10 倍以上ずれる。これは**採用率**（提出したうち実際にライセンスされる割合）で説明がつく。

| 想定単価 | 説明に必要な採用率 | 妥当性 |
| --- | ---: | --- |
| $1/分 | 39.3% | 妥当な範囲 |
| $2/分 | 19.6% | 妥当な範囲 |
| $4/分 | 9.8% | 妥当な範囲 |

→ **在庫の 1〜4 割しか売れない**前提で見るべき。上表の $23.6/時間・年は採用率込みの実効値なので、そのまま使える。

### 【写真】Shutterstock / Wirestock — 不成立

| 枚数 | 累計収入 | 判定 |
| ---: | ---: | :--: |
| 1,000 枚 | $6.90（1,100 円） | × |
| 10,000 枚 | $69.00（10,997 円） | × |
| 100,000 枚 | $690.00（109,972 円） | △ |

⚠ Wirestock の最低支払額は PayPal $30 / Payoneer $50。**上表の多くは支払い閾値にすら届かない**。
時間あたり $31 に達するには 1 時間で 4,492 枚の登録が必要で、不可能。

## ⚠ 権利条件（一次情報で確認）

### ElevenLabs Voice Library Addendum — 撤回できるが、完全には戻らない

| 論点 | 規約の記述 |
| --- | --- |
| 撤回 | 「You may choose to remove a User Voice Model you shared through the Voice Library Service **at any time**」 |
| ただし | 「any Outputs generated using your User Voice Model **prior to the end of the Notice Period will continue to exist and remain available for use thereafter**」 |
| ただし | Notice Period 中は、削除前に追加した他ユーザーのアカウントで**引き続き利用可能** |
| ⚠ トレードオフ | **Notice Period を長く設定するほど報酬レートが上がる**。撤回しにくさと報酬が引き換えになっている |
| 報酬の決まり方 | 「rewards are calculated based on **factors that we determine**」= レートは**事業者裁量**。$0.03/1,000 文字はブログの公表値であって規約上の保証ではない |
| 独占性 | **記載なし** |
| ⚠ 学習利用 | **記載なし**。ElevenLabs 自身がモデル学習に使うかは規約から読み取れない |
| 悪用防止 | 「Live Moderation」は任意機能で、「**not guaranteed to, stop users**」と明記 |
| 国 | イリノイ州居住者を除外（生体情報保護法対応）。**日本は対応国リスト（56 か国）に明示あり**（2026-08-27 確認） |
| 税 | 「You are responsible for reporting and paying any applicable taxes」 |
| ⚠ 参加要件 | **有料プラン（Starter 以上）の維持が必須**。ほか 30 分の音声と voice captcha による本人確認 |
| 支払い | Stripe Connect。最低 $10、6〜8 日ごとに自動処理。通貨は USD（EEA・英国・スイスは現地通貨） |

→ ⚠ **ElevenLabs は「AI 学習許諾」ではなく「声モデルの貸出」**。D7 の定義とずれる（後述）。

### Troveo — 独占の意味が公開情報では確定できない（初版の記述を訂正）

⚠ **初版で「95% が独占契約 → 在庫を失う → 実質一度きりの売却」と書いたのは、行き過ぎた解釈だった。**
公式ページを当たったところ、逆の記述がある。

| 論点 | 内容 | 出所 |
| --- | --- | --- |
| 所有権 | 「**you retain full ownership**」「**you keep full ownership**」 | Troveo 公式（content-owners） |
| ライセンス回数 | 「retain full ownership rights, and an ability to **license their content an unlimited number of times**」 | Troveo 公式 |
| 収入の形 | オンボーディングは「パートナーシップ契約 → アップロード → 処理 1〜2 週間 → **AI ラボへライセンスされた際にロイヤリティ**」 | Troveo 公式 |
| 独占性 | 「7,000 ライセンサーのうち **95% が独占（exclusively）**」 | Morningstar / BusinessWire 経由のプレスリリース |
| 独占の定義 | 「an exclusive license means the rights holder **will not license the same data to others**」 | Troveo 公式（AI Data Licensing） |
| 付与する権利 | 「permission to **reproduce and process the content to train, fine-tune, and evaluate** machine learning models」 | Troveo 公式 |
| 撤回 | **明示なし** | — |
| 単価・対応国・最低要件 | **すべて非公表**。契約は NDA 下 | — |

**この 2 つは矛盾して見える。** 考えられる読みは 2 つある。

1. 「独占」は **Troveo を唯一の窓口にする**という意味（他の集約サービスに出さない）。所有権も他用途の利用も残る
2. 「独占」は **そのデータを他ラボにライセンスしない**という意味（Troveo 自身の定義がこちら）。この場合、
   Troveo 経由でも 1 社にしか売れないことになり、「unlimited number of times」と整合しない

**公開情報では確定できない。** 契約書は NDA 下のパートナーシップ契約で、条件は事前に見えない。

→ 訂正の要点: **在庫を失うとは言えない**（所有権保持は公式が明記）。一方で独占条件は不明のままなので、
リスクは「在庫の喪失」ではなく「**条件が事前に分からないまま契約する**」ことにある。

## ⚠ 未確認のまま残った論点

| 論点 | 状態 | なぜ重要か |
| --- | --- | --- |
| ~~日本からの受取可否（声）~~ | **解決（2026-08-27）**。ElevenLabs の対応 56 か国に Japan が明示 | — |
| 日本からの受取可否（動画） | **未確認**。Troveo は対応国・支払方法とも非公表 | 受け取れなければ全部が無意味 |
| 日本語音声の需要 | **不明** | 月 $80〜320 は英語圏の値。日本語は 32 言語の 1 つで、需要規模の裏付けがない |
| 日本語話者の実例 | **見つからず**（再調査でも発見できず） | ⚠ I7 で「実例ゼロ」が打ち切りの決め手になった。**同じ警戒が要る** |
| ElevenLabs 分配の中央値 | **分布不明** | 10,400 人は「稼いでいる creator」の数。登録したが稼げていない人は分母に入っていない可能性 |
| Troveo の独占条件 | **確定できない** | 公式の「所有権保持・無制限ライセンス」と「95% が独占」が整合しない。契約は NDA 下 |
| AILAS（国内の窓口） | 収益還元は「契約条件に従って」で**額は非公開**、実績も不明 | 国内経路の有無 |

### ⚠ 声で固定費が発生する（新たに判明）

ElevenLabs は**有料プラン（Starter 以上）の維持が受取の条件**。Starter は **$6/月（年払い $60）**。

| 項目 | 値 |
| --- | ---: |
| 年間の固定費 | $60（年払い時） |
| 分岐点（$0.03/1,000 文字 の既定レート時） | 年 **200 万文字** = 月 約 167,000 文字 |
| 音声に換算すると（1,000 文字 ≒ 90 秒） | 月 **約 4.2 時間**、自分の声が有料ユーザーに使われれば黒字 |

→ 月 $80〜320 稼ぐ層なら固定費は誤差。ただし**まったく使われなければ年 $60 の赤字**になる。
「在庫があれば追加労働ほぼゼロ」は正しいが、**コストはゼロではない**。

## 体系へのフィードバック: D7 は 2 つの形が混在している

調査の結果、D7 に分類した 2 つの経路は**関与度も収入の形も違う**ことが分かった。

```mermaid
flowchart TD
  D7["D7 既存コンテンツの<br/>AI 学習許諾"] --> A["継続分配型<br/>ElevenLabs"]
  D7 --> B["独占売却型<br/>Troveo 95%"]
  A --> A1["使われるたびに分配<br/>撤回できる<br/>在庫は手元に残る"]
  A1 --> A2["L2 として正しい"]
  B --> B1["一度きりの対価<br/>撤回の定めなし<br/>在庫を失う"]
  B1 --> B2["実態は在庫の譲渡<br/>= E8 / D6 に近い"]
  style A2 fill:#e8f0fe
  style B2 fill:#fdd
```

> **訂正（2026-08-27）**: 初版はここで「継続分配型と独占売却型が混在している」としたが、
> Troveo も公式には**所有権保持のロイヤリティ方式**であり、**どちらも継続分配型**だった。分割の根拠は弱い。

- **継続分配型**（ElevenLabs）は L2 の定義に合う。初回だけ関与し、以後は使われた分が入る
- **Troveo も同じくロイヤリティ方式**。ただし「95% が独占」の意味が確定できないため、
  実態が継続分配のままなのか、独占により 1 社への売り切りに近づくのかは**公開情報では判別できない**
- したがって **D7 の分割は保留**する。分けるべきは「継続 / 売却」ではなく、
  **条件が事前に見えるか（ElevenLabs = 規約公開）／見えないか（Troveo = NDA 下の個別契約）**かもしれない

**残る体系上の論点**: ElevenLabs は規約上「声モデルの貸出」であって、
ElevenLabs 自身がモデル学習に使うかは規約から読み取れない。
D7 を「AI 学習許諾」と定義したが、実際に売っているものは**学習データとは限らない**。
**D7 の定義（何を許諾しているのか）は依然として詰め直す必要がある。**

## 次のアクション

| # | 内容 | 状態 |
| --- | --- | --- |
| 1 | ElevenLabs の対応国確認 | **完了（2026-08-27）**。Japan は対応 56 か国に含まれる |
| 2 | 日本語音声・日本の映像の**実例**を探す | **完了・見つからず**。⚠ I7 と同じ形として警戒が要る |
| 3 | Troveo の対応国・支払方法 | **未完了**。公開情報に無いため、問い合わせないと確定しない（登録は不要） |
| 4 | 手持ちの**声・動画**の在庫量を数える | **未完了**（写真は除外してよい） |

**判断が要る点**: 声は日本から受け取れることが確定し、固定費も年 $60 と小さい。
一方で**日本語話者の実例が 1 件も見つかっていない**。これは I7 を打ち切った理由と同じ形であり、
月 $80〜320 という数字が日本語話者に当てはまる保証はどこにもない。

→ 実例が無いまま進めるかどうかは、**投じるものが小さい**（年 $60 + 録音数時間）ことをどう見るかによる。
I7（打ち切り）との違いは、**ここでは失敗しても失うものがほぼ無い**点にある。

## 米国居住前提での再判定（2026-08-28）

> 体系の前提に**税務上の居住地 = 米国（WA 州シアトル）**を追加したため、本ファイルを再判定した
> （[TODO.md 前提の見直し](../../../TODO.md)、プラン: `docs/plans/us-residency-premise.md`）。
> **上の 2026-08-27 時点の分析は日本居住前提で書かれている。以下がそれに優先する。**

### 結論: **声も動画も成立側に動いた。最大リスクだった「実例ゼロ」が消える**

**この再判定で最も大きいのは、打ち切りの根拠になりかけていた 2 つの未確認論点が、居住地を変えるだけで消えたこと。**

| # | 2026-08-27 の状態（日本居住） | 2026-08-28 の状態（米国・WA 州） |
| --- | --- | --- |
| 1 | ⚠ **日本語話者の実例がゼロ**。I7 を打ち切った理由と同じ形として警戒していた | **消滅**。英語話者が主要な母集団で、ElevenLabs は voice creator への支払い累計 $22M、Troveo は 7,000 ライセンサーへ累計 $20M【Web 確認済（二次）】 |
| 2 | ⚠ **Troveo の対応国・支払方法がすべて非公表**。「問い合わせないと確定しない」として保留の理由になっていた | **争点でなくなる**。Troveo は米国（ロサンゼルス）の事業者で、米国居住者の受取が問題になる余地がない |
| 3 | 日本語需要の裏付けが無い（月 $80〜320 は英語圏の値） | **英語圏の値がそのまま自分に当たる**前提になる |

**日本語話者向けに書かれていた警戒が、そのまま「英語話者なら該当しない」に変わる。**
判定条件 2（受け取れるか）と、未確認論点の主要 3 件が同時に解消した。

### 判定条件の再照合

| # | 条件 | 日本居住（2026-08-27） | 米国・WA 州（本節） |
| --- | --- | --- | --- |
| 1 | 時間あたりで B14 を上回るか | ○（B14 は $31〜65/時） | **○ だが基準が上がった**。⚠ 米国の B14 は職種次第で $65〜110/時（後述） |
| 2 | 受け取れるか | 声 ○ / 動画 ⚠ | **声 ○ / 動画 ○**。どちらも解消 |
| 3 | ⚠ 許諾の不可逆性・独占性 | 声 ○ 条件付き / 動画 確定できない | **変わらず**。⚠ Troveo の独占条件は依然 NDA 下 |
| 4 | 継続収入か | どちらも継続分配 | **変わらず** |

### ⚠ 比較対象（B14）の水準が上がったため、余裕は縮む

D7 の判定は「B14 を時間あたりで上回るか」で行っている。**その B14 の米国での水準を取り直した。**

| 職種 | 日本基準（2026-08-26） | 米国（2026-08）【Web 確認済（二次）】 |
| --- | --- | --- |
| 全体平均 | — | $31/時 |
| RLHF・選好アノテーション | $31〜65/時 | Outlier $30〜42／Mercor $40〜55／Surge $35〜50 |
| **コーディング評価（上位）** | — | **Outlier $65〜75／Mercor $85〜110／Surge $70〜95** |

⚠ **確定セグメントの既存スキルは「ソフトウェア開発」なので、当たるのはコーディング評価の帯**である。
比較の基準は $31〜65/時ではなく **$65〜110/時**に上がる。

これを当てて D7 を再照合する。

| 種類 | 登録に要する時間 | 時間あたり | 旧基準 $31〜65 | **新基準 $65〜110** |
| --- | ---: | ---: | :--: | :--: |
| 声 | 3 時間 | $320〜1,280 | ○ | **○** |
| 声 | 10 時間 | $96〜384 | ○ | **○**（下限 $96 が基準内） |
| 声 | 30 時間 | $32〜128 | △ | **× 下限は届かない** |
| 動画 | 在庫の 1/10 | $236 | ○ | **○** |

**声は「登録に 10 時間以内で済むか」が分岐になった。** 日本基準では 30 時間かけても辛勝だったが、
米国では 30 時間かけると下限が B14 を下回る。⚠ **在庫を数える作業（未着手）の重要度が上がっている。**

### ⚠ 米国・WA 州で新たに効く論点

**（1）WA 州は 2026-06-10 に声を含むパブリシティ権を拡張した**

| 項目 | 内容 |
| --- | --- |
| 法律 | ワシントン州 SB 5886（Washington Personality Rights Act の改正）。**2026-06-10 施行**【Web 確認済（二次）】 |
| 内容 | 氏名・**声**・署名・写真・肖像に加え、「**forged digital likeness**（偽造されたデジタル肖像）」の無断使用に対する請求権を新設 |
| 範囲 | ⚠ **著名性・住所・商業的価値を問わず、すべての個人に適用**される |

**これは D7 にとって両側に効く。**

- **有利な側**: 声が州法上の**財産権**として明示された。ライセンスするという行為の法的な足場が明確になる
- ⚠ **不利な側**: ライセンスは「許諾された使用」なので、**許諾した範囲については、この請求権を自ら手放すことになる**。
  ElevenLabs の Notice Period 経過後も残る出力（規約が明記）は、州法の保護対象から外れる部分になる

**（2）⚠ ElevenLabs に対する生体情報保護法（BIPA）の集団訴訟が係属している**

2026 年 5 月 11〜13 日、イリノイ州北部地区連邦地裁に **Adobe・Google・Amazon・Apple・ElevenLabs・Meta・Microsoft・NVIDIA・Samsung** を
被告とする BIPA 集団訴訟 9 件が提起された。公開音源から声紋を抽出して AI 音声モデルの学習に使ったとする主張【Web 確認済（二次）】。

⚠ **これは自分が原告になる話ではなく、取引相手の事業リスクとして見る論点**である。
D7 は「相手が払い続けること」に依存する手法なので、⚠ **相手の法務リスクは収入の継続性に直結する**。
2026-08-27 の分析では ElevenLabs 側のリスクを一切見ていなかった。

**（3）参加要件が上がっている可能性がある**

| 項目 | 2026-08-27 に記録した値 | 2026-08-28 に確認した値 |
| --- | --- | --- |
| 必要な音声の長さ | 30 分 | **「約 2 時間の高品質な録音」を推奨**【公表値】 |
| 税務手続き | 記載なし | **W-8 / W-9 の提出**が要件に明示【公表値】 |
| 有料プラン | Starter 以上が必須 | **変わらず** |

⚠ **米国居住者は W-9 を出す**ことになる。日本居住なら W-8BEN で租税条約による源泉税の軽減を申請する話だったが、
米国居住では源泉徴収ではなく**自分の確定申告で処理する**形になる。

**（4）課税**

| 項目 | 扱い |
| --- | --- |
| 州所得税 | **ゼロ**（WA 州） |
| 連邦所得税 | 通常所得。⚠ 2026 年の限界税率は所得階層による（[横断調査 §3-3](../online-tradable-assets.md#3-3-課税--ここで順位が決まる)） |
| ⚠ 自営業税の有無 | **未確認**。ロイヤリティとして Schedule E で申告するか、事業所得として Schedule C＋自営業税 15.3% がかかるかで手取りが変わる |

⚠ **自営業税がかかる場合、時間あたりの手取りは 15.3% 分下がる。** 上の $96〜1,280/時 はいずれも税引前である。
これは B14（同じく業務委託）との比較では**両方にかかるため順位は変わらない**が、絶対額としては効く。

### 残る論点（居住地では解消しなかったもの）

| 論点 | 状態 |
| --- | --- |
| ⚠ Troveo の独占条件 | **変わらず確定できない**。「所有権保持・無制限ライセンス」と「95% が独占」の矛盾は公開情報では解けない。契約は NDA 下 |
| ⚠ ElevenLabs のレート決定 | **変わらず事業者裁量**。「factors that we determine」 |
| ⚠ D7 の定義 | **変わらず未解決**。ElevenLabs が売っているのは「声モデルの貸出」で、学習データとは限らない |
| 手持ちの声・動画の在庫量 | **未着手**。上記のとおり重要度が上がった |
| ElevenLabs 分配の中央値 | **分布不明**。$22M は総額で、登録したが稼げていない人は見えない |

### 再判定の結論

| 種類 | 日本居住（2026-08-27） | 米国・WA 州（2026-08-28） |
| --- | --- | --- |
| 声（ElevenLabs） | **成立**（⚠ 日本語話者の実例ゼロ） | **成立。実例の懸念が消え、根拠が強くなった**。⚠ 登録 10 時間以内が条件。⚠ 相手の BIPA 訴訟リスクを新たに認識 |
| 動画（Troveo） | **保留**（対応国・支払方法が非公表） | **保留の理由が 1 つ減った**（受取は問題にならない）。⚠ ただし独占条件が NDA 下という主たる理由は残る |
| 写真 | **不成立** | **変わらず不成立** |

**D7 は依然として唯一の残存候補であり、米国居住前提ではその根拠がむしろ強くなった。**
⚠ ただし比較対象（B14）の水準が上がったため、**「在庫がどれだけあるか」を数えないと判定が閉じない**状態は変わっていない。

### 出典（米国基準の再判定分。すべて 2026-08-28 取得）

| # | 内容 | URL |
| --- | --- | --- |
| 1 | ElevenLabs の支払対応国（米国を含む）・参加要件（Starter 以上・約 2 時間の録音・Voice Captcha・W-8/W-9・最低 $10） | https://elevenlabs.io/docs/eleven-creative/voices/payouts |
| 2 | WA 州 SB 5886（パブリシティ権を forged digital likeness へ拡張、2026-06-10 施行） | https://www.dwt.com/insights/2026/06/washington-state-right-of-publicity-ai-replicas |
| 3 | 同上（適用範囲は著名性・住所・商業的価値を問わない） | https://www.cooley.com/news/insight/2026/2026-04-06-washington-state-expands-personality-rights-law-to-cover-ai-generated-deepfakes |
| 4 | ⚠ ElevenLabs を含む BIPA 集団訴訟 9 件（2026-05、N.D. Ill.） | https://www.loevy.com/class-actions/artificial-intelligence/bipa-ai-class-action/ |
| 5 | Troveo の単価 $1〜4/分・累計支払 $20M | https://mediaandthemachine.substack.com/p/this-ai-company-has-paid-out-20m |
| 6 | 米国の AI トレーナー時給（Mercor $25〜250+、コーディング評価 $85〜110、Outlier $65〜75） | https://www.aigigjobs.com/blog/mercor-complete-guide |
| 7 | 職種別の時給比較（Outlier / Mercor / Surge AI、2026） | https://hirefeed.co.in/blog/outlier-vs-mercor-vs-surge-2026 |

## 出典（すべて 2026-08-27 取得）

- [ElevenLabs — The ElevenLabs Voice Library Addendum（規約一次情報）](https://elevenlabs.io/vla)
- [ElevenLabs — What are Voice Actor Payouts?（docs）](https://elevenlabs.io/docs/help-center/product/monetization-business/payouts/what-are-voice-actor-payouts)
- [ElevenLabs — $22M earned by voice creators, doubling in 6 months](https://elevenlabs.io/blog/22-million-earned-by-voice-creators-on-elevenlabs)
- [ElevenLabs — How to monetize your voice with ElevenLabs Voice Library](https://elevenlabs.io/blog/monetize-your-voice-with-elevenlabs-voice-library-and-create-passive-income)
- [ElevenLabs — Voice Library（product guide）](https://elevenlabs.io/docs/product-guides/voices/voice-library)
- [ElevenLabs — How do I delete a voice I've shared with the Voice Library?](https://help.elevenlabs.io/hc/en-us/articles/27948860521745-How-do-I-delete-a-voice-I-ve-shared-with-the-Voice-Library)
- [Troveo — AI Data Licensing in 2026: How Deals Work and What Data Costs](https://www.troveo.ai/resources/ai-data-licensing)
- [Morningstar — Troveo Announces $20 Million in Payouts（7,000 ライセンサー、95% 独占）](https://www.morningstar.com/news/business-wire/20260428319383/troveo-accelerates-ai-model-development-expands-ai-training-data-platform-to-five-new-categories-announces-20-million-in-payouts)
- [The Seattle Times — YouTubers profit as AI companies search for unused video footage](https://www.seattletimes.com/business/youtubers-profit-as-ai-companies-search-for-unused-video-footage/)
- [ElevenLabs — Payouts（対応 56 か国のリスト。Japan を含む）](https://elevenlabs.io/docs/eleven-creative/voices/payouts)
- [ElevenLabs — What countries do you support for Voice Actor Payouts with Stripe Connect?](https://help.elevenlabs.io/hc/en-us/articles/22975638535185-What-countries-do-you-support-for-Voice-Actor-Payouts-with-Stripe-Connect)
- [ElevenLabs — Earn with Your Voice（30 分の音声・voice captcha）](https://elevenlabs.io/payouts)
- [Troveo — License Your Content to Top AI Companies（所有権保持の明記）](https://www.troveo.ai/content-owners)
- [Wirestock — Terms of Use](https://wirestock.io/docs/terms-of-use)
- [AILAS 公式サイト](https://ailas.or.jp/)
- [PC Watch — AI 学習用音声の管理/追跡するシステム構築に向けた団体「AILAS」設立](https://pc.watch.impress.co.jp/docs/news/1603695.html)
- 単価・分配の一部は [i7-dataset.md 1-B](i7-dataset.md) の 2026-08-26 時点の調査を再利用
