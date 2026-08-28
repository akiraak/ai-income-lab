# D7 既存コンテンツの AI 学習許諾（すべて【推測】。前提は【公表値】に紐づく）
FX = 159.38
B14_LO, B14_HI = 31, 65   # 比較対象: AI 開発者向け受託 $31〜65/時【公表値】

print("■ 種類別: 投下時間あたりの収入は B14 受託（$31〜65/時）を上回るか\n")

# --- 声: ElevenLabs ---
print("【声】ElevenLabs Voice Library")
print("  前提: 月 $80〜320（第三者情報・稼いでいる人の典型レンジ）、録音と登録は一度きり")
for setup_h in (3, 10, 30):
    for mo_lo, mo_hi in [(80, 320)]:
        y_lo, y_hi = mo_lo * 12, mo_hi * 12
        print(f"    登録に {setup_h:>2} 時間 → 初年度 ${y_lo:,}〜${y_hi:,} / "
              f"時間あたり ${y_lo/setup_h:>7,.0f}〜${y_hi/setup_h:>7,.0f}  "
              f"{'○ B14 超' if y_lo/setup_h > B14_HI else '△'}")
print(f"    ※ 2 年目以降は追加作業ゼロで継続（使われた分だけ）→ 時間あたりはさらに上がる")

# --- 動画: Troveo ---
print("\n【動画】Troveo")
print("  前提: 個人例 1,400 時間で $33,000/年 = $23.6/時間・年【公表値（二次）】")
print("        アップロード等の作業を在庫 10 時間あたり 1 時間と仮定")
RATE_PER_H_YEAR = 33_000 / 1_400
for stock_h in (10, 50, 100, 500):
    work_h = stock_h / 10
    rev = RATE_PER_H_YEAR * stock_h
    print(f"    在庫 {stock_h:>3} 時間 → 年 ${rev:>8,.0f}（{rev*FX:>10,.0f}円） / "
          f"作業 {work_h:>5.1f}h → 時間あたり ${rev/work_h:>6,.0f}  "
          f"{'○ B14 超' if rev/work_h > B14_HI else '×'}")
print(f"  ⚠ 95% のライセンサーが独占契約。在庫は売り切りで手元に残らない")

# 第三者情報 $1〜4/分 との乖離を採用率で説明
print("\n  単価の乖離チェック: 第三者情報の $1〜4/分 と個人例 $23.6/時間・年 は 10 倍以上ずれる")
for rate_min in (1, 2, 4):
    per_h = rate_min * 60
    adopt = RATE_PER_H_YEAR / per_h
    print(f"    ${rate_min}/分（= ${per_h}/時間）で説明するには採用率 {adopt*100:>5.1f}% が必要"
          f" → {'妥当な範囲' if 0.05 <= adopt <= 0.5 else '説明できない'}")

# --- 写真 ---
print("\n【写真】Shutterstock Contributor Fund / Wirestock")
print("  前提: 中央値 $0.0069/枚【公表値】。バルクは 1 枚セント未満")
for n in (1_000, 10_000, 100_000):
    rev = 0.0069 * n
    print(f"    {n:>7,} 枚 → 累計 ${rev:>8,.2f}（{rev*FX:>9,.0f}円）  "
          f"{'× 論外' if rev < 100 else '△'}")
print("  ⚠ Wirestock の最低支払額は PayPal $30 / Payoneer $50。上表の多くは閾値にすら届かない")

print("\n■ 損益分岐（時間あたりで B14 下限 $31/h を超えるのに必要な在庫）")
print(f"  声  : 年 ${31*3:,}（= 月 ${31*3/12:.0f}）以上を稼げば、登録 3 時間で分岐点を超える")
print(f"  動画: 在庫量に依存しない（収入も作業も在庫に比例）。{RATE_PER_H_YEAR:.1f}/0.1 = ${RATE_PER_H_YEAR*10:,.0f}/h で常に超える")
print(f"  写真: ${31:,}/h に達するには 1 時間の作業で {int(31/0.0069):,} 枚の登録が必要 → 不可能")
