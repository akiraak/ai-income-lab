"""レジストリ `20261124-seq-cluster-analog`（参考なし・枠の外。記録 dev-loop.md）:
過去 60 日の値動きの道筋を **ラベルを見ずに k-means で 8 つの型に分け**、型ごとの訓練分割の翌日リターンの t 値を、
訓練分割の中の順位で 0〜100 に写して買い% にする検知器 A1。

- 道筋: 表の `own_seq60_r0..r59`（r0 ＝ 足 i のリターン）を古い順に累積し、exp を取った 60 点 ＝ 窓の始点の価格で割った価格の比。
  ⚠ 20261107 の P1（`path-kmeans8`）は行ごとの z スコア（形だけ）。こちらは始点で割るだけ ＝ 値動きの大きさが残る（レジストリの structure）。
- k-means: K ＝ 8・シード 0・n_init 10（⚠ 事前固定・1 水準）。訓練分割の行だけで fit（ラベルは見ない）。評価の行は近い中心へ。
- 型ごとの t ＝ 訓練分割の y（翌日 log リターン）の平均 ÷ 標準誤差（行が 2 未満 ／ 標準偏差 0 の型は 0）。
- 買い% ＝ 型の t を、訓練分割の行の t の分布の中の順位（中央順位 ＝ (＜ の数 ＋ ≦ の数) ÷ 2）で 0〜100 に写したもの。
  ⚠ 較正しない・学習器なし（ctx の model は呼ばない）。評価分割の値は何も使わない。
- leak 対照: 表に LEAK_ 列があれば、行ごとに標準化した LEAK_ 列を、訓練分割の道筋のブロックと分散の合計が同じになる倍率で足す
  （塊の割り当てに先の値を混ぜる）。倍率は訓練分割だけで決める。
- 記録: 型ごとの訓練の行数・平均・t・買い%・評価の行の型の分布・買い% の幅。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from ail.features import seq
from ail.registry import register

K = 8                  # ⚠ 事前固定（レジストリ・結果を見て振らない）
SEED = 0
NAME = f"A1 60日の道筋の型ごとの翌日平均のt値の順位（k-means・{K}個）"


def paths(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    """(行, 60) の道筋。cols は新しい順（r0..r59）。古い順に累積して exp ＝ 始点の価格で割った比。"""
    r = df[cols].to_numpy(dtype=float)[:, ::-1]
    r = np.nan_to_num(r, nan=0.0)
    return np.exp(np.cumsum(r, axis=1))


def leak_block(tr: pd.DataFrame, te: pd.DataFrame, feats, path_tr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lk = [c for c in feats if c.startswith("LEAK_")]
    if not lk:
        return np.zeros((len(tr), 0)), np.zeros((len(te), 0))
    a, b = tr[lk].to_numpy(dtype=float), te[lk].to_numpy(dtype=float)
    mu, sd = np.nanmean(a, 0), np.nanstd(a, 0)
    sd = np.where(sd > 0, sd, 1.0)
    za, zb = np.nan_to_num((a - mu) / sd), np.nan_to_num((b - mu) / sd)
    total = float(path_tr.var(0).sum())                       # 訓練分割の道筋のブロックの分散の合計
    w = np.sqrt(total / len(lk)) if total > 0 else 1.0
    return za * w, zb * w


def clusters(tr, te, feats) -> tuple[np.ndarray, np.ndarray, list[str]]:
    cols = seq.window_columns(feats)
    ptr, pte = paths(tr, cols), paths(te, cols)
    ltr, lte = leak_block(tr, te, feats, ptr)
    km = KMeans(n_clusters=K, random_state=SEED, n_init=10).fit(np.hstack([ptr, ltr]))
    lk = [c for c in feats if c.startswith("LEAK_")]
    return km.labels_.astype(int), km.predict(np.hstack([pte, lte])).astype(int), cols + lk


def tstats(ctr: np.ndarray, ytr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """型ごとの (平均, t)。行が 2 未満 ／ 標準偏差 0 の型は t 0。"""
    mean, t = np.zeros(K), np.zeros(K)
    for c in range(K):
        y = ytr[(ctr == c) & np.isfinite(ytr)]
        if len(y) == 0:
            continue
        mean[c] = float(y.mean())
        if len(y) >= 2:
            s = float(y.std(ddof=1))
            if s > 0:
                t[c] = mean[c] / (s / np.sqrt(len(y)))
    return mean, t


def rank_pct(t: np.ndarray, ctr: np.ndarray) -> np.ndarray:
    """型ごとの買い% ＝ 型の t の、訓練分割の行の t の分布の中の中央順位 × 100。"""
    row_t = t[ctr]
    n = len(row_t)
    if n == 0:
        return np.full(K, 50.0)
    out = np.empty(K)
    for c in range(K):
        lt = float((row_t < t[c]).sum())
        le = float((row_t <= t[c]).sum())
        out[c] = 100.0 * (lt + le) / (2.0 * n)
    return out


def a1(tr, te, feats, ctx):
    ctr, cte, cols = clusters(tr, te, feats)
    ytr = tr["y"].to_numpy(dtype=float)
    mean, t = tstats(ctr, ytr)
    pct = rank_pct(t, ctr)
    buy = pct[cte]
    doc = {"columns": cols, "K": K, "シード": SEED, "較正": "なし（訓練分割の型ごとの t の順位）",
           "訓練の行の型": {str(c): int((ctr == c).sum()) for c in range(K)},
           "型ごとの平均": {str(c): round(float(mean[c]), 7) for c in range(K)},
           "型ごとのt": {str(c): round(float(t[c]), 4) for c in range(K)},
           "型ごとの買い%": {str(c): round(float(pct[c]), 4) for c in range(K)},
           "評価の行の型": {str(c): int((cte == c).sum()) for c in range(K)},
           "買い%の幅": round(float(pct.max() - pct.min()), 3)}
    return buy, doc


register("detector", NAME)(a1)
