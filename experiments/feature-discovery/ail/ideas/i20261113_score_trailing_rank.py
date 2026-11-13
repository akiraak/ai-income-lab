"""登録簿 `20261113-score-trailing-rank`（参考あり・枠の内。記録 dev-loop.md）:
較正した確率の代わりに、**その銘柄の直近 252 営業日の自分の予測値の中での順位**を買い% にする検知器 TR1。

- 予測値は既存の own × 学習器（ctx の model ＝ Ridge）そのまま: 訓練分割の全部で学び、評価の行を当てる（素の own × Ridge と同じ 1 本）。
- 買い% ＝ 100 ×（その行の予測値の、同じ銘柄の直近 N ＝ 252 個〔その行を含む〕の予測値の中での順位 − 0.5）÷ 並びの長さ。
  ⚠ 並びに入るのは **その行と、それより前の日の予測値だけ**（先の日は見ない）。同じ値は平均の順位。
- 評価期間の頭の N 日ぶんは、**訓練分割の尻 N 日を学習から外して学んだ 1 本** で尻を当てた予測値で埋める（訓練内の当てはめ値は使わない）。
  尻 ＝ 訓練の最後の N 日（訓練が 2N 日に満たなければ後ろ半分）。尻に行の無い銘柄は、評価の行だけで並びが伸びる（最初の行は 50）。
- 較正はしない（順位がそのまま買い%）。出口% は 100 − 買い%（器の既定）。
- leak 対照: LEAK_ 列を学習器の入力に足す（予測値が先を知る ＝ 順位が先を知る）。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail.registry import register

N = 252                # ⚠ 事前固定（登録簿「1 年ぶんの 1 種類だけ」）
NAME = f"TR1 自分の直近{N}日の予測値の中の順位（own・学習）"


def tail_days(ts: np.ndarray, n: int = N) -> np.ndarray:
    """訓練の最後の n 日（2n 日に満たなければ後ろ半分）。"""
    days = np.sort(pd.unique(ts))
    return days[-n:] if len(days) >= 2 * n else days[len(days) - len(days) // 2:]


def trailing_rank(symbol, ts, pred, n: int = N) -> np.ndarray:
    """行ごとの 100 ×（直近 n 個の中の順位 − 0.5）÷ 並びの長さ（銘柄ごと・日付の順。入力の並びのまま返す）。"""
    df = pd.DataFrame({"s": np.asarray(symbol).astype(str), "t": pd.to_datetime(ts), "p": np.asarray(pred, dtype=float)})
    df["_i"] = np.arange(len(df))
    df = df.sort_values(["s", "t", "_i"], kind="stable")
    roll = df.groupby("s", sort=False)["p"].rolling(n, min_periods=1)
    r = roll.rank().to_numpy()
    c = roll.count().to_numpy()
    out = np.empty(len(df))
    out[df["_i"].to_numpy()] = 100.0 * (r - 0.5) / c
    return out


def tr1(tr, te, feats, ctx):
    cols = [c for c in feats if c.startswith(("own_", "LEAK_"))]
    sc = StandardScaler().fit(tr[cols])
    Xtr = pd.DataFrame(sc.transform(tr[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    ytr = tr["y"].to_numpy(dtype=float)
    model = ctx["model"]
    ts_tr = pd.to_datetime(tr["ts"]).to_numpy()
    tail = np.isin(ts_tr, tail_days(ts_tr))
    p_tail = np.asarray(model(Xtr[~tail].reset_index(drop=True), ytr[~tail], Xtr[tail].reset_index(drop=True), ctx), dtype=float)
    p_te = np.asarray(model(Xtr, ytr, Xte, ctx), dtype=float)
    sym = np.r_[tr["symbol"].to_numpy()[tail], te["symbol"].to_numpy()]
    ts = np.r_[ts_tr[tail], pd.to_datetime(te["ts"]).to_numpy()]
    buy = trailing_rank(sym, ts, np.r_[p_tail, p_te])[int(tail.sum()):]
    doc = {"columns": cols, "N": N, "較正": "なし（順位がそのまま買い%）", "尻の日数": int(len(tail_days(ts_tr))),
           "尻の行": int(tail.sum()), "頭の行": int((~tail).sum()),
           "買い%>50の割合": round(float((buy > 50).mean()), 4), "買い%>60の割合": round(float((buy > 60).mean()), 4),
           "買い%の幅": round(float(buy.max() - buy.min()), 3) if len(buy) else 0.0}
    return buy, doc


register("detector", NAME)(tr1)
