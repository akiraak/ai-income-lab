"""登録簿 `20261101-crash-label-exit`（参考なし・枠の外。rules.md 31-6・記録 dev-loop.md §7）:
当てるものを翌日の方向から **「先 5 営業日のうちに、その銘柄の訓練期間の日次 σ の 2 倍を超えて下がる日があるか」** に替え、
その確率を **出口%** にする検知器（買いは常に 100 ＝ X1 と同じ「出口だけ」の形）。

⚠ ラベルは訓練分割の行だけで作る: 行 t の y（t → t+1）〜 t+4 の y を同じ銘柄の足の並びで取り、どれかが −2σ を割れば 1。
   σ は fold の訓練分割の、その銘柄の y の標準偏差。⚠ 先の 4 本が訓練分割に無い ／ 暦で 10 日を超えて離れる行は訓練から落とす
   （評価期間の y を使わない・休場の穴をまたがない）。パージは config の horizon_min（5 営業日を覆う）。
⚠ 説明変数は own_ 35 列（＋ leak 対照の LEAK_ 列）・学習器は ctx の model（Ridge）・較正は訓練分割の尻（5 日ぶんパージ）＝ scale._fit_calibration。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail.detectors.scale import _fit_calibration
from ail.registry import register

NAME = "X5 出口だけ 先5日の急落を当てる（own・学習）"
AHEAD = 5          # 先 5 営業日（y_t 〜 y_{t+4}）
K_SIGMA = 2.0      # 訓練期間の日次 σ の 2 倍
MAX_SPAN_DAYS = 10 # 先の 4 本が暦でこれより離れたら使わない（休場の穴・訓練分割の切れ目）


def crash_label(tr: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, dict]:
    """(ラベル 0/1, 使える行の印, 記録)。並びは tr と同じ。"""
    df = tr[["symbol", "ts", "y"]].copy()
    df["_i"] = np.arange(len(df))
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.sort_values(["symbol", "ts"])
    g = df.groupby("symbol", sort=False)
    sigma = g["y"].transform("std")
    hit = pd.Series(False, index=df.index)
    ok = pd.Series(True, index=df.index)
    for k in range(AHEAD):
        yk = g["y"].shift(-k)
        ok &= yk.notna()
        hit |= yk < -K_SIGMA * sigma
    span = (g["ts"].shift(-(AHEAD - 1)) - df["ts"]).dt.days
    ok &= span.notna() & (span <= MAX_SPAN_DAYS) & sigma.notna()
    lab = np.zeros(len(df), dtype=float)
    use = np.zeros(len(df), dtype=bool)
    lab[df["_i"].to_numpy()] = hit.to_numpy(dtype=float)
    use[df["_i"].to_numpy()] = ok.to_numpy()
    doc = {"ラベル": f"先{AHEAD}営業日に −{K_SIGMA:g}σ を割る日がある", "使える行": int(use.sum()),
           "急落の割合": round(float(lab[use].mean()), 4) if use.any() else None}
    return lab, use, doc


@register("detector", NAME)
def x5_crash_exit(tr, te, feats, ctx):
    lab, use, ldoc = crash_label(tr)
    cols = [c for c in feats if c.startswith("own_") or c.startswith("LEAK_")]
    tr_ok = tr.loc[use]
    sc = StandardScaler().fit(tr_ok[cols])
    Xtr = pd.DataFrame(sc.transform(tr_ok[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    ytr = np.where(lab[use] > 0, 1.0, -1.0)          # ⚠ 較正は「正の確率」を出す ＝ 急落の確率
    model = ctx["model"]
    cal = _fit_calibration(model, Xtr, ytr, tr_ok["ts"], AHEAD, ctx)
    pred = model(Xtr, ytr, Xte, ctx)
    exit_pct = np.asarray(cal.buy_pct(pred), dtype=float)
    doc = {"columns": cols, "入口": "常に 100（出口だけ）", "出口": {**ldoc, **cal.doc}, "訓練の行": int(use.sum())}
    return np.full(len(te), 100.0), exit_pct, doc
