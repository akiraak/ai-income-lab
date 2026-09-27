"""損切りの検証の診断（[記録](../../../docs/specs/experiments/stoploss-as-model.md) §0-5 の物差し）。⚠ **読むだけ・何も書かない。**

    python3 -m cli.stopdiag --run 2026-09-27T15-53-55_trade_own_stop_ridge_a [--control "L0 入口のみ（損切りなし）"] [--theta 50]

出すもの（全部 fold 5 本の系列から。⚠ **採否には使わない** ＝ 採否は 13-7 の検証結果一覧の判定）:
  A. 手法 × θ: 純利・対 B&H 上乗せ（平均・符号・t）・取引/fold・保有日率・対 乱択ゲート（手法 − 乱択）・保有日数の中央値・強制清算の割合
  B. ⚠ **対 損切りなし の差**（fold ごとの 手法 − 対照。符号 5/5 で読む ＝ 記録 §0-5）
  C. 最大の含み損: ポートフォリオ日次純利の累積の最大ドローダウン（fold ごとの平均と最悪）
  D. 1 取引あたりの損益（`holds.csv` × 表の `y`。最悪・p05・負けの割合）
t は fold 5 本の平均 ÷ (標準偏差 ÷ √5)（`checks.json` と同じ）。
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from ail import runs

BH = "基準 常に上（ドリフト）"


def _t(v: np.ndarray) -> float:
    v = np.asarray(v, dtype=float)
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 1 and v.std(ddof=1) > 0 else float("nan")


def _pattern(v) -> str:
    return "".join("＋" if x > 0 else ("−" if x < 0 else "0") for x in v)


def _fold_series(res: pd.DataFrame, method: str, th: float, col: str = "純利bp") -> np.ndarray:
    d = res[(res["手法"] == method) & (res["閾値"] == th)].sort_values("fold")
    return d[col].to_numpy(dtype=float)


def table_a(res: pd.DataFrame, holds: pd.DataFrame, methods: list[str], thetas: list[float]) -> pd.DataFrame:
    rows = []
    for th in thetas:
        bh = _fold_series(res, BH, th)
        for m in methods:
            net = _fold_series(res, m, th)
            if len(net) != len(bh):
                continue
            edge = net - bh
            rand = net - _fold_series(res, m, th, "乱択ゲート純利bp")
            h = holds[(holds["手法"] == m) & (holds["閾値"] == th)]
            d = res[(res["手法"] == m) & (res["閾値"] == th)]
            rows.append({"手法": m, "θ": int(th), "純利": round(net.mean()), "上乗せ": round(edge.mean()),
                         "符号": _pattern(edge), "t": round(_t(edge), 2),
                         "取引/fold": round(d["取引回数"].mean()), "保有日率": round(d["保有日率"].mean(), 2),
                         "対乱択": round(rand.mean()), "中央値": float(h["保有日数"].median()) if len(h) else np.nan,
                         "強制": round(float(h["強制清算"].mean()), 2) if len(h) else np.nan})
    return pd.DataFrame(rows)


def table_b(res: pd.DataFrame, control: str, methods: list[str], thetas: list[float]) -> pd.DataFrame:
    rows = []
    for th in thetas:
        base = _fold_series(res, control, th)
        for m in methods:
            if m == control:
                continue
            net = _fold_series(res, m, th)
            if len(net) != len(base):
                continue
            diff = net - base
            rows.append({"手法": m, "θ": int(th), "対 損切りなし": round(diff.mean()), "符号": _pattern(diff),
                         "t": round(_t(diff), 2), "fold 値": " ".join(f"{x:+.0f}" for x in diff)})
    return pd.DataFrame(rows)


def _fold_lengths(res: pd.DataFrame, method: str, th: float) -> list[int]:
    d = res[(res["手法"] == method) & (res["閾値"] == th)].sort_values("fold")
    return d["検証日数"].astype(int).tolist()


def table_c(res: pd.DataFrame, daily: pd.DataFrame, methods: list[str], thetas: list[float]) -> pd.DataFrame:
    rows = []
    for th in thetas:
        for m in methods:
            s = daily[(daily["手法"] == m) & (daily["閾値"] == th) & (daily["系列"] == "純利bp")].sort_values("ts")["値"].to_numpy(float)
            lens = _fold_lengths(res, m, th)
            if not len(s) or sum(lens) != len(s):
                continue
            dds, pos = [], 0
            for n in lens:
                c = np.cumsum(s[pos:pos + n])
                dds.append(float((c - np.maximum.accumulate(c)).min()))
                pos += n
            rows.append({"手法": m, "θ": int(th), "最大DD 平均": round(float(np.mean(dds))), "最大DD 最悪": round(min(dds)),
                         "fold 値": " ".join(f"{x:.0f}" for x in dds)})
    return pd.DataFrame(rows)


def table_d(holds: pd.DataFrame, table: pd.DataFrame, methods: list[str], thetas: list[float], cost_bp: float) -> pd.DataFrame:
    """1 取引の損益 bp ＝ 建てた日から保有日数ぶんの `y`（対数）の合計 × 1e4 − 往復の費用。⚠ 強制清算の取引も含む。"""
    ys = {s: g.set_index("date")["y"].sort_index() for s, g in table.assign(date=pd.to_datetime(table["ts"]).dt.date).groupby("symbol")}
    rows = []
    for th in thetas:
        for m in methods:
            h = holds[(holds["手法"] == m) & (holds["閾値"] == th)]
            if not len(h):
                continue
            pnl = []
            for sym, day, k in zip(h["銘柄"], pd.to_datetime(h["建てた日"]).dt.date, h["保有日数"].astype(int)):
                y = ys.get(sym)
                if y is None:
                    continue
                i = y.index.searchsorted(day)
                pnl.append(float(y.iloc[i:i + k].sum()) * 1e4 - cost_bp)
            p = np.asarray(pnl)
            rows.append({"手法": m, "θ": int(th), "取引": len(p), "最悪": round(p.min()), "p05": round(np.percentile(p, 5)),
                         "中央値": round(float(np.median(p)), 1), "負けの割合": round(float((p < 0).mean()), 2),
                         "1 取引の平均": round(float(p.mean()), 1)})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--control", default="L0 入口のみ（損切りなし）")
    ap.add_argument("--theta", type=float, default=None, help="1 つの θ だけ出す（既定は全部）")
    ap.add_argument("--table", default=None, help="表の名前（既定は実行の config の名前）")
    a = ap.parse_args()
    res = runs.read_csv(a.run, "result.csv")
    holds = runs.read_csv(a.run, "holds.csv")
    daily = runs.read_csv(a.run, "daily.csv")
    thetas = sorted(res["閾値"].unique()) if a.theta is None else [float(a.theta)]
    methods = [m for m in res["手法"].unique() if m != BH and m != "基準 直前リターンの符号"]
    cfg = runs.read_json(a.run, "config.json") if runs.exists(a.run, "config.json") else {}
    cost = float(cfg.get("cost_bp", 5.0))
    name = a.table or cfg.get("name") or a.run.split("_", 1)[1]
    path = os.path.join(runs.ROOT, "data", "features", name, "d.parquet")
    table = pd.read_parquet(path, columns=["ts", "symbol", "y"]) if os.path.exists(path) else None
    pd.set_option("display.width", 250)
    print("## A. 手法 × θ（対 B&H・対 乱択・保有）")
    print(table_a(res, holds, methods, thetas).to_string(index=False))
    print(f"\n## B. 対 損切りなし の差（対照 ＝ {a.control}）")
    print(table_b(res, a.control, methods, thetas).to_string(index=False))
    print("\n## C. 最大ドローダウン（ポートフォリオ日次純利の累積・fold ごと・bp）")
    print(table_c(res, daily, methods, thetas).to_string(index=False))
    if table is not None:
        print(f"\n## D. 1 取引あたりの損益 bp（表 {name}・往復 {cost:g}bp 込み）")
        print(table_d(holds, table, methods, thetas, cost).to_string(index=False))
    else:
        print(f"\n## D. 表 {path} が無いので飛ばした")


if __name__ == "__main__":
    main()
