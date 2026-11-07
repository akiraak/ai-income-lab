"""登録簿 `20261107-path-cluster-baserate`（参考なし・枠の外。記録 dev-loop.md）:
過去 60 日の値動きの道筋を **ラベルを見ずに k-means で形に分け**、形ごとの訓練分割での「翌日に上がった割合」をそのまま買い% にする検知器 P1。

- 道筋: 表の `own_seq60_r0..r59`（r0 ＝ 足 i のリターン）を古い順に累積した 60 点。⚠ 行ごとに z スコア（平均 0・標準偏差 1）にする
  ＝ 水準と振れ幅を消して「形」だけを残す（登録簿の「z スコアで正規化」を形の意味で読んだ。標準偏差 0 の行は 0 の並び）。
- k-means: K ＝ 8・種 0・n_init 10（⚠ 事前固定・1 水準）。訓練分割の行だけで fit（ラベルは見ない）。評価の行は近い重心へ。
- 買い% ＝ 100 × 訓練分割でその塊の y > 0 の割合（⚠ 較正しない ＝ 割合がそのまま確率。学習器なし ＝ ctx の model は呼ばない）。
- leak 対照: 表に LEAK_ 列があれば、行ごとに標準化した LEAK_ 列を √60 倍して塊の入力に足す（60 点の形と同じ重み ＝ 塊の割り当てに先の値を混ぜる）。
- 基準線（数えない）「形の塊を乱数で割り当てた割合（8個）」: 評価の行の塊の番号を乱数で並べ替え（種 ＝ config の種）、同じ割合を引く
  （買い% − 50 を返す ＝ `fold_buy_pct` が 100 ／ 0 にする ＝ θ 50 の行と比べる）。
- 記録: 塊ごとの訓練の行数・割合・評価の行の塊の分布・買い% の幅（最大 − 最小）。
⚠ 登録するだけ ＝ この検知器 ／ 基準線を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from ail.features import seq
from ail.registry import register

K = 8                  # ⚠ 事前固定（登録簿）
SEED = 0
NAME = f"P1 60日の形の塊ごとの上がる割合（k-means・{K}個）"
BASELINE = f"形の塊を乱数で割り当てた割合（{K}個）"


def paths(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    """(行, 60) の形。cols は新しい順（r0..r59）。古い順に累積して行ごとに z スコア。"""
    r = df[cols].to_numpy(dtype=float)[:, ::-1]
    r = np.nan_to_num(r, nan=0.0)
    p = np.cumsum(r, axis=1)
    mu = p.mean(1, keepdims=True)
    sd = p.std(1, keepdims=True)
    return np.where(sd > 0, (p - mu) / np.where(sd > 0, sd, 1.0), 0.0)


def leak_block(tr: pd.DataFrame, te: pd.DataFrame, feats) -> tuple[np.ndarray, np.ndarray]:
    lk = [c for c in feats if c.startswith("LEAK_")]
    if not lk:
        return np.zeros((len(tr), 0)), np.zeros((len(te), 0))
    a, b = tr[lk].to_numpy(dtype=float), te[lk].to_numpy(dtype=float)
    mu, sd = np.nanmean(a, 0), np.nanstd(a, 0)
    sd = np.where(sd > 0, sd, 1.0)
    w = np.sqrt(seq.WINDOW)
    return (np.nan_to_num((a - mu) / sd) * w, np.nan_to_num((b - mu) / sd) * w)


def clusters(tr, te, feats) -> tuple[np.ndarray, np.ndarray, list[str]]:
    cols = seq.window_columns(feats)
    ltr, lte = leak_block(tr, te, feats)
    Ztr = np.hstack([paths(tr, cols), ltr])
    Zte = np.hstack([paths(te, cols), lte])
    km = KMeans(n_clusters=K, random_state=SEED, n_init=10).fit(Ztr)
    lk = [c for c in feats if c.startswith("LEAK_")]
    return km.labels_.astype(int), km.predict(Zte).astype(int), cols + lk


def rates(ctr: np.ndarray, ytr: np.ndarray) -> np.ndarray:
    """塊ごとの y > 0 の割合（訓練に 1 行も無い塊は全体の割合）。"""
    up = ytr > 0
    base = float(up.mean()) if len(up) else 0.5
    out = np.full(K, base)
    for c in range(K):
        m = ctr == c
        if m.any():
            out[c] = float(up[m].mean())
    return out


def p1(tr, te, feats, ctx, perm_seed: int | None = None):
    ctr, cte, cols = clusters(tr, te, feats)
    ytr = tr["y"].to_numpy(dtype=float)
    rate = rates(ctr, ytr)
    if perm_seed is not None:
        cte = np.random.default_rng(perm_seed).permutation(cte)
    buy = 100.0 * rate[cte]
    doc = {"columns": cols, "K": K, "種": SEED, "較正": "なし（訓練分割の塊ごとの割合）",
           "訓練の行の塊": {str(c): int((ctr == c).sum()) for c in range(K)},
           "塊ごとの上がる割合": {str(c): round(float(rate[c]), 5) for c in range(K)},
           "評価の行の塊": {str(c): int((cte == c).sum()) for c in range(K)},
           "買い%の幅": round(float(rate.max() - rate.min()) * 100.0, 3)}
    return buy, doc


register("detector", NAME)(p1)


@register("model", BASELINE)
def random_path_cluster(tr, te, feats, ctx):
    """基準線（数えない）: 買い% ＞ 50 なら正（`fold_buy_pct` が 100 ／ 0 にする）。"""
    buy, _doc = p1(tr, te, feats, ctx, perm_seed=int(ctx.get("seed", 0)))
    return buy - 50.0
