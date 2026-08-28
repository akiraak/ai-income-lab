# I8 GPU貸出 回収期間の試算（すべて【推測】。前提は【公表値】に紐づく）
FX = 159.38          # USD/JPY 2026-08-27【公表値】
H = 730              # 月間時間

# (機材名, 初期投資[円], 借り手表示価格[$/h], 稼働時システム消費[kW], アイドル[kW])
RIGS = [
    ("RTX 4090 中古 1台",  400_000, 0.39, 0.60, 0.08),
    ("RTX 5090 新品 1台",  900_000, 0.55, 0.75, 0.09),
    ("RTX PRO 6000 1台", 2_400_000, 1.80, 0.80, 0.10),
    ("RTX 5090 x4 ラック", 3_400_000, 0.55*4, 3.00, 0.36),
]
# 電気単価【公表値】: 従量電灯B第3段階 40.49円 / 高圧全国平均 18.92円
POWER = [("自宅・低圧", 40.49, 0), ("高圧・ハウジング", 18.92, 50_000)]
HOST_SHARE = 0.8     # 表示価格の約80%がホスト受取（表示は受取の約25%上）【公表値】
UTIL = [0.30, 0.40, 0.55]

print(f"{'構成':<20}{'設置':<18}{'稼働率':>6}{'月粗利':>12}{'回収':>12}")
print("-" * 70)
best = []
for name, capex, price_h, kw_on, kw_idle in RIGS:
    for site, yen_kwh, fixed in POWER:
        for u in UTIL:
            rev = price_h * HOST_SHARE * FX * H * u
            kwh = kw_on * H * u + kw_idle * H * (1 - u)
            cost = kwh * yen_kwh + fixed
            gp = rev - cost
            mo = capex / gp if gp > 0 else float("inf")
            best.append((mo, name, site, u, gp))
            r = f"{mo:.1f}ヶ月" if gp > 0 else "回収不能"
            print(f"{name:<20}{site:<18}{u*100:>5.0f}%{gp:>11,.0f}円{r:>12}")
    print()

print("=" * 70)
m, n, s, u, gp = min(best)
print(f"最良ケース: {n} / {s} / 稼働率{u*100:.0f}% → 月{gp:,.0f}円・回収{m:.1f}ヶ月")
print()
print("【上限テスト】稼働率100%・電気代ゼロでも回収12ヶ月に入るか:")
for name, capex, price_h, kw_on, kw_idle in RIGS:
    rev_max = price_h * HOST_SHARE * FX * H
    need = capex / 12
    ok = "○" if rev_max >= need else "×"
    print(f"  {ok} {name:<20} 月収入上限 {rev_max:>10,.0f}円 / 12ヶ月回収に必要 {need:>10,.0f}円")
