# TODO

- online-tradable-assets-slides.html にある商品の過去の取引データが取得可能なサービスがあるか調べる。データの粒度（１分間隔、１日間隔など）も調べる [plan](docs/plans/market-data-availability.md)
  - [ ] Phase 1: 96 行を機械抽出し、識別子と束 S1〜S7 を割り当てる
  - [ ] Phase 2: 束ごとに一次情報でサービス・粒度・費用・規約を当たる
  - [ ] Phase 3: L4・L5 の穴を特定し、上場代替などの代替経路を書く
  - [ ] Phase 4: docs/specs/market-data-availability.md にまとめ、96 行との 1:1 を検算する
- APIで売買可能な商品を調べる
- APIなしでもブラウザ操作の自動操作で売買できるか調べる
- online-tradable-assets-slides.html の「11 分類の全体像」スライドの主張「96 件のうち 68 件は同じ証券口座の板で完結し、残り 28 件は…」が行単位の実数と合わない（出口が板 = 56 件、入口が証券口座 = 49 件）。数え方を確定して直す
