"""登録簿 `20261111-sector-pair-revert`（参考なし・枠の外。記録 dev-loop.md）:
当てるものを「1 銘柄の翌日の方向」から **「同じセクターの 2 銘柄の開きが先 5 日で縮むか」** に替え、出遅れた側だけを買う検知器 PR1。

- 組: 会社ごとに、同じセクター ETF（`config/universe/us63.toml` の `[sector_of]`）に属する会社のうち、**訓練分割の日次リターン
  （`own_ret_1`）の相関が最も高い相手 1 本**（事前固定。同じセクターに相手のいない会社と ETF は対象外 ＝ 買い% 50・出口% 100）。
- 開き d ＝ log(自分の終値) − log(相手の終値)。ずれ ＝ d − d の直近 60 日の平均。z ＝ ずれ ÷ 直近 w 日の標準偏差（w ＝ 20 ／ 60 の 2 列）。
  ⚠ どれも その日までの終値だけ（評価の行は訓練の終値から続けて数える）。
- ラベル（訓練分割の中だけ）＝ 先 5 日でずれが縮んだか: |ずれ_t| − |d_{t+5} − 平均_t| > 0。⚠ 訓練の最後の 5 日は先が無いので学習に使わない。
- ⚠ 学ぶ行 ・ 当てる行は **自分が出遅れている行だけ**（60 日の z < 0。回す前に決めた読み ＝ 買うのは出遅れた側だけなので、
  線形の学習器が「開きが大きいほど」を 1 つの向きで学べる形にした）。
- 説明変数 ＝ z20 ・ z60 ＋ own 35 列。学習器 ＝ ctx の model（Ridge）に ±1 のラベルを当てさせ、既存の較正 `calibrate.fit` で確率にする。
- 買い% ＝ 出遅れている行は縮む確率・ほかは 50。⚠ 出口% ＝ 出遅れていない行は 100（開きが閉じた ＝ 降りる）・出遅れている行は 100 − 買い%
  （回す前に決めた読み ＝ 50 のままだと売る線を超えず、1 度買うと降りない）。
- leak 対照: LEAK_ 列 ＋ 先 5 日の開きの変化（d_{t+5} − d_t・評価の行にも先の値を入れる）を説明変数に足す。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import os
import tomllib

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail.models import calibrate
from ail.registry import register

FWD = 5                # ⚠ 事前固定（登録簿）
WINDOWS = (20, 60)     # ⚠ 事前固定（登録簿）
BASE = 60              # ずれの土台 ＝ 直近 60 日の平均
MIN_CORR_DAYS = 120
MIN_ROWS = 500
UNIVERSE = os.path.join(os.path.dirname(__file__), "..", "..", "config", "universe", "us63.toml")
NAME = f"PR1 同じセクターの相手との開きが縮むかを当てて出遅れを買う（先{FWD}日）"
Z20, Z60, LEAK5 = "__z20", "__z60", "__leak_d5"


def sector_of() -> dict[str, str]:
    with open(UNIVERSE, "rb") as f:
        return {str(k): str(v) for k, v in tomllib.load(f)["sector_of"].items()}


def partners(tr: pd.DataFrame, sectors: dict[str, str]) -> dict[str, str]:
    """会社 → 同じセクターで訓練分割の日次リターンの相関が最も高い相手（いなければ載せない。同じ相関は名前の順）。"""
    wide = tr.pivot_table(index="ts", columns="symbol", values="own_ret_1", aggfunc="first")
    corr = wide.corr(min_periods=MIN_CORR_DAYS)
    out: dict[str, str] = {}
    for s in sorted(wide.columns):
        cands = sorted(c for c in wide.columns if c != s and sectors.get(str(c)) and sectors.get(str(c)) == sectors.get(str(s)))
        best, bv = None, -np.inf
        for c in cands:
            v = corr.at[s, c]
            if np.isfinite(v) and v > bv:
                best, bv = c, v
        if best is not None:
            out[str(s)] = str(best)
    return out


def spreads(close: pd.DataFrame, pairs: dict[str, str]) -> dict[str, pd.DataFrame]:
    """終値（日 × 銘柄）→ {"d", "mean", "z20", "z60"}（日 × 組のある会社）。どれも その日までの値だけ。"""
    lc = np.log(close.where(close > 0))
    syms = [s for s in pairs if s in lc.columns and pairs[s] in lc.columns]
    d = pd.DataFrame({s: lc[s] - lc[pairs[s]] for s in syms}, index=lc.index)
    mean = d.rolling(BASE, min_periods=BASE).mean()
    dev = d - mean
    out = {"d": d, "mean": mean}
    for w in WINDOWS:
        sd = d.rolling(w, min_periods=w).std()
        out[f"z{w}"] = dev / sd.where(sd > 0)
    return out


def _lookup(frame: pd.DataFrame, ts, symbol) -> np.ndarray:
    """(日, 銘柄) の行ごとに frame の値（無ければ NaN）。"""
    ri = frame.index.get_indexer(pd.to_datetime(ts))
    ci = frame.columns.get_indexer(np.asarray(symbol).astype(str))
    v = np.full(len(ri), np.nan)
    ok = (ri >= 0) & (ci >= 0)
    v[ok] = frame.to_numpy(dtype=float)[ri[ok], ci[ok]]
    return v


def pr1(tr, te, feats, ctx):
    cols = [c for c in feats if c.startswith(("own_", "LEAK_"))]
    leak = any(c.startswith("LEAK_") for c in feats)
    pairs = partners(tr, sector_of())
    ttr, tte = pd.to_datetime(tr["ts"]), pd.to_datetime(te["ts"])
    both = pd.concat([tr[["ts", "symbol", "close"]], te[["ts", "symbol", "close"]]], ignore_index=True)
    both["ts"] = pd.to_datetime(both["ts"])
    close = both.pivot_table(index="ts", columns="symbol", values="close", aggfunc="first").sort_index()
    close.columns = close.columns.astype(str)
    sp = spreads(close, pairs)
    # ラベルは訓練分割の終値だけで作る（先 5 日が訓練の外に出る日は NaN ＝ 使わない）
    d_tr = sp["d"].loc[sp["d"].index.isin(ttr)]
    dev_tr = d_tr - sp["mean"].loc[d_tr.index]
    shrink = dev_tr.abs() - (d_tr.shift(-FWD) - sp["mean"].loc[d_tr.index]).abs()
    z = {c: (_lookup(sp[k], ttr, tr["symbol"]), _lookup(sp[k], tte, te["symbol"])) for c, k in ((Z20, "z20"), (Z60, "z60"))}
    lab_tr = _lookup(shrink, ttr, tr["symbol"])
    xcols = cols + [Z20, Z60]
    Ftr = tr[cols].reset_index(drop=True).assign(**{Z20: z[Z20][0], Z60: z[Z60][0]})
    Fte = te[cols].reset_index(drop=True).assign(**{Z20: z[Z20][1], Z60: z[Z60][1]})
    if leak:
        d5 = sp["d"].shift(-FWD) - sp["d"]                       # ⚠ leak 対照だけ: 評価の行にも先の値
        Ftr[LEAK5], Fte[LEAK5] = np.nan_to_num(_lookup(d5, ttr, tr["symbol"])), np.nan_to_num(_lookup(d5, tte, te["symbol"]))
        xcols = xcols + [LEAK5]
    lag_tr = np.isfinite(Ftr[Z20].to_numpy()) & (Ftr[Z60].to_numpy() < 0) & np.isfinite(lab_tr)
    lag_te = np.isfinite(Fte[Z20].to_numpy()) & (Fte[Z60].to_numpy() < 0)
    buy, ex = np.full(len(te), 50.0), np.full(len(te), 100.0)
    doc = {"columns": xcols, "組": pairs, "組のある会社": len(pairs), "先の日数": FWD, "訓練の出遅れの行": int(lag_tr.sum()),
           "評価の出遅れの行の割合": round(float(lag_te.mean()), 4) if len(lag_te) else 0.0}
    if lag_tr.sum() < MIN_ROWS or not lag_te.any():
        return buy, ex, {**doc, "学習": f"なし（訓練の出遅れの行 {int(lag_tr.sum())}・下限 {MIN_ROWS}）"}
    y2 = np.where(lab_tr[lag_tr] > 0, 1.0, -1.0)
    sc = StandardScaler().fit(Ftr.loc[lag_tr, xcols])
    Xtr = pd.DataFrame(sc.transform(Ftr.loc[lag_tr, xcols]), columns=xcols)
    Xte = pd.DataFrame(sc.transform(Fte.loc[lag_te, xcols]), columns=xcols)
    model = ctx["model"]
    cal = calibrate.fit(model, Xtr, y2, ctx)
    p = np.asarray(cal.buy_pct(model(Xtr, y2, Xte, ctx)), dtype=float)
    buy[lag_te] = p
    ex[lag_te] = 100.0 - p
    doc.update({"学習": "あり", "訓練で縮んだ割合": round(float((y2 > 0).mean()), 4),
                "縮む確率の 5〜95%": [round(float(np.quantile(p, q)), 2) for q in (0.05, 0.5, 0.95)], **cal.doc})
    return buy, ex, doc


register("detector", NAME)(pr1)
