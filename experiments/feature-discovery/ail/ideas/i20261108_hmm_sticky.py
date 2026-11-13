"""登録簿 `20261108-hmm-sticky-regime-hold`（参考あり・枠の外。記録 dev-loop.md）:
遷移を抑えた隠れマルコフ（HMM）で **数十日続く局面** を作り、局面ごとの「翌日に上がった割合」を買い% にする検知器 R4。
R3 の記録が「決着なしなら次はこれ」と先に書いた手（k-means の局面は 1〜2 日で入れ替わった）。

- 観測（日ごとに 2 列）: 表の銘柄の等加重の日次リターン（`own_ret_1` の断面平均）と、その 20 日の標準偏差。
  ⚠ **1 日遅れ**（登録簿）: 足 t の行が見るのは t − 1 日までの 2 列。欠け（頭の 20 日）は訓練の中央値で埋める。
- ガウス HMM（対角の共分散・K ＝ 2 ／ 3・事前固定）を **訓練分割の日だけ** で EM（最大 50 反復・初期値は k-means 種 0）。
  ⚠ 遷移行列の対角に下限 0.95（期待滞在 1 ／ (1 − a) ≥ 20 営業日 ＝ 事前固定）: M ステップのたびに対角を 0.95 以上に上げ、残りを比で配り直す。
- 状態の確率は **前向き（filtering）だけ**: P(状態 | その日までの観測)。評価の日は訓練の最後の日から前向きに続ける（smoothing は使わない）。
- 素の点 ＝ Σ_k P(状態 k | 過去) × 訓練分割での状態 k の「翌日に上がった行の割合」（重みは訓練の日の filtering の確率）。
  これを Platt（`calibrate.fit_from_predictions` ＝ 既存の較正と同じ計算）に通して買い% にする。**同じ日の全銘柄に同じ買い%**（断面の情報は使わない）。
  ⚠ **Platt を当てるのは訓練分割の全部の行**（source ＝ "train"。回す前に決めた読み ＝ 2026-11-13）: 素の点は日ごとに 1 つの値で、
  既存の較正の尻（訓練の最後の 10% ＝ 最初の fold では 40 日足らず）は 1 つの局面に収まりうる ＝ 傾きが決まらず買い% が 0 ／ 100 に張り付く
  （作り物の表で確かめた。本物の結果は見ていない）。HMM は y を見ないので、訓練内で動くのは状態ごとの割合だけ。
- leak 対照: LEAK_ 列の日ごとの断面平均（遅らせない）を観測の列に足す。⚠ 滞在に下限のある局面は翌日の符号に付いていけないので、
  leak が跳ねるとは限らない（作りから。先の日を見ないことはテストで確かめる）。
- 学習器（ctx の model）は呼ばない（識別項目の綴りのためだけに config に Ridge を書く ＝ L1・P1 と同じ都合）。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from ail.models import calibrate
from ail.registry import register

KS = (2, 3)            # ⚠ 事前固定（登録簿）
DWELL_DAYS = 20        # ⚠ 事前固定: 期待滞在の下限（営業日）
DIAG_MIN = 1.0 - 1.0 / DWELL_DAYS
VOL_WINDOW = 20
MAX_ITER = 50
TOL = 1e-4
VAR_FLOOR = 1e-3       # 標準化した観測の分散の下限
SEED = 0
_D = "__day"


def name_of(k: int) -> str:
    return f"R4 粘る局面で持つ（HMM・{k}状態）"


def floor_diag(A: np.ndarray, d: float = DIAG_MIN) -> np.ndarray:
    """対角を d 以上に上げ、行の残りを比のまま 1 − 対角 に配り直す（残りが 0 なら等分）。"""
    A = np.array(A, dtype=float)
    k = len(A)
    for i in range(k):
        if A[i, i] >= d:
            continue
        off = A[i].copy()
        off[i] = 0.0
        s = off.sum()
        A[i] = off / s * (1.0 - d) if s > 0 else np.full(k, (1.0 - d) / max(k - 1, 1))
        A[i, i] = d
    return A


def _logb(X: np.ndarray, mu: np.ndarray, var: np.ndarray) -> np.ndarray:
    return -0.5 * (((X[:, None, :] - mu[None]) ** 2) / var[None] + np.log(2.0 * np.pi * var[None])).sum(2)


def forward(logb: np.ndarray, start: np.ndarray, A: np.ndarray, first_is_prior: bool = True):
    """前向きの確率 α（行ごとに和 1）・尺度 c・尺度を外した出力 b。`start` ＝ 最初の日の事前の分布
    （`first_is_prior=False` なら 1 つ前の日の α ＝ 遷移を 1 回掛けてから使う）。"""
    T, K = logb.shape
    b = np.exp(logb - logb.max(1, keepdims=True))
    alpha, c = np.empty((T, K)), np.empty(T)
    prev = start if first_is_prior else start @ A
    for t in range(T):
        a = prev * b[t]
        c[t] = a.sum()
        if not c[t] > 0:                       # 数値の底（全部の状態で出力が 0）→ 事前のまま
            a, c[t] = prev, 1.0
        alpha[t] = a / a.sum()
        prev = alpha[t] @ A
    return alpha, c, b


def fit_hmm(X: np.ndarray, k: int) -> dict:
    """標準化した観測 X（日 × 列）→ {pi, A, mu, var, 反復, 対数尤度}。EM（Baum–Welch）・対角は毎回 `floor_diag`。"""
    T = len(X)
    km = KMeans(n_clusters=k, random_state=SEED, n_init=10).fit(X)
    mu = km.cluster_centers_.astype(float)
    var = np.vstack([X[km.labels_ == j].var(0) if (km.labels_ == j).sum() > 1 else X.var(0) for j in range(k)])
    var = np.maximum(var, VAR_FLOOR)
    pi = np.full(k, 1.0 / k)
    A = floor_diag(np.full((k, k), 1.0 / k))
    ll_prev, ll, it = -np.inf, -np.inf, 0
    for it in range(1, MAX_ITER + 1):
        logb = _logb(X, mu, var)
        alpha, c, b = forward(logb, pi, A)
        ll = float(np.log(c).sum() + logb.max(1).sum())
        beta = np.ones((T, k))
        for t in range(T - 2, -1, -1):
            beta[t] = (A @ (b[t + 1] * beta[t + 1])) / c[t + 1]
        gamma = alpha * beta
        gamma /= gamma.sum(1, keepdims=True)
        xi = np.zeros((k, k))
        for t in range(T - 1):
            xi += alpha[t][:, None] * A * (b[t + 1] * beta[t + 1])[None, :] / c[t + 1]
        pi = gamma[0] / gamma[0].sum()
        A = floor_diag(xi / np.maximum(xi.sum(1, keepdims=True), 1e-300))
        w = gamma.sum(0)
        mu = (gamma.T @ X) / np.maximum(w[:, None], 1e-300)
        var = np.maximum((gamma.T @ (X ** 2)) / np.maximum(w[:, None], 1e-300) - mu ** 2, VAR_FLOOR)
        if abs(ll - ll_prev) < TOL * max(1.0, abs(ll)):
            break
        ll_prev = ll
    return {"pi": pi, "A": A, "mu": mu, "var": var, "反復": it, "対数尤度": ll}


def day_obs(tr: pd.DataFrame, te: pd.DataFrame, feats) -> tuple[pd.DataFrame, list[str]]:
    """日ごとの観測（index ＝ 日・訓練 ＋ 評価の通し）。2 列は 1 日遅れ・LEAK_ の平均は遅らせない。"""
    lk = [c for c in feats if c.startswith("LEAK_")]
    df = pd.concat([tr[["ts", "own_ret_1"] + lk], te[["ts", "own_ret_1"] + lk]], ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"])
    g = df.groupby("ts", sort=True)
    m = g["own_ret_1"].mean()
    obs = pd.DataFrame({"__o_ret": m.shift(1), "__o_vol": m.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std().shift(1)})
    for j, c in enumerate(lk):
        obs[f"__o_leak{j}"] = g[c].mean()
    return obs, list(obs.columns)


def _runs(states: np.ndarray) -> list[int]:
    out, n = [], 0
    for i, s in enumerate(states):
        n += 1
        if i + 1 == len(states) or states[i + 1] != s:
            out.append(n)
            n = 0
    return out


def hmm_points(k: int, ocols: list[str], Xtr: pd.DataFrame, ytr, Xte: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, dict]:
    """行の表（`__day` と観測の列つき）→ (訓練の行の素の点, 評価の行の素の点, 記録)。素の点は 0〜1・日ごとに 1 つの値。"""
    up = np.asarray(ytr, dtype=float) > 0
    dtr = Xtr.assign(_up=up).groupby(_D, sort=True).agg({**{c: "first" for c in ocols}, "_up": ["sum", "count"]})
    Otr = dtr[[(c, "first") for c in ocols]].to_numpy(dtype=float)
    n_up, n = dtr[("_up", "sum")].to_numpy(dtype=float), dtr[("_up", "count")].to_numpy(dtype=float)
    dte = Xte.groupby(_D, sort=True)[ocols].first()
    mu0, sd0 = Otr.mean(0), Otr.std(0)
    sd0 = np.where(sd0 > 0, sd0, 1.0)
    Ztr, Zte = (Otr - mu0) / sd0, (dte.to_numpy(dtype=float) - mu0) / sd0
    h = fit_hmm(Ztr, k)
    a_tr, _c, _b = forward(_logb(Ztr, h["mu"], h["var"]), h["pi"], h["A"])
    rate = (a_tr.T @ n_up) / np.maximum(a_tr.T @ n, 1e-300)
    a_te, _c, _b = forward(_logb(Zte, h["mu"], h["var"]), a_tr[-1], h["A"], first_is_prior=False)
    p_tr = pd.Series(a_tr @ rate, index=dtr.index)
    p_te = pd.Series(a_te @ rate, index=dte.index)
    s_te = a_te.argmax(1)
    rec = {"K": k, "対角の下限": DIAG_MIN, "遷移行列": np.round(h["A"], 4).tolist(), "反復": int(h["反復"]),
           "状態ごとの上がる割合": [round(float(x), 5) for x in rate],
           "訓練の日の重み": [round(float(x), 1) for x in a_tr.sum(0)],
           "評価の日の状態": {str(j): int((s_te == j).sum()) for j in range(k)},
           "評価の滞在日数の中央値": float(np.median(_runs(s_te))) if len(s_te) else None,
           "評価の状態の切り替わり": int(max(len(_runs(s_te)) - 1, 0)),
           "素の点の幅": round(float(p_te.max() - p_te.min()) * 100.0, 3) if len(p_te) else 0.0}
    return p_tr.reindex(Xtr[_D]).to_numpy(dtype=float), p_te.reindex(Xte[_D]).to_numpy(dtype=float), rec


def r4_hmm(k: int, tr, te, feats, ctx):
    obs, ocols = day_obs(tr, te, feats)
    dtr, dte = pd.to_datetime(tr["ts"]), pd.to_datetime(te["ts"])
    obs = obs.fillna(obs.loc[obs.index.isin(dtr)].median()).fillna(0.0)
    Xtr = obs.reindex(dtr).reset_index(drop=True)
    Xte = obs.reindex(dte).reset_index(drop=True)
    Xtr[_D], Xte[_D] = dtr.to_numpy(), dte.to_numpy()
    ytr = tr["y"].to_numpy(dtype=float)
    p_tr, p_te, rec = hmm_points(k, ocols, Xtr, ytr, Xte)
    cal = calibrate.fit_from_predictions(p_tr, ytr, "train")
    buy = np.asarray(cal.buy_pct(p_te), dtype=float)
    doc = {"columns": ocols, **rec, "買い%の幅": round(float(buy.max() - buy.min()), 3) if len(buy) else 0.0,
           "買い%>50の日の割合": round(float((pd.Series(buy).groupby(dte.to_numpy()).first() > 50).mean()), 4) if len(buy) else None, **cal.doc}
    return buy, doc


for _k in KS:
    register("detector", name_of(_k))(lambda tr, te, feats, ctx, _k=_k: r4_hmm(_k, tr, te, feats, ctx))
