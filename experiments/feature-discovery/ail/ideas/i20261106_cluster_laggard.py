"""登録簿 `20261106-cluster-laggard-revert`（参考なし・枠の外。記録 dev-loop.md）:
訓練分割の日次リターンの相関から銘柄の集まりを作り、評価期間で「集まりの平均より直近 5 日に遅れた銘柄」を買う規則 L1（学習器なし）。

- 集まり: 訓練分割の `own_ret_1` を 日 × 銘柄 に並べた相関（2 銘柄の重なりが 60 日未満の組は 0）→ 距離 1 − ρ →
  階層クラスタ（平均連結）を集まりの数 8 で切る（⚠ 事前固定・1 水準）。fold ごとに作り直す。
- 点: 評価の各行で s ＝ −（`own_ret_5` − 同じ日・同じ集まりの `own_ret_5` の平均）。
- 買い% ＝ 訓練分割の s の分布での順位 × 100（同点は半分）。⚠ 較正は Platt を通さない（学習器なしの規則 ＝ 順位そのもの。
  Platt は向きを学び直すので規則が逆になりうる）。⚠ ctx の model は呼ばない（config の model は綴りのためだけ）。
- 基準線（数えない）「集まりを乱数で割り当てた遅れの買い（8個）」: 同じ大きさの集まりに銘柄を乱数で割り当て（種 ＝ config の種）、
  同じ規則の順位が 50 を超える行で買う（基準線の買い% は 0 ／ 100 ＝ θ 50 の行と比べる）。
- ⚠ leak 対照: 規則は LEAK_ 列を読まない ＝ leak は跳ねない（器の確かめにならない）。
⚠ 登録するだけ ＝ この検知器 ／ 基準線を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from ail.registry import register

N_CLUSTERS = 8         # ⚠ 事前固定（登録簿）
LOOKBACK = "own_ret_5"
DAILY = "own_ret_1"
MIN_OVERLAP = 60
NAME = f"L1 集まりの中で遅れた銘柄を買う（相関の集まり{N_CLUSTERS}個・5日）"
BASELINE = f"集まりを乱数で割り当てた遅れの買い（{N_CLUSTERS}個）"


def clusters(tr: pd.DataFrame, n: int = N_CLUSTERS) -> dict[str, int]:
    """訓練分割だけで 銘柄 → 集まりの番号（1 始まり）。"""
    wide = tr.pivot_table(index="ts", columns="symbol", values=DAILY, aggfunc="first")
    rho = wide.corr(min_periods=MIN_OVERLAP).fillna(0.0).to_numpy()
    np.fill_diagonal(rho, 1.0)
    d = np.clip(1.0 - rho, 0.0, 2.0)
    d = (d + d.T) / 2.0
    np.fill_diagonal(d, 0.0)
    syms = [str(s) for s in wide.columns]
    if len(syms) <= n:
        return {s: i + 1 for i, s in enumerate(syms)}
    lab = fcluster(linkage(squareform(d, checks=False), method="average"), t=n, criterion="maxclust")
    return dict(zip(syms, (int(x) for x in lab)))


def laggard_score(df: pd.DataFrame, cl: dict[str, int]) -> np.ndarray:
    """s ＝ −（own_ret_5 − 同じ日・同じ集まりの平均）。集まりの無い銘柄（訓練に居なかった）は自分だけの集まり ＝ 0。"""
    sym = df["symbol"].astype(str)
    g = sym.map(cl)
    g = g.where(g.notna(), "solo:" + sym)
    r = df[LOOKBACK].astype(float)
    r = r.fillna(r.median() if r.notna().any() else 0.0)
    mean = r.groupby([pd.to_datetime(df["ts"]).to_numpy(), g.astype(str).to_numpy()]).transform("mean")
    return -(r - mean).to_numpy(dtype=float)


def rank_pct(ref: np.ndarray, x: np.ndarray) -> np.ndarray:
    """ref の分布での x の順位 × 100（同点は半分）。"""
    ref = np.sort(np.asarray(ref, dtype=float))
    lo = np.searchsorted(ref, x, side="left")
    hi = np.searchsorted(ref, x, side="right")
    return 100.0 * (lo + hi) / (2.0 * len(ref))


def l1_laggard(tr, te, feats, ctx, cl: dict[str, int] | None = None):
    cl = clusters(tr) if cl is None else cl
    s_tr, s_te = laggard_score(tr, cl), laggard_score(te, cl)
    buy = rank_pct(s_tr, s_te)
    sizes = pd.Series(cl).value_counts().sort_index()
    doc = {"columns": [LOOKBACK], "集まりの数": int(len(sizes)), "集まりの大きさ": {str(k): int(v) for k, v in sizes.items()},
           "集まり": {k: int(v) for k, v in sorted(cl.items())}, "較正": "なし（訓練分割の順位）"}
    return buy, doc


def shuffled(cl: dict[str, int], seed: int) -> dict[str, int]:
    """同じ大きさの集まりに銘柄を乱数で割り当て直す。"""
    syms = sorted(cl)
    lab = np.array([cl[s] for s in syms])
    rng = np.random.default_rng(seed)
    return dict(zip(syms, (int(x) for x in rng.permutation(lab))))


register("detector", NAME)(l1_laggard)


@register("model", BASELINE)
def random_cluster_laggard(tr, te, feats, ctx):
    """基準線（数えない）: 買い% ＞ 50 なら正（`fold_buy_pct` が 100 ／ 0 にする）。"""
    cl = shuffled(clusters(tr), int(ctx.get("seed", 0)))
    buy, _doc = l1_laggard(tr, te, feats, ctx, cl=cl)
    return buy - 50.0
