"""レジストリ `20261128-triple-barrier-gate20`（参考あり・枠の内。rules.md 31 章）:
先 20 営業日の終値のパスが **上の線と下の線のどちらに先に当たるか** をラベルにする検知器（トリプルバリアのラベル）。

- 線の幅 ＝ A × 直近 20 日の日次 σ × √20（上下対称・対数リターンで測る）。A ＝ 1.0 に事前固定（結果を見て振らない）。
- 上に先に当たる確率を較正して **買い%**、下に先に当たる確率を較正して **出口%**（3 つ返す検知器 ＝ 買い% ／ 出口% ／ 記録）。
- どちらにも当たらない行は両方のラベルが 0（負例）。

⚠ ラベルは訓練分割の行だけで作る: 行 t の終値からのパス ＝ 同じ銘柄の y_t（t → t+1）〜 y_{t+19} の累積和。
   σ ＝ 訓練分割の中の、その銘柄の y_{t-20}〜y_{t-1} の標準偏差（y_t は先のリターンなので入れない）。
   ⚠ 訓練の末尾 20 行（銘柄ごと。線の判定が訓練の外の終値に掛かる行）・σ が取れない頭の 20 行・
   先の 20 本が暦で MAX_SPAN_DAYS を超えて離れる行は訓練から落とす（評価期間の y を使わない）。
⚠ 説明変数は対の相手（H3 先20日ゲート・`fwd_columns = "own"`）と同じ絞り ＝ `y_fwd_*` と `own_trend*` を外した列（own 35 列 ＋ leak 対照の `LEAK_` 列）。
⚠ 学習器は ctx の model（Ridge）・較正は訓練分割の末尾（20 日ぶんパージ）＝ `scale._fit_calibration`（H1〜H4 ／ X5 と同じ）。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない（既存の H1〜H5 ／ X5 ／ X6 は触らない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail.detectors.scale import _fit_calibration
from ail.features import labels, trend
from ail.registry import register

NAME = "B1 先20日の上下の線に先に当たる側（own・学習）"
AHEAD = 20           # 先 20 営業日（y_t 〜 y_{t+19}）。⚠ 1 種類だけ（事前固定）
SIGMA_WINDOW = 20    # 線の幅に使う日次 σ の観測期間（y_{t-20} 〜 y_{t-1}）
A = 1.0              # 線の幅の倍率。⚠ 1.0 に事前固定（結果を見て振らない）
MAX_SPAN_DAYS = 40   # 行 t と行 t+20 が暦でこれより離れたら使わない（休場の穴・訓練分割の切れ目）


def barrier_label(tr: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, dict]:
    """(side, 使える行のフラグ, 記録)。side ＝ +1 上の線に先 ／ −1 下の線に先 ／ 0 どちらにも当たらない。並びは tr と同じ。"""
    df = tr[["symbol", "ts", "y"]].copy()
    df["_i"] = np.arange(len(df))
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.sort_values(["symbol", "ts"])
    g = df.groupby("symbol", sort=False)
    sigma = g["y"].transform(lambda s: s.shift(1).rolling(SIGMA_WINDOW).std())
    width = A * sigma * np.sqrt(AHEAD)
    ok = sigma.notna() & (sigma > 0)
    cum = pd.Series(0.0, index=df.index)
    side = pd.Series(0.0, index=df.index)
    for k in range(AHEAD):
        yk = g["y"].shift(-k)
        ok &= yk.notna()
        cum = cum + yk                                   # 欠けた先は NaN のまま ＝ 比較は False
        still = side == 0                                # まだどちらにも当たっていない行だけ
        up = still & (cum >= width)
        dn = still & (cum <= -width)
        side = side.mask(up, 1.0).mask(dn, -1.0)
    # ⚠ 訓練の末尾 20 行（銘柄ごと）は捨てる ＝ 行 t+20（最後の y の終値の日）が訓練分割にある行だけ
    span = (g["ts"].shift(-AHEAD) - df["ts"]).dt.days
    ok &= span.notna() & (span <= MAX_SPAN_DAYS)
    out = np.zeros(len(df), dtype=float)
    use = np.zeros(len(df), dtype=bool)
    idx = df["_i"].to_numpy()
    out[idx] = side.to_numpy(dtype=float)
    use[idx] = ok.to_numpy(dtype=bool)
    out[~use] = 0.0
    n = int(use.sum())
    doc = {"ラベル": f"先{AHEAD}営業日のパスが ±{A:g}×σ{SIGMA_WINDOW}×√{AHEAD} のどちらに先に当たるか",
           "使える行": n,
           "上に先の割合": round(float((out[use] > 0).mean()), 4) if n else None,
           "下に先の割合": round(float((out[use] < 0).mean()), 4) if n else None,
           "どちらにも当たらない割合": round(float((out[use] == 0).mean()), 4) if n else None}
    return out, use, doc


def columns_of(feats) -> list[str]:
    """対の相手（`fwd.forward_gate` の `fwd_columns = "own"`）と同じ列。⚠ `LEAK_` の列はそのまま残る（13-10）。"""
    return [c for c in feats if not c.startswith(labels.SCALE_PREFIX) and not c.startswith(f"{trend.PREFIX}trend")]


@register("detector", NAME)
def b1_barrier(tr, te, feats, ctx):
    """⚠ **3 つ返す**（買い%, 出口%, 記録）＝ rules.md 16-1 の契約（X5 と同じ形・保有日数は返さない）。"""
    side, use, ldoc = barrier_label(tr)
    cols = columns_of(feats)
    tr_ok = tr.loc[use]
    sc = StandardScaler().fit(tr_ok[cols])               # ⚠ 標準化も訓練の内側で fit（rules.md 3 章 B）
    Xtr = pd.DataFrame(sc.transform(tr_ok[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    model = ctx["model"]
    s = side[use]
    pcts, docs = [], []
    for sign in (1.0, -1.0):                             # 上に先 ＝ 買い% ／ 下に先 ＝ 出口%
        ytr = np.where(s == sign, 1.0, -1.0)             # ⚠ 較正は「正の確率」を出す ＝ その側に先に当たる確率
        cal = _fit_calibration(model, Xtr, ytr, tr_ok["ts"], AHEAD, ctx)   # ⚠ 20 営業日ぶんパージした holdout で較正
        pred = model(Xtr, ytr, Xte, ctx)
        pcts.append(np.asarray(cal.buy_pct(pred), dtype=float))
        docs.append(dict(cal.doc))
    doc = {"scale": AHEAD, "a": A, "sigma_window": SIGMA_WINDOW, "columns": cols, "訓練の行": int(use.sum()),
           **ldoc, "入口": docs[0], "出口": docs[1]}
    return pcts[0], pcts[1], doc
