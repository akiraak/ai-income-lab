"""系列モデル（PatchTST）を検知器として回す（[プラン](../../../../docs/plans/patchtst-threshold.md)）。

⚠ **入力は時系列分類器（`tsc.py`）と同じ窓**（過去 60 営業日の累積の道筋。`tsc.window_paths`）。
⚠ **違うのはモデルだけ**: 固定の変換 → Ridge ではなく、⚠ **窓から表現を学習して次の 1 点（＝ `y`）を予測する。**

    窓（道筋 60 点）→ PatchTST（config の `model`）→ Platt 較正 → 買い%

⚠ **Ridge は挟まない**（PatchTST は頭まで学習するモデル。挟むと別のモデルになる）。
⚠ **分類器の確率には替えない**（rules.md 13-2）。⚠ **fit は訓練分割の内側だけ**（3 章 B）。

⚠ **`LEAK_` の列は窓の後ろに並べて渡し、モデルが「重み 0 で始まる線形の項」として使う**（rules.md 13-10。
PatchTST は系列ごとに独立なので、2 本目の系列として入れても 1 本目の予測に効かない — プラン §0-2 の 6）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.detectors import tsc
from ail.features import seq
from ail.models import calibrate, patchtst
from ail.registry import register

NAME = "S1 PatchTST（60日窓）"


def _inputs(df: pd.DataFrame, feats, leak: list[str]) -> np.ndarray:
    """(行, 窓 60 ＋ `LEAK_` 列) の float32。窓は古い → 新しい（最後の点は 0）。"""
    w = tsc.window_paths(df, feats)[:, 0, :]
    if leak:
        w = np.hstack([w, df[leak].to_numpy(dtype=np.float32)])
    return np.ascontiguousarray(w, dtype=np.float32)


@register("detector", NAME)
def patchtst_detector(tr: pd.DataFrame, te: pd.DataFrame, feats, ctx: dict):
    model = ctx["model"]
    if getattr(model, "__ail_name__", None) != "PatchTST":
        raise SystemExit(f"⚠ 検知器 {NAME} は config の `model = \"PatchTST\"` と組にする"
                         f"（いまは {getattr(model, '__ail_name__', model)!r}）")
    cols = seq.window_columns(feats)
    leak = [c for c in feats if c.startswith("LEAK_")]
    Xtr, Xte = _inputs(tr, feats, leak), _inputs(te, feats, leak)
    ytr = tr["y"].to_numpy(dtype=float)
    # ⚠ **ctx は写しを渡す**（窓の長さと学習の記録を書き込むので、呼び元の ctx を汚さない）
    c = {**ctx, "seq_window": seq.WINDOW, "n_exog": len(leak)}
    cal = calibrate.fit(model, Xtr, ytr, c)                   # ⚠ 訓練の尻の holdout で較正（13-2 の 2）
    fit_cal = c.pop(patchtst.DOC_KEY, None)
    pred = model(Xtr, ytr, Xte, c)
    fit_main = c.pop(patchtst.DOC_KEY, None)
    doc = {"model": "PatchTST", "input": tsc.INPUT, "window": seq.WINDOW, "columns": cols + leak,
           "訓練の行": int(len(tr)), "上がる割合": round(float((ytr > 0).mean()), 4),
           "学習（較正用）": fit_cal, "学習（本番）": fit_main, **cal.doc}
    return cal.buy_pct(pred), doc
