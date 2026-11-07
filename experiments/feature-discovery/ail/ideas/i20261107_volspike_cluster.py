"""登録簿 `20261107-volspike-cluster-lag`（参考あり・枠の外。記録 dev-loop.md）:
L1（`i20261106_cluster_laggard`）の集まりを **出来高が同じ日に跳ねる銘柄どうし** で作り直し、仲間が出来高を伴って上がった翌日に
まだ跳ねていない銘柄を買う規則 L2（学習器なし）。

- 出来高: 表には日々の出来高が無い（`own_vratio_*` は比）ので、表の close と同じ層の **調整済み日足の `volume`**
  （`store.adjusted_dir("d")`。`cli.run.next_open_returns` と同じ置き場）を (銘柄, 日) で継ぐ。
  z ＝ (出来高 − 前の 60 日の平均) ／ 前の 60 日の標準偏差（⚠ その日を含まない 60 日・足りない日は NaN ＝ 跳ねていない）。跳ね ＝ z > 2。
- 集まり: 訓練分割の日だけで、銘柄ごとの「跳ねた日」の集合の Jaccard 類似度 → 距離 1 − J → 階層クラスタ（平均連結）を 8 個で切る
  （⚠ 事前固定 ＝ L1 と同じ数）。fold ごとに作り直す。
- 点: 行ごとに a ＝ 同じ日・同じ集まりの **自分以外の仲間** のうち「跳ね かつ `own_ret_1` > 0」の割合。
  合図 ＝ a > 0.5（過半数）かつ 自分は跳ねていない。点 s ＝ 合図 ? a : 0。
- 買い% ＝ 訓練分割の s の分布での順位 × 100（同点は半分 ＝ L1 の `rank_pct`）。⚠ Platt を通さない。⚠ ctx の model は呼ばない。
- 基準線（数えない）「出来高の集まりを乱数で割り当てた遅れの買い（8個）」: 同じ大きさの集まりに銘柄を乱数で割り当て（種 ＝ config の種）、
  同じ規則の順位 − 50 を返す。
- ⚠ leak 対照: 規則は LEAK_ 列を読まない ＝ leak は跳ねない（器の確かめにならない ＝ 登録簿の failure_modes 3）。
- 記録: 集まりの大きさ・合図の日数（訓練 ／ 評価）・合図の行の翌日の平均 y（訓練）。
⚠ 登録するだけ ＝ この検知器 ／ 基準線を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from ail.data import store
from ail.ideas import i20261106_cluster_laggard as l1
from ail.registry import register

N_CLUSTERS = 8         # ⚠ 事前固定（登録簿 ＝ L1 と同じ）
WINDOW = 60
Z_SPIKE = 2.0
MAJORITY = 0.5
NAME = f"L2 出来高が同じ日に跳ねる仲間の遅れを買う（集まり{N_CLUSTERS}個・翌日）"
BASELINE = f"出来高の集まりを乱数で割り当てた遅れの買い（{N_CLUSTERS}個）"
VZ = "__vol_z"

_CACHE: dict[tuple[str, str], pd.Series] = {}


def volume_z(volume: pd.Series, window: int = WINDOW) -> pd.Series:
    """前の window 日（その日を含まない）の平均と標準偏差で測った z。"""
    prev = volume.shift(1).rolling(window, min_periods=window)
    sd = prev.std()
    return (volume - prev.mean()) / sd.where(sd > 0)


def _z_of(symbol: str, directory: str) -> pd.Series:
    key = (directory, symbol)
    if key not in _CACHE:
        if not os.path.exists(store.path_of(directory, symbol)):
            _CACHE[key] = pd.Series(dtype=float)
        else:
            b = store.read_bars(directory, symbol)
            z = volume_z(b["volume"].astype(float).reset_index(drop=True))
            _CACHE[key] = pd.Series(z.to_numpy(), index=pd.to_datetime(b["ts"]).to_numpy())
    return _CACHE[key]


def attach_z(df: pd.DataFrame, directory: str | None = None) -> np.ndarray:
    """行ごとの出来高 z（表に `__vol_z` があればそれ ＝ テスト用）。"""
    if VZ in df:
        return df[VZ].to_numpy(dtype=float)
    directory = directory or store.adjusted_dir("d")
    out = np.full(len(df), np.nan)
    ts = pd.to_datetime(df["ts"]).to_numpy()
    for s, idx in df.groupby(df["symbol"].astype(str)).indices.items():
        z = _z_of(s, directory)
        if len(z):
            out[idx] = z.reindex(ts[idx]).to_numpy()
    return out


def clusters(tr: pd.DataFrame, z: np.ndarray, n: int = N_CLUSTERS) -> dict[str, int]:
    """訓練分割だけで 銘柄 → 集まりの番号（1 始まり）。跳ねた日の集合の Jaccard。"""
    spike = pd.DataFrame({"symbol": tr["symbol"].astype(str).to_numpy(), "ts": pd.to_datetime(tr["ts"]).to_numpy(),
                          "s": (z > Z_SPIKE)})
    wide = spike.pivot_table(index="ts", columns="symbol", values="s", aggfunc="max", fill_value=False).astype(bool)
    syms = [str(s) for s in wide.columns]
    if len(syms) <= n:
        return {s: i + 1 for i, s in enumerate(syms)}
    M = wide.to_numpy(dtype=float)
    inter = M.T @ M
    cnt = M.sum(0)
    union = cnt[:, None] + cnt[None, :] - inter
    J = np.where(union > 0, inter / np.where(union > 0, union, 1.0), 0.0)
    d = np.clip(1.0 - J, 0.0, 1.0)
    d = (d + d.T) / 2.0
    np.fill_diagonal(d, 0.0)
    lab = fcluster(linkage(squareform(d, checks=False), method="average"), t=n, criterion="maxclust")
    return dict(zip(syms, (int(x) for x in lab)))


def score(df: pd.DataFrame, z: np.ndarray, cl: dict[str, int]) -> tuple[np.ndarray, np.ndarray]:
    """(点 s, 合図)。仲間 ＝ 同じ日・同じ集まりの自分以外。集まりの無い銘柄（訓練に居なかった）は仲間なし ＝ 合図なし。"""
    sym = df["symbol"].astype(str)
    g = sym.map(cl)
    g = g.where(g.notna(), "solo:" + sym).astype(str).to_numpy()
    spike = np.nan_to_num(z, nan=-np.inf) > Z_SPIKE
    hit = (spike & (df["own_ret_1"].to_numpy(dtype=float) > 0)).astype(float)
    key = [pd.to_datetime(df["ts"]).to_numpy(), g]
    tot = pd.Series(hit).groupby(key).transform("sum").to_numpy()
    n = pd.Series(np.ones(len(df))).groupby(key).transform("sum").to_numpy()
    peers = n - 1.0
    a = np.where(peers > 0, (tot - hit) / np.where(peers > 0, peers, 1.0), 0.0)
    sig = (a > MAJORITY) & ~spike
    return np.where(sig, a, 0.0), sig


def l2(tr, te, feats, ctx, cl: dict[str, int] | None = None):
    ztr, zte = attach_z(tr), attach_z(te)
    cl = clusters(tr, ztr) if cl is None else cl
    s_tr, sig_tr = score(tr, ztr, cl)
    s_te, sig_te = score(te, zte, cl)
    buy = l1.rank_pct(s_tr, s_te)
    sizes = pd.Series(cl).value_counts().sort_index()
    ytr = tr["y"].to_numpy(dtype=float)
    dtr, dte = pd.to_datetime(tr["ts"]).to_numpy(), pd.to_datetime(te["ts"]).to_numpy()
    doc = {"columns": ["own_ret_1", "volume（調整済み日足）"], "集まりの数": int(len(sizes)),
           "集まりの大きさ": {str(k): int(v) for k, v in sizes.items()}, "集まり": {k: int(v) for k, v in sorted(cl.items())},
           "合図の行（訓練）": int(sig_tr.sum()), "合図の日（訓練）": int(len(np.unique(dtr[sig_tr]))),
           "合図の行（評価）": int(sig_te.sum()), "合図の日（評価）": int(len(np.unique(dte[sig_te]))),
           "合図の行の翌日の平均y（訓練）": float(np.nanmean(ytr[sig_tr])) if sig_tr.any() else None,
           "全行の翌日の平均y（訓練）": float(np.nanmean(ytr)),
           "出来高zが無い行の割合（評価）": round(float(np.isnan(zte).mean()), 4), "較正": "なし（訓練分割の順位）"}
    return buy, doc


register("detector", NAME)(l2)


@register("model", BASELINE)
def random_volspike_cluster(tr, te, feats, ctx):
    """基準線（数えない）: 買い% ＞ 50 なら正（`fold_buy_pct` が 100 ／ 0 にする）。"""
    cl = l1.shuffled(clusters(tr, attach_z(tr)), int(ctx.get("seed", 0)))
    buy, _doc = l2(tr, te, feats, ctx, cl=cl)
    return buy - 50.0
