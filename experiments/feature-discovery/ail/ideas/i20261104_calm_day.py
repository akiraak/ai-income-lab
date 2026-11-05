"""登録簿 `20261104-calm-day-hold`（参考なし・枠の外。記録 dev-loop.md）:
当てるものを方向から **翌日の断面の値動きの大きさ** に替え、荒れると読んだ日だけ全銘柄を現金にする検知器。

- ラベル（日ごとに 1 値）: その日の行の y（翌日の 1 日リターン）の **絶対値の断面中央値** が、訓練分割の日々の値の
  **70 分位**（事前固定・振らない）を超えれば 1。分位は fold の訓練分割の日だけで決める。
- 入力（日ごとに 1 行）: own_ 列の断面中央値 ＋ ex_ 列（全銘柄で同じ値 ＝ 断面中央値で取る。表の時点で 1 日遅れ）。
  ⚠ leak 対照の表の `LEAK_` 列は断面中央値と **絶対値の断面中央値** の 2 本にする（翌日の |y| を混ぜる ＝ 跳ねるかで配線を確かめる）。
- 学習器: ctx の model（config の `model = "ロジスティック回帰"` ＝ 下で登録する学習器）。較正は訓練分割の尻（`scale._fit_calibration`・
  ラベルの先は 1 日）。買い% ＝ 100 − 較正した荒れる確率（その日の全銘柄に同じ値を配る）。出口% は返さない（100 − 買い%）。
⚠ 登録するだけ ＝ この検知器 ／ 学習器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from ail.detectors.scale import _fit_calibration
from ail.registry import register

NAME = "V2 荒れる日を休む（翌日の断面の値動き・日ごと）"
LEARNER = "ロジスティック回帰"
QUANTILE = 0.70        # ⚠ 事前固定（登録簿）。結果を見て振らない


@register("model", LEARNER)
def logistic(Xtr, ytr, Xte, ctx):
    """2 値（ytr > 0 を 1）のロジスティック回帰。返すのは決定関数（較正は呼んだ側が Platt で掛ける）。"""
    up = np.asarray(ytr, dtype=float) > 0
    if up.all() or (~up).all():
        return np.zeros(len(Xte))
    m = LogisticRegression(C=float(ctx.get("logit_c", 1.0)), max_iter=1000).fit(Xtr, up)
    return m.decision_function(Xte)


def day_features(df: pd.DataFrame, feats) -> tuple[pd.DataFrame, list[str]]:
    """日ごとの入力（index ＝ 日）。own_ ／ ex_ は断面中央値・LEAK_ は断面中央値と絶対値の断面中央値。"""
    own = [c for c in feats if c.startswith("own_") or c.startswith("ex_")]
    leak = [c for c in feats if c.startswith("LEAK_")]
    ts = pd.to_datetime(df["ts"])
    g = df[own + leak].groupby(ts)
    out = g[own].median() if own else pd.DataFrame(index=g.size().index)
    for c in leak:
        out[c] = g[c].median()
        out[c + "|abs"] = df[c].abs().groupby(ts).median()
    return out, list(out.columns)


def rough_label(tr: pd.DataFrame) -> tuple[pd.Series, float]:
    """(日ごとの 0/1, 70 分位の値)。y の絶対値の断面中央値 > 訓練分割の日々の 70 分位。"""
    med = tr["y"].abs().groupby(pd.to_datetime(tr["ts"])).median()
    q = float(med.quantile(QUANTILE))
    return (med > q).astype(float), q


@register("detector", NAME)
def v2_calm_day(tr, te, feats, ctx):
    lab, q = rough_label(tr)
    Dtr, cols = day_features(tr, feats)
    Dte, _ = day_features(te, feats)
    Dtr = Dtr.loc[lab.index]
    ok = Dtr.notna().all(axis=1).to_numpy()
    Dtr, lab = Dtr.loc[ok], lab.loc[ok]
    sc = StandardScaler().fit(Dtr[cols])
    Xtr = pd.DataFrame(sc.transform(Dtr[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(Dte[cols].fillna(Dtr[cols].median())), columns=cols)
    ytr = np.where(lab.to_numpy() > 0, 1.0, -1.0)          # ⚠ 較正は「正の確率」＝ 荒れる確率
    model = ctx["model"]
    cal = _fit_calibration(model, Xtr, ytr, pd.Series(Dtr.index), 1, ctx)
    p_rough = np.asarray(cal.buy_pct(model(Xtr, ytr, Xte, ctx)), dtype=float)
    day_buy = pd.Series(100.0 - p_rough, index=Dte.index)
    buy = day_buy.reindex(pd.to_datetime(te["ts"])).to_numpy(dtype=float)
    doc = {"ラベル": f"翌日の |y| の断面中央値 > 訓練の {int(QUANTILE * 100)} 分位", "分位の値": round(q, 6),
           "訓練の日": int(len(lab)), "荒れる日の割合（訓練）": round(float(lab.mean()), 4),
           "columns": cols, "評価の日": int(len(Dte)),
           "休む日の割合（買い% < 50）": round(float((day_buy < 50).mean()), 4) if len(day_buy) else None, **cal.doc}
    return buy, doc
