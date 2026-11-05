"""登録簿 `20261104-regime-cluster-ridge`（参考あり・枠の外。記録 dev-loop.md）:
ラベルを使わずに日ごとの相場の局面を k-means で分け、**局面ごとに別の学習器（Ridge）** を学ぶ検知器 R1。

- 局面の入力（日ごとに 4 列・断面の要約）: own_ret_1 の断面平均 ／ own_ret_1 の断面のばらつき（標準偏差）／ own_vol_20 の断面平均 ／
  終値がその行までの SMA200 より上にある銘柄の割合（表の close から。SMA の無い銘柄は数えない・1 本も無い日は訓練の中央値で埋める）。
- 訓練分割の日だけで標準化と k-means（K ＝ 2 ／ 4・種 0・事前固定）を当て、評価の日は近い重心に振り分ける。
- 局面ごとに own 35 列（＋ leak 対照の LEAK_ 列）× ctx の model を学ぶ。訓練の中で 250 日に満たない局面は全体の 1 本で埋める。
- 較正は全体で 1 回（`calibrate.fit` ＝ 素の own × Ridge と同じ訓練分割の尻。尻でも局面ごとの学習器で予測する）。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from ail.models import calibrate
from ail.registry import register

KS = (2, 4)            # ⚠ 事前固定（登録簿）
SEED = 0
MIN_DAYS = 250         # 訓練の中でこれより少ない局面は全体の学習器で埋める
SMA = 200
REGIME_COLS = ("平均リターン", "ばらつき", "平均ボラ", "SMA200より上の割合")
_R, _D = "__regime", "__day"


def name_of(k: int) -> str:
    return f"R1 局面別 Ridge（k-means・{k}個）"


def above_sma(tr: pd.DataFrame, te: pd.DataFrame, window: int = SMA) -> pd.Series:
    """行ごとの「終値 > その行までの SMA」（1.0 ／ 0.0 ／ SMA が無ければ NaN）。index は (分割, 位置)。"""
    a = tr[["symbol", "ts", "close"]].assign(_s=0, _i=np.arange(len(tr)))
    b = te[["symbol", "ts", "close"]].assign(_s=1, _i=np.arange(len(te)))
    df = pd.concat([a, b], ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.sort_values(["symbol", "ts", "_s"])
    sma = df.groupby("symbol", sort=False)["close"].transform(lambda s: s.rolling(window, min_periods=window).mean())
    v = (df["close"] > sma).astype(float).where(sma.notna())
    return pd.Series(v.to_numpy(), index=pd.MultiIndex.from_arrays([df["_s"].to_numpy(), df["_i"].to_numpy()]))


def day_summary(df: pd.DataFrame, above: np.ndarray) -> pd.DataFrame:
    ts = pd.to_datetime(df["ts"]).to_numpy()
    g = pd.DataFrame({"r": df["own_ret_1"].to_numpy(dtype=float), "v": df["own_vol_20"].to_numpy(dtype=float),
                      "a": above, "ts": ts}).groupby("ts")
    out = pd.DataFrame({REGIME_COLS[0]: g["r"].mean(), REGIME_COLS[1]: g["r"].std(), REGIME_COLS[2]: g["v"].mean(),
                        REGIME_COLS[3]: g["a"].mean()})
    return out


def regimes(tr: pd.DataFrame, te: pd.DataFrame, k: int) -> tuple[np.ndarray, np.ndarray, dict]:
    """(訓練の行の局面, 評価の行の局面, 記録)。k-means は訓練分割の日だけで当てる。"""
    ab = above_sma(tr, te)
    Str = day_summary(tr, ab.loc[0].sort_index().to_numpy())
    Ste = day_summary(te, ab.loc[1].sort_index().to_numpy())
    med = Str.median()
    Str, Ste = Str.fillna(med), Ste.fillna(med)
    sc = StandardScaler().fit(Str)
    km = KMeans(n_clusters=k, random_state=SEED, n_init=10).fit(sc.transform(Str))
    dtr = pd.Series(km.labels_, index=Str.index)
    dte = pd.Series(km.predict(sc.transform(Ste)), index=Ste.index)
    rtr = dtr.reindex(pd.to_datetime(tr["ts"])).to_numpy()
    rte = dte.reindex(pd.to_datetime(te["ts"])).to_numpy()
    doc = {"K": k, "種": SEED, "訓練の日の局面": {str(i): int((dtr == i).sum()) for i in range(k)},
           "評価の日の局面": {str(i): int((dte == i).sum()) for i in range(k)},
           "重心（標準化）": np.round(km.cluster_centers_, 3).tolist(), "局面の列": list(REGIME_COLS)}
    return rtr, rte, doc


def regime_model(base, cols: list[str]):
    """局面ごとに base を学ぶ学習器（X に局面と日の列を持たせて渡す ＝ calibrate.fit の尻でも同じ形で呼べる）。"""
    def fn(Xtr, ytr, Xte, ctx):
        ytr = np.asarray(ytr, dtype=float)
        rtr, rte = Xtr[_R].to_numpy(), Xte[_R].to_numpy()
        out = np.asarray(base(Xtr[cols], ytr, Xte[cols], ctx), dtype=float)   # 全体の 1 本（足りない局面の埋め）
        for r in np.unique(rte):
            m = rtr == r
            if Xtr.loc[m, _D].nunique() < MIN_DAYS:
                continue
            t = rte == r
            out[t] = np.asarray(base(Xtr.loc[m, cols], ytr[m], Xte.loc[t, cols], ctx), dtype=float)
        return out
    return fn


def r1_regime(k: int, tr, te, feats, ctx):
    cols = [c for c in feats if c.startswith("own_") or c.startswith("LEAK_")]
    rtr, rte, rdoc = regimes(tr, te, k)
    sc = StandardScaler().fit(tr[cols])
    Xtr = pd.DataFrame(sc.transform(tr[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    Xtr[_R], Xte[_R] = rtr, rte
    Xtr[_D] = pd.to_datetime(tr["ts"]).to_numpy()
    Xte[_D] = pd.to_datetime(te["ts"]).to_numpy()
    ytr = tr["y"].to_numpy(dtype=float)
    fn = regime_model(ctx["model"], cols)
    cal = calibrate.fit(fn, Xtr, ytr, ctx)
    buy = np.asarray(cal.buy_pct(fn(Xtr, ytr, Xte, ctx)), dtype=float)
    days = Xtr.groupby(_R)[_D].nunique()
    doc = {**rdoc, "columns": cols, "全体で埋めた局面": [str(r) for r, n in days.items() if n < MIN_DAYS], **cal.doc}
    return buy, doc


for _k in KS:
    register("detector", name_of(_k))(lambda tr, te, feats, ctx, _k=_k: r1_regime(_k, tr, te, feats, ctx))
