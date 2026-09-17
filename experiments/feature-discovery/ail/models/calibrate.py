"""回帰の予測値を「買い% 0〜100」にする Platt 較正（rules.md 13-2）。

買い% = 100 · σ(a·pred + b) を **P(y > 0) の較正**として fit する。⚠ **売り% = 100 − 買い%**。
⚠ **分類モデル（predict_proba）には替えない**（モデルの軸が動くと検証方式の変更と交絡する）。

⚠ **fit は訓練分割の内側だけ**（rules.md 3 章 B）。木系は in-sample 予測が過信になるので
`tail_holdout` の末尾（モデルは頭で fit）で較正する。holdout が取れないほど訓練が薄いとき
（(B) 銘柄別の最初の fold）は訓練予測で代用し、⚠ **どちらで fit したかを `source` に残す**。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ail.models.holdout import tail_holdout

# ⚠ **較正の版。台帳の鍵に入る**（rules.md 13-2 の 6・13-9 の 5）。
# ⚠ **これが無い実行は「旧」＝ 2026-09-12 より前の、数値解が止まっていた較正である。**
# ⚠ **旧行は再計算しない・消さない。** 回し直した分は別の鍵になり、新しい試行として数える
VERSION = "std"


@dataclass(frozen=True)
class Calibration:
    a: float
    b: float
    source: str          # "holdout" ／ "train"（訓練が薄く代用）／ "constant"（符号が片側だけ）

    def buy_pct(self, pred) -> np.ndarray:
        """予測値 → 買い%（0〜100）。transform だけで、ここでは何も学ばない。"""
        z = self.a * np.asarray(pred, dtype=float) + self.b
        return 100.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))

    @property
    def doc(self) -> dict:
        """`runs/<実行>/fitted/` に残す形。⚠ **次の実行では読み込まない**（13-2 の 3）。"""
        return {"a": self.a, "b": self.b, "source": self.source}


def fit(model_fn, Xtr, ytr, ctx: dict, split: str = "train") -> Calibration:
    """訓練分割の内側で較正を fit する。

    ⚠ **`split` は「訓練分割を渡している」ことの宣言**で、"train" 以外は受けない
    （検証の行で fit すると較正がそのまま先読みになる）。
    """
    if split != "train":
        raise ValueError(f"較正の fit は訓練分割の内側だけ（rules.md 13-2 の 2）。split={split!r} は受けない")
    ytr = np.asarray(ytr, dtype=float)
    (Xh, yh), holdout = tail_holdout(Xtr, ytr)
    if holdout is not None:
        Xv, yv = holdout
        pred, target, source = np.asarray(model_fn(Xh, yh, Xv, ctx), float), yv, "holdout"
    else:
        # ⚠ 訓練が薄い（(B) の最初の fold など）。in-sample の過信ごと記録に残す
        pred, target, source = np.asarray(model_fn(Xtr, ytr, Xtr, ctx), float), ytr, "train"
    return fit_from_predictions(pred, target, source)


def fit_from_predictions(pred: np.ndarray, target: np.ndarray, source: str) -> Calibration:
    """予測と実現値の対から較正を作る（前置きの門も同じ計算を使う。rules.md 14-5）。"""
    pred = np.asarray(pred, dtype=float)
    up = np.asarray(target, dtype=float) > 0
    if up.all() or (~up).all() or np.std(pred) == 0:
        # 片側しか無い・予測が定数 → 傾きは学べない。基準率の定数確率に落とす
        p = (up.sum() + 1.0) / (len(up) + 2.0)               # Laplace 平滑化
        return Calibration(a=0.0, b=float(np.log(p / (1.0 - p))), source="constant")
    a, b = _platt(pred, up)
    return Calibration(a=float(a), b=float(b), source=source)


def _platt(pred: np.ndarray, up: np.ndarray) -> tuple[float, float]:
    """ロジスティック回帰 1 変数（正則化はほぼ切る＝ 素の Platt）。決定的。

    ⚠ **予測を標準化してから解き、係数を元のスケールへ戻す**（2026-09-12 の処置）。
    ⚠ **モデルも標本も目的関数も変えていない。変えたのは解き方だけである**（rules.md 13-2 の 6）。

    ⚠ **なぜ要るか**: `lbfgs` の `tol`（既定 1e-4）は L-BFGS-B の `gtol` に渡り、
    ⚠ **平均対数損失の勾配**で収束を判定する。傾きの勾配は `≈ cov(y, pred)` で
    ⚠ **予測のスケールにそのまま比例する**ので、1 日リターンの尺度（σ ≈ 0.0017）では
    ⚠ **開始点の勾配 3e-5 が `tol` を下回り、1 反復で「収束」と判定されて a が 0 のまま出てくる。**
    ⚠ **実測**: 生のまま 1 反復・a = 0.00013 ／ 標準化して 2 反復・a = 46.54（同じ最尤解）。
    切り分けは [buy-pct-width-collapse.md](../../../../docs/specs/experiments/buy-pct-width-collapse.md)。
    """
    from sklearn.linear_model import LogisticRegression

    mu, sd = float(np.mean(pred)), float(np.std(pred))
    if sd == 0.0:                                        # 呼び元が先に弾いているが念のため
        return 0.0, 0.0
    m = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
    m.fit(((pred - mu) / sd).reshape(-1, 1), up.astype(int))
    a_z, b_z = float(m.coef_[0, 0]), float(m.intercept_[0])
    # ⚠ **元のスケールへ戻す。** σ(a·pred + b) が標準化した式とちょうど一致する
    return a_z / sd, b_z - a_z * mu / sd
