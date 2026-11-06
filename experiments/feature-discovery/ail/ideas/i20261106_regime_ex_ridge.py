"""登録簿 `20261106-regime-ex-cluster-ridge`（参考あり・枠の内。記録 dev-loop.md）:
R1（`i20261104_regime_ridge`）の局面を決める軸を **外生の列（米国債金利・ECB 為替の差分と z スコア）** に替えた検知器 R2。

- 局面の入力（日ごとに 28 列）: 表の `ex_UST*` ／ `ex_EURTO*`（`_d1`・`_z20`）。外部系列は全銘柄で同じ値なので日ごとに 1 つ取る。
  ⚠ 気象（`ex_WX_*`）・地震（`ex_EQ_*`）は使わない（登録簿の question ＝ 金利・為替）。
- 訓練分割の日だけで標準化と k-means（K ＝ 2 ／ 4・種 0・事前固定 ＝ R1 と同じ）を当て、評価の日は近い重心に振り分ける。
- 局面ごとの学習器は R1 と同じ（own 35 列 ＋ leak 対照の LEAK_ 列 × ctx の model。ex は Ridge の入力に入れない・
  訓練の中で 250 日に満たない局面は全体の 1 本で埋める・較正は全体で 1 回）。⚠ R1 との違いは局面の列だけ。
- 記録（登録簿の failure_modes の確かめ）: 局面の滞在日数の中央値（訓練 ／ 評価）・評価期間の局面の割合。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from ail.ideas import i20261104_regime_ridge as r1
from ail.models import calibrate
from ail.registry import register

KS = (2, 4)            # ⚠ 事前固定（登録簿 ＝ R1 と同じ）
SEED = 0
PREFIXES = ("ex_UST", "ex_EURTO")


def name_of(k: int) -> str:
    return f"R2 局面別 Ridge（外生で k-means・{k}個）"


def regime_columns(cols) -> list[str]:
    return [c for c in cols if c.startswith(PREFIXES)]


def day_frame(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """日ごとに 1 行（外部系列は全銘柄で同じ ＝ 日ごとの平均は その値）。"""
    return df[cols].groupby(pd.to_datetime(df["ts"]).to_numpy()).mean().sort_index()


def run_lengths(labels: np.ndarray) -> list[int]:
    """続けて同じ局面にいた日数の並び。"""
    if len(labels) == 0:
        return []
    cut = np.flatnonzero(np.diff(labels) != 0) + 1
    edges = np.r_[0, cut, len(labels)]
    return np.diff(edges).astype(int).tolist()


def regimes(tr: pd.DataFrame, te: pd.DataFrame, k: int, cols: list[str]) -> tuple[np.ndarray, np.ndarray, dict]:
    """(訓練の行の局面, 評価の行の局面, 記録)。k-means は訓練分割の日だけで当てる。"""
    Str, Ste = day_frame(tr, cols), day_frame(te, cols)
    med = Str.median()
    Str, Ste = Str.fillna(med).fillna(0.0), Ste.fillna(med).fillna(0.0)
    sc = StandardScaler().fit(Str)
    km = KMeans(n_clusters=k, random_state=SEED, n_init=10).fit(sc.transform(Str))
    dtr = pd.Series(km.labels_, index=Str.index)
    dte = pd.Series(km.predict(sc.transform(Ste)), index=Ste.index)
    rtr = dtr.reindex(pd.to_datetime(tr["ts"])).to_numpy()
    rte = dte.reindex(pd.to_datetime(te["ts"])).to_numpy()
    ltr, lte = run_lengths(dtr.to_numpy()), run_lengths(dte.to_numpy())
    doc = {"K": k, "種": SEED, "局面の列": cols,
           "訓練の日の局面": {str(i): int((dtr == i).sum()) for i in range(k)},
           "評価の日の局面": {str(i): int((dte == i).sum()) for i in range(k)},
           "評価の日の局面の割合": {str(i): round(float((dte == i).mean()), 4) for i in range(k)},
           "滞在日数の中央値（訓練）": float(np.median(ltr)) if ltr else None,
           "滞在日数の中央値（評価）": float(np.median(lte)) if lte else None}
    return rtr, rte, doc


def r2_regime(k: int, tr, te, feats, ctx):
    rcols = regime_columns(feats)
    if not rcols:
        raise SystemExit(f"⚠ {name_of(k)} は表に ex_UST* ／ ex_EURTO* の列が要る（own ＋ ex の表で回す）")
    cols = [c for c in feats if c.startswith("own_") or c.startswith("LEAK_")]
    rtr, rte, rdoc = regimes(tr, te, k, rcols)
    sc = StandardScaler().fit(tr[cols])
    Xtr = pd.DataFrame(sc.transform(tr[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    Xtr[r1._R], Xte[r1._R] = rtr, rte
    Xtr[r1._D] = pd.to_datetime(tr["ts"]).to_numpy()
    Xte[r1._D] = pd.to_datetime(te["ts"]).to_numpy()
    ytr = tr["y"].to_numpy(dtype=float)
    fn = r1.regime_model(ctx["model"], cols)
    cal = calibrate.fit(fn, Xtr, ytr, ctx)
    buy = np.asarray(cal.buy_pct(fn(Xtr, ytr, Xte, ctx)), dtype=float)
    days = Xtr.groupby(r1._R)[r1._D].nunique()
    doc = {**rdoc, "columns": cols, "全体で埋めた局面": [str(r) for r, n in days.items() if n < r1.MIN_DAYS], **cal.doc}
    return buy, doc


for _k in KS:
    register("detector", name_of(_k))(lambda tr, te, feats, ctx, _k=_k: r2_regime(_k, tr, te, feats, ctx))
