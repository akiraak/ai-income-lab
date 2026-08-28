# C4/C5 機会費用の回収試算（すべて【推測】。前提は【公表値】に紐づく）
FX = 159.38   # USD/JPY 2026-08-27【公表値】

# 機会費用: フリーランスエンジニア時給 4,000〜6,000円【公表値】、6ヶ月=26週
HOURLY = 5_000
WEEKS = 26
HOURS = [5, 10, 15]

# RevenueCat State of Subscription Apps 2026（115,000+ アプリ）【公表値】
# 発売1年後の月間収益
TIERS = [("中央値", 72), ("上位25%", 429), ("上位10%", 2_574)]

print("■ 投下時間の機会費用を、アプリ収益で回収するのに要する月数")
print(f"  （時給 {HOURLY:,}円 × 26週。判定条件は 6〜12ヶ月）\n")
print(f"{'週あたり':<10}{'機会費用':>12}", end="")
for label, _ in TIERS:
    print(f"{label:>14}", end="")
print()
print("-" * 66)
for h in HOURS:
    opp = h * WEEKS * HOURLY
    print(f"週 {h:>2} 時間  {opp:>11,}円", end="")
    for _, usd in TIERS:
        mo = opp / (usd * FX)
        mark = "○" if 6 <= mo <= 12 else ("!" if mo < 6 else "×")
        print(f"{mo:>11.1f}ヶ月{mark}", end="")
    print()

print("\n■ 資金 大の優位が使えるか（有料獲得の単位経済）")
CPI = [1.5, 5.0]                  # iOS CPI $1.5〜5【公表値】
CONV = [0.02, 0.05]               # freemium 課金転換 2〜5%【公表値】
RLTV = [23, 32]                   # Y1 RLTV/payer 世界$23・北米$32【公表値】
print(f"  課金1人あたり獲得単価 CAC = CPI / 転換率")
for cpi in CPI:
    for cv in CONV:
        cac = cpi / cv
        print(f"    CPI ${cpi:.2f} × 転換 {cv*100:.0f}% → CAC ${cac:>6,.0f}  "
              f"vs Y1 RLTV ${RLTV[0]}〜${RLTV[1]}  "
              f"→ {'回収不能（広告を出すほど赤字）' if cac > RLTV[1] else '回収可'}")

print("\n■ 同じ時間を B14（AI 開発者向け受託）に充てた場合との比較")
B14 = [31, 65]  # $31〜65/時【公表値】i7-dataset.md 2026-08-26
for h in HOURS:
    lo = h * 4.33 * B14[0] * FX
    hi = h * 4.33 * B14[1] * FX
    med_app = TIERS[0][1] * FX
    print(f"  週 {h:>2} 時間 → B14: 月 {lo:>9,.0f}〜{hi:>9,.0f}円 / "
          f"C4C5 中央値: 月 {med_app:>8,.0f}円  （{lo/med_app:>5.1f}〜{hi/med_app:.1f} 倍）")
