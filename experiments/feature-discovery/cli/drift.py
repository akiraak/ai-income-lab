"""⚠ **上乗せを「当てた分」と「基準線が落ちた分」に分ける**（rules.md 14-6 b の銘柄別版）。

    python3 -m cli.drift --run runs/2026-09-12T17-35-16_trend_scales_1995_us74

⚠ **保存済みの `per_symbol.csv` を読むだけ。新しい実行も新しい試行も作らない。**

対 B&H 上乗せは ⚠ **休んだ日の −y の合計**なので、⚠ **銘柄が下がり続けていれば、
でたらめに休んでも正になる。** その分を引かないと「当てた」と読み違える。

⚠ **同じ日数をでたらめに休んだときの上乗せの期待値**:

    E[上乗せ] = −(1 − 保有日率) × B&H の粗利

⚠ **粗利を使うのは、コストが保有日率ではなく売買回数で決まるから**（13-4 規約 3）。
⚠ **保有日率 1.0 なら 0**（休まないので落とす損益が無い）。

⚠ **これは 14-6 b の乱択ゲートと同じ問いに、銘柄ごとに答えるものである。**
⚠ **乱択ゲートは 1 本の系列を種を固定して回した実測、こちらは期待値の式。** 数字は一致しない。
"""

from __future__ import annotations

import argparse

import pandas as pd

DRIFT = "基準 常に上（ドリフト）"


def random_gate_expectation(hold_rate, bh_gross_bp):
    """⚠ **同じ日数をでたらめに休んだときの上乗せの期待値。** 保有日率 1.0 で 0。"""
    return -(1.0 - hold_rate) * bh_gross_bp


def decompose(per_symbol: pd.DataFrame, method: str, threshold: float) -> pd.DataFrame:
    """銘柄 × fold の上乗せを、⚠ **落ちた分（でたらめ）と 当てた分（乱択超え）**に分ける。"""
    m = per_symbol[(per_symbol["手法"] == method) & (per_symbol["閾値"] == threshold)]
    b = per_symbol[(per_symbol["手法"] == DRIFT) & (per_symbol["閾値"] == threshold)]
    if m.empty or b.empty:
        raise SystemExit(f"⚠ 手法 {method!r} θ={threshold} か基準線の行が per_symbol.csv に無い")
    j = m.merge(b, on=["fold", "銘柄"], suffixes=("_m", "_b"))
    j["上乗せ"] = j["純利bp_m"] - j["純利bp_b"]
    j["落ちた分"] = random_gate_expectation(j["保有日率_m"], j["粗利bp_b"])
    j["当てた分"] = j["上乗せ"] - j["落ちた分"]
    return j


def main() -> int:
    ap = argparse.ArgumentParser(description="上乗せをドリフト分と当てた分に分ける")
    ap.add_argument("--run", required=True)
    ap.add_argument("--method", help="既定は summary の最良手法")
    ap.add_argument("--threshold", type=float, default=55.0)
    ap.add_argument("--group", action="append", default=[],
                    help="銘柄の群（`名前=SYM,SYM,...`）。⚠ 何度でも指定できる")
    a = ap.parse_args()

    ps = pd.read_csv(f"{a.run}/per_symbol.csv")
    method = a.method
    if method is None:
        s = pd.read_csv(f"{a.run}/summary.csv", index_col=0)
        cand = [str(x) for x in s.index if not str(x).startswith("基準 ") and str(x) != "乱択（基準）"]
        method = cand[0] if cand else None
    j = decompose(ps, method, a.threshold)

    groups = {}
    for g in a.group:
        name, _, syms = g.partition("=")
        groups[name] = [x.strip() for x in syms.split(",") if x.strip()]
    named = {s for v in groups.values() for s in v}
    j["群"] = j["銘柄"].map(lambda s: next((k for k, v in groups.items() if s in v), "その他"))

    print(f"実行 {a.run}")
    print(f"手法 {method} θ={a.threshold:g}\n")
    cols = ["上乗せ", "落ちた分", "当てた分"]
    if named:
        print(j.groupby("群")[cols].sum().round(0).to_string())
        print()
    per = j.groupby("銘柄")[cols].sum().sort_values("当てた分")
    show = per.loc[[s for s in per.index if s in named]] if named else per.tail(15)
    print("⚠ 銘柄別（5 fold 合計 bp・当てた分の小さい順）")
    print(show.round(0).to_string())
    print()
    tot = j[cols].sum()
    print(f"全体: 上乗せ {tot['上乗せ']:+.0f} ＝ 落ちた分 {tot['落ちた分']:+.0f} ＋ 当てた分 {tot['当てた分']:+.0f}")
    print("⚠ **落ちた分は「でたらめに同じ日数休んだときの期待値」であって、実測の乱択ゲートではない**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
