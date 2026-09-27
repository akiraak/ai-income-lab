"""先 W 営業日を当てにいく検知器（[記録](../../../../docs/specs/experiments/forward10-target.md) §0）。

⚠ **動かす軸は 1 つだけ — 学習の対象。** 実売買の 3 人（T1〜T3）は「明日の符号」（`y`）を学ぶ。
ここでは ⚠ **同じ表の説明変数を全部そのまま**使い、⚠ **先 W 営業日の累積対数リターン `y_fwd_W`** を学ぶ。
買い% = P(y_fwd_W > 0) の Platt 較正。損益の対象（シミュレータ）は 1 日の `y` のまま（rules.md 15-3）。

⚠ **`scale.py`（D1〜D4）との違いは見る数字だけ**: D は `trend` 層の 6 列（そのスケールのものだけ）、
ここは ⚠ **表の説明変数 全部**（`own` 35 ／ `ownex` 124 ／ `ownseq` 95 ＝ 「いまの 3 本が見ている数字」）。
⚠ **既存の `SCALES`・D1〜D4・C1〜C3 は 1 文字も変えない**（登録名は記録の識別項目 ＝ rules.md 10-1）。

⚠ **数値は 1 つも手で置かない**（rules.md 14-9）: 事前固定するのは 窓 W・列 ＝ 全部・出力が買い% 1 本 だけ。
学習器は config の `model`（Ridge ／ LightGBM）を `ctx["model"]` で受け取る（T1〜T3 の検知器と同じ形）。

⚠ **fit は訓練分割の内側だけ**（rules.md 3 章 B）: 標準化・学習器・較正のすべて。
⚠ **較正の holdout は日付で切り、W 営業日ぶん（暦 1.5 倍）パージする**（15-5。`scale._fit_calibration` を共用）。
⚠ **`LEAK_` の列は `feats` に入っていればそのまま説明変数に入る**（13-10 の配線の検査。`LEAK_fwd_W` で跳ねる）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail.detectors.scale import _fit_calibration
from ail.features import labels
from ail.registry import register

# ⚠ **窓は事前固定**（K4）。5 日（cGAN）と 20 日（D1）のあいだの空白。増やすときは新しい登録名で足す
WINDOW = 10


def forward_gate(w: int, tr: pd.DataFrame, te: pd.DataFrame, feats, ctx: dict):
    """先 w 営業日のゲート。⚠ **戻り値は買い%（0〜100・検証の行数ぶん）と、記録に残す辞書**（rules.md 15-1）。"""
    target = f"{labels.SCALE_PREFIX}{w}"
    if target not in tr:
        raise SystemExit(f"⚠ {target} が表に無い。config の `label_scales` に {w} を入れて `cli.build` し直す")
    # ⚠ **説明変数は表の全部**（`feats` は呼ぶ側が META を外して渡す ＝ `y_fwd_` は接頭辞ごと外れている）
    cols = [c for c in feats if not c.startswith(labels.SCALE_PREFIX)]
    # ⚠ **末尾はラベルが取れない行**（表の尻）。訓練からだけ落とす（検証の行はラベルを使わない）
    ok = tr[target].notna().to_numpy()
    tr_ok = tr.loc[ok]
    model = ctx["model"]
    sc = StandardScaler().fit(tr_ok[cols])           # ⚠ 標準化も訓練の内側で fit（rules.md 3 章 B）
    Xtr = pd.DataFrame(sc.transform(tr_ok[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    ytr = tr_ok[target].to_numpy(dtype=float)
    cal = _fit_calibration(model, Xtr, ytr, tr_ok["ts"], w, ctx)   # ⚠ w 営業日ぶんパージした holdout で較正
    pred = model(Xtr, ytr, Xte, ctx)
    doc = {"scale": w, "target": target, "columns": cols,
           "訓練の行": int(len(tr_ok)), "上がる割合": round(float((ytr > 0).mean()), 4), **cal.doc}
    return cal.buy_pct(pred), doc


@register("detector", f"H1 先{WINDOW}日ゲート（全列・学習）")
def h1_forward10(tr, te, feats, ctx):
    return forward_gate(WINDOW, tr, te, feats, ctx)
