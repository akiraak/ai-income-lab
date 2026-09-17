"""下降トレンドの検知器 7 本（[プラン §2-3](../../../../docs/plans/archive/downtrend-detection.md)）。

⚠ **動かす軸は 1 つだけ — 予測の対象。** 既存の 24 試行は「明日の符号」を当てにいって
較正の傾き a ≈ 0（＝ 確率に情報が無い）だった（[threshold-trading.md §2](../../../../docs/specs/experiments/threshold-trading.md)）。
ここでは ⚠ **「これから W 営業日が上げか」** を対象にする。買い% = P(先 W 本の累積リターン > 0)。

⚠ **スケールの違いは窓 W だけ**（20 / 60 / 200）。3 つに同じ 6 列を与えるので、
⚠ **勝ち負けを「列の差」ではなく「スケールの差」として読める**（`ail/features/trend.py`）。

⚠ **数値は 1 つも手で置かない**（rules.md 14-9）。事前固定するのは構造だけ —
スケールの窓・列の選び方・出力が買い% 1 本であること。⚠ **閾値と係数は学習と config が決める。**

| 手法 | 中身 | 学習 |
| --- | --- | --- |
| D1 / D2 / D3 | そのスケールの 6 列 → モデル → Platt 較正 | ⚠ 訓練分割の内側 |
| D4 | D1・D2・D3 の買い% の**単純平均**（⚠ 重みを手で置かない） | 上の 3 本ぶん |
| C1 / C2 / C3 | ⚠ **終値が W 日移動平均より上なら買い%100、下なら 0** | ⚠ **しない**（公表された古典フィルタ） |
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail.features import labels, trend
from ail.models import calibrate
from ail.models.holdout import date_holdout
from ail.registry import register

SCALES = {"短期": 20, "中期": 60, "長期": 200}


def _fit_calibration(model, Xtr, ytr, ts, w: int, ctx: dict):
    """較正は ⚠ **訓練分割の尻（W 日ぶんパージ）** で行う（rules.md 13-2 の 2）。

    ⚠ **既定の行数 holdout では 200 営業日のラベルが頭と重なる。** 重なったまま較正すると
    ⚠ **買い% が実力より振れて見え、門の値も楽観側に外れる。**
    """
    split = date_holdout(ts, label_bars=w)
    if split is None:
        # ⚠ 訓練が薄くて尻を切れない。in-sample の過信ごと `source` に残す（13-2 の 2）
        pred = np.asarray(model(Xtr, ytr, Xtr, ctx), dtype=float)
        return calibrate.fit_from_predictions(pred, ytr, "train")
    head, hold = split
    pred = np.asarray(model(Xtr[head], ytr[head], Xtr[hold], ctx), dtype=float)
    return calibrate.fit_from_predictions(pred, ytr[hold], "holdout")


def scale_gate(w: int, tr: pd.DataFrame, te: pd.DataFrame, feats, ctx: dict):
    """スケール w の学習ゲート。⚠ **戻り値は買い%（0〜100）と、記録に残す辞書。**"""
    cols = trend.scale_columns(feats, w)
    if not cols:
        raise SystemExit(f"⚠ スケール {w} の列が表に無い。`feature_layers` に \"trend\" を入れる")
    target = f"{labels.SCALE_PREFIX}{w}"
    if target not in tr:
        raise SystemExit(f"⚠ {target} が表に無い。config の `label_scales` に {w} を入れる")
    # ⚠ **末尾はラベルが取れない行**（表の尻）。訓練からだけ落とす（検証の行はラベルを使わない）
    ok = tr[target].notna().to_numpy()
    tr_ok = tr.loc[ok]
    model = ctx["model"]
    sc = StandardScaler().fit(tr_ok[cols])           # ⚠ 標準化も訓練の内側で fit（rules.md 3 章 B）
    Xtr = pd.DataFrame(sc.transform(tr_ok[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    ytr = tr_ok[target].to_numpy(dtype=float)
    cal = _fit_calibration(model, Xtr, ytr, tr_ok["ts"], w, ctx)
    pred = model(Xtr, ytr, Xte, ctx)
    doc = {"scale": w, "target": target, "columns": cols,
           "訓練の行": int(len(tr_ok)), "上がる割合": round(float((ytr > 0).mean()), 4), **cal.doc}
    return cal.buy_pct(pred), doc


def classic_filter(w: int, tr: pd.DataFrame, te: pd.DataFrame, feats, ctx: dict):
    """古典フィルタ: ⚠ **移動平均の上なら持つ、下なら休む。** 学習しない（公表された規則そのまま）。

    ⚠ **買い% が 0 か 100 しか取らない**ので、θ の 3 水準で結果が同じになる。
    ⚠ **それでも 3 試行として数える**（rules.md 13-3 の 5・13-9 の 3。数え落とさない側に倒す）。
    """
    col = f"{trend.PREFIX}trend{w}_dist"
    if col not in te:
        raise SystemExit(f"⚠ {col} が表に無い。`feature_layers` に \"trend\" を入れる")
    buy = np.where(te[col].to_numpy(dtype=float) > 0.0, 100.0, 0.0)
    return buy, {"scale": w, "rule": f"{col} > 0", "source": "none（学習しない）", "columns": []}


def _register_scale(label: str, w: int) -> None:
    @register("detector", f"D{list(SCALES).index(label) + 1} {label}ゲート（{w}日・学習）")
    def _learned(tr, te, feats, ctx, _w=w):
        return scale_gate(_w, tr, te, feats, ctx)

    @register("detector", f"C{list(SCALES).index(label) + 1} {label} SMA{w} フィルタ")
    def _classic(tr, te, feats, ctx, _w=w):
        return classic_filter(_w, tr, te, feats, ctx)


for _label, _w in SCALES.items():
    _register_scale(_label, _w)


@register("detector", "D4 合成ゲート（3スケール平均）")
def composite(tr, te, feats, ctx):
    """⚠ **3 スケールの買い% の単純平均。** 重みは手で置かない（rules.md 14-9）。

    ⚠ **14-6 (a) により、これは「最良の単独スケール」に勝って初めて意味がある。**
    """
    parts, docs = [], {}
    for label, w in SCALES.items():
        bp, doc = scale_gate(w, tr, te, feats, ctx)
        parts.append(np.asarray(bp, dtype=float))
        docs[f"{label}{w}"] = doc
    buy = np.mean(np.vstack(parts), axis=0)
    cols = sorted({c for d in docs.values() for c in d["columns"]})
    return buy, {"scales": list(SCALES.values()), "columns": cols, "内訳": docs,
                 "source": "mean（3 スケールの買い% の単純平均）"}
