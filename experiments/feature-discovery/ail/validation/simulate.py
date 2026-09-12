"""閾値つき売買のシミュレータ（rules.md 13 章）。⚠ **純粋関数**（読み書きしない・乱数なし）。

⚠ **ポジションは 0 か 1 の 2 状態だけ**（13-4）。買い増しなし・空売りなし・期末は強制清算。
足 t の買い% で足 t の終値で執行し、その日のポジションが y_t（t → t+1 終値の対数リターン）を得る。
コストは **建てた日と手仕舞った日にだけ片道 `cost_bp / 2`**。毎日は引かない。

⚠ **θ は 50% 以上だけ**（13-3 の 2）。θ ≥ 50 なら買い（買い% > θ）と
売り（100 − 買い% > θ）は同時に立たない。θ < 50 は優先規則という自由度が増えるので受けない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def simulate(buy_pct, y, threshold: float, cost_bp: float = 5.0) -> dict:
    """1 銘柄 × 1 fold を状態機械で回す。

    buy_pct: 0〜100 の系列（時刻順）。y: その日のリターン（対数）。threshold: θ（%）。
    戻り値: net_bp（日次純利 bp）・gross_bp・pos（0/1）・trades（建てた回数）・
    hold_ratio（保有日率）・skip_days（見送り日数）・cost_bp_total（払ったコスト bp）。
    """
    if threshold < 50.0:
        raise ValueError(f"θ = {threshold} は受けない。⚠ **θ は 50% 以上だけ**（rules.md 13-3 の 2。"
                         "θ < 50 は買いと売りが同時に立ち、優先規則という自由度が増える）")
    b = np.asarray(buy_pct, dtype=float)
    yy = np.asarray(y, dtype=float)
    if b.shape != yy.shape:
        raise ValueError(f"買い% と y の長さが違う（{b.shape} と {yy.shape}）")
    n = len(b)
    half = cost_bp / 2.0
    pos = np.zeros(n, dtype=int)
    net = np.zeros(n)
    p, trades, cost_total = 0, 0, 0.0
    for t in range(n):
        traded = False
        if p == 0 and b[t] > threshold:            # 建てる（未保有のときだけ）
            p, traded, trades = 1, True, trades + 1
        elif p == 1 and (100.0 - b[t]) > threshold:  # 手仕舞う（保有中のときだけ）
            p, traded = 0, True
        # ⚠ 未保有で売り指標が立っても何もしない（買い専用。13-4 の 2）
        pos[t] = p
        net[t] = p * yy[t] * 1e4 - (half if traded else 0.0)
        if traded:
            cost_total += half
    if p == 1:                                     # ⚠ fold 末尾の強制清算（13-4 の 4）
        net[-1] -= half
        cost_total += half
    gross = pos * yy * 1e4
    return {"net_bp": net, "gross_bp": gross, "pos": pos, "trades": trades,
            "hold_ratio": float(pos.mean()) if n else 0.0,
            "skip_days": int((pos == 0).sum()), "cost_bp_total": float(cost_total)}


def shifted_gate(pos, y, threshold_free_cost: float, rng) -> dict:
    """⚠ **同じ保有日率の乱択ゲート**（rules.md 14-6 の b）。⚠ **基準線なので試行に数えない。**

    ⚠ **「正しい日を休んだのか、ただ休んだだけか」を分ける**のが目的なので、
    ⚠ **保有日数と売買回数は保ったまま、日付の対応だけを壊す** — ポジション系列を巡回シフトする。
    （日をでたらめに選び直すと売買回数が跳ね上がり、⚠ **コストの差で負けるだけ**になって分離できない。）

    ⚠ **巡回の継ぎ目で売買回数が ±1 ずれることがある**（端の 1 か所だけ）。保有日数はぴったり同じ。
    """
    p = np.asarray(pos, dtype=int)
    n = len(p)
    off = int(rng.integers(1, n)) if n > 2 else 0
    rolled = np.roll(p, off)
    # ⚠ **θ=50 に 0/100 を流すと、その 0/1 がそのままポジションになる**（同じ状態機械・同じコスト）
    return simulate(rolled * 100.0, y, 50.0, threshold_free_cost)


def portfolio_daily(sym_series: dict[str, pd.Series]) -> pd.Series:
    """銘柄別の日次系列（bp）を **等加重平均**で 1 本にする（rules.md 13-7）。

    ⚠ **等加重にまとめた時点で銘柄間の相関は織り込まれる**ので、
    標本数はこの系列の長さ（検証日数）で数える。行数 63 × 日数 を使わない。
    """
    return pd.DataFrame(sym_series).mean(axis=1).sort_index()
