"""時系列分類器 3 本（MiniRocket ・ Hydra ・ QUANT）を検知器として回す（[プラン](../../../../docs/plans/tsc-minirocket-hydra-quant.md)）。

⚠ **動かす軸は「入力の形」だけ。** これまでの手法は手で作った 35 列（own 層）を入力にしてきた。
ここでは ⚠ **過去 60 営業日の価格の道筋そのもの**から、論文どおりの変換で特徴を作る。

| 手法 | 変換（aeon 1.5.0 の既定のまま） | 出力の列 |
| --- | --- | ---: |
| T1 | MiniRocket（核 10,000） | 9,996 |
| T2 | Hydra（核 8 × 群 64） | 3,072 |
| T3 | QUANT（深さ 6・分位の割り 4） | 653 |

⚠ **頭は 3 本とも同じ**: 変換 → `StandardScaler` → **config の `model`（Ridge）** → **Platt 較正** → 買い%。
⚠ **論文の頭（RidgeClassifierCV・QUANT は ExtraTrees）とは違う**が、⚠ **同じ頭にしないと「変換の差」として読めない**。
⚠ **分類器の確率には替えない**（rules.md 13-2）。学習の対象は `y`（1 日先。モデルの軸と同じ）。

⚠ **fit は訓練分割の内側だけ**（rules.md 3 章 B）: 変換の fit（MiniRocket のバイアスの分位点）・標準化・
Ridge・較正のすべて。⚠ **検証の行は transform にしか使わない。**

⚠ **aeon はこの関数の中でだけ import する。** aeon 1.5.0 は `numba<0.64` を宣言するが、本体は
⚠ **numpy 2.4.2 を保つために numba 0.67.0 と組み合わせている**（プラン §0-2 の 3。⚠ **宣言どおりの環境と
出力がビット単位で一致することは 2026-09-15 に確かめた**）。⚠ **既存の実行は numba を読み込まない。**

⚠ **大きい配列は numpy の float32 のまま扱う**（MiniRocket × 11.5 万行 ＝ 4.6GB。DataFrame にしない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail.features import seq
from ail.models import calibrate
from ail.registry import register

# ⚠ **入力の形は事前固定**（プラン §0-1）。⚠ **窓ごとの z 正規化はしない**（振れ幅の情報を消さない）
INPUT = "累積の道筋（過去 60 営業日の対数価格 − 足 i の対数価格。最後の点は 0）"


def window_paths(df: pd.DataFrame, feats) -> np.ndarray:
    """表の窓の列 → aeon の形 (行, 1 チャンネル, 60)。⚠ **古い → 新しいの順**に並べる。

    列は r0（足 i）… r59（足 i−59）の新しい順なので ⚠ **逆に並べてから累積する**。
    道筋の j 番目は log c[i−59+j] − log c[i] で、⚠ **最後の点（j = 59）はちょうど 0**。
    """
    cols = seq.window_columns(feats)
    r = df[cols].to_numpy(dtype=np.float64)[:, ::-1]          # 古い → 新しい
    path = np.cumsum(r, axis=1)
    path -= path[:, -1:]
    return np.ascontiguousarray(path[:, None, :], dtype=np.float32)


def _minirocket(seed: int):
    from aeon.transformations.collection.convolution_based import MiniRocket
    return MiniRocket(random_state=seed)


def _hydra(seed: int):
    from aeon.transformations.collection.convolution_based import HydraTransformer
    return HydraTransformer(random_state=seed)


def _quant(seed: int):
    from aeon.transformations.collection.interval_based import QUANTTransformer
    return QUANTTransformer()                                   # ⚠ 乱数を引かない（区間は決定的）


def run_transform(label: str, make, tr: pd.DataFrame, te: pd.DataFrame, feats, ctx: dict):
    """変換 1 本の検知器。⚠ **戻り値は買い%（0〜100・検証の行数ぶん）と、記録に残す辞書**（rules.md 15-1）。"""
    model = ctx["model"]
    cols = seq.window_columns(feats)
    t = make(int(ctx.get("seed", 0)))
    ztr = np.asarray(t.fit_transform(window_paths(tr, feats)), dtype=np.float32)   # ⚠ fit は訓練だけ
    zte = np.asarray(t.transform(window_paths(te, feats)), dtype=np.float32)
    n_out = int(ztr.shape[1])
    # ⚠ **`LEAK_` の列は変換の出力の横に足す**（rules.md 13-10 の配線の検査）。
    # ⚠ **足し忘れると leak 対照が跳ねず、「配線が壊れている」と見分けがつかなくなる**（trend.scale_columns と同じ）
    leak = [c for c in feats if c.startswith("LEAK_")]
    if leak:
        ztr = np.hstack([ztr, tr[leak].to_numpy(dtype=np.float32)])
        zte = np.hstack([zte, te[leak].to_numpy(dtype=np.float32)])
    sc = StandardScaler().fit(ztr)                              # ⚠ 標準化も訓練の内側で fit
    ztr = sc.transform(ztr)
    zte = sc.transform(zte)
    ytr = tr["y"].to_numpy(dtype=float)
    cal = calibrate.fit(model, ztr, ytr, ctx)                   # ⚠ 訓練の尻の holdout で較正（13-2 の 2）
    pred = model(ztr, ytr, zte, ctx)
    doc = {"transform": label, "input": INPUT, "window": seq.WINDOW,
           "変換の出力の列": n_out, "columns": cols + leak,
           "訓練の行": int(len(tr)), "上がる割合": round(float((ytr > 0).mean()), 4), **cal.doc}
    return cal.buy_pct(pred), doc


@register("detector", "T1 MiniRocket（60日窓）")
def minirocket(tr, te, feats, ctx):
    return run_transform("MiniRocket", _minirocket, tr, te, feats, ctx)


@register("detector", "T2 Hydra（60日窓）")
def hydra(tr, te, feats, ctx):
    return run_transform("Hydra", _hydra, tr, te, feats, ctx)


@register("detector", "T3 QUANT（60日窓）")
def quant(tr, te, feats, ctx):
    return run_transform("QUANT", _quant, tr, te, feats, ctx)
