"""Chronos-2 — 基盤モデルの zero-shot 予測を「学習器」の口で呼ぶ（[記録](../../../../docs/specs/experiments/exog-deep-models.md) §0）。

⚠ **学習しない。** 訓練の行は（`LEAK_` 列の配線の検査を除いて）読まず、検証の行の窓だけを与えて
先 10 営業日の分位点（21 本）を受け取り、⚠ **10 日先の点が 0（＝ いまの値）を上回る確率**を読む。
その確率のロジットが「予測値」で、検知器が Platt 較正で買い% にする（PatchTST と同じ口・rules.md 13-2）。

| 固定したもの（⚠ 振らない） | 値 | 出所 |
| --- | --- | --- |
| 重み | `amazon/chronos-2`（120M・Apache 2.0。取得した版の識別子は記録に残す） | [HF](https://huggingface.co/amazon/chronos-2)・[arXiv:2510.15821](https://arxiv.org/abs/2510.15821) |
| 文脈 | 60 点（`seq` 層の窓と同じ。時系列分類器・PatchTST と揃える） | 記録 §0-2 |
| 予測の長さ ／ 読む点 | 10 ／ 10 番目（`y_fwd_10` と同じ地平） | K5 |
| 分位点 → 確率 | 21 本の分位点（0.01〜0.99）から**文脈の最後の値（いまの値）を引き**、単調に直して線形補間で F(0) を読む。P(上) ＝ 1 − F(0)。端は 0.01 ／ 0.99 で止める | 記録 §0-2 |
| 共変量 | 過去だけ（`past_covariates`）。未来の共変量は無い | 検知器が並べる |

⚠ **`LEAK_` 列は「重みを訓練の行で最小二乗」**（学習しないモデルなので、PatchTST の「重み 0 で始まる線形の項」の代わり）。
本番の表には無いので、本番は分位点の読みだけ。

⚠ **窓の配列でしか呼べない。** 入力は 1 行 ＝ [目的 60 ｜ 共変量 K × 60（変数ごとに 60 が並ぶ）｜ `LEAK_` 列]。
検知器（`ail/detectors/exomodel.py`）が ctx に `seq_window`・`n_cov`・`cov_names` を入れて呼ぶ。

デバイスは `AIL_TORCH_DEVICE`（`deep.py` と同じ）。⚠ **重みは最初の呼び出しで 1 度だけ読む**（プロセスの中で使い回す）。
"""

from __future__ import annotations

import functools
import glob
import os
import time

import numpy as np

from ail.models.deep import torch_device
from ail.registry import register

MODEL_ID = "amazon/chronos-2"
SETUP = {"prediction_length": 10, "step": 10, "p_floor": 0.01, "p_ceil": 0.99}
# ⚠ **1 バッチに入れる系列の数**（目的 ＋ 共変量を数える。Chronos の `batch_size` の意味）。⚠ 結果には効かない（速さだけ）
BATCH_SERIES = 1024                # ⚠ 4,096 にすると 1 行あたり 2 倍以上遅くなった【実測 2026-09-27。35 行/秒 対 78 行/秒】
# ⚠ 1 度の `predict_quantiles` に渡す行の数（辞書の一覧が巨大にならないように）
CHUNK_ROWS = 8192
DOC_KEY = "_chronos2_fit"


@functools.lru_cache(maxsize=None)
def _pipeline(device: str):
    """⚠ **chronos はここで初めて読む**（既存の実行は読み込まない）。"""
    from chronos import Chronos2Pipeline
    return Chronos2Pipeline.from_pretrained(MODEL_ID, device_map=device)


def revision() -> str | None:
    """手元の重みの版（HF のキャッシュの `refs/main`）。記録に残すため。"""
    for root in (os.environ.get("HF_HOME"), os.path.expanduser("~/.cache/huggingface")):
        if not root:
            continue
        for ref in glob.glob(os.path.join(root, "hub", "models--amazon--chronos-2", "refs", "main")):
            with open(ref) as f:
                return f.read().strip()
    return None


def prob_up(q: np.ndarray, levels, floor: float = SETUP["p_floor"], ceil: float = SETUP["p_ceil"]) -> np.ndarray:
    """分位点（行 × 水準。ある 1 時点）→ P(値 > 0)。

    分位点を単調に直してから、0 の位置の累積確率 F(0) を水準のあいだで線形補間する。
    全部の分位点が 0 より上なら F(0) ＝ 0（P ＝ 1）、下なら 1（P ＝ 0）。⚠ **端は floor ／ ceil で止める**（ロジットが発散しないように）。
    """
    q = np.maximum.accumulate(np.asarray(q, dtype=float), axis=1)
    lv = np.asarray(levels, dtype=float)
    f0 = np.array([np.interp(0.0, row, lv, left=0.0, right=1.0) for row in q])
    return np.clip(1.0 - f0, floor, ceil)


def _inputs(X: np.ndarray, window: int, n_cov: int, names) -> list:
    """行 → Chronos の辞書（`target` と `past_covariates`）。⚠ 配列は写さず窓を切るだけ。"""
    tgt = X[:, :window]
    if not n_cov:
        return [{"target": tgt[i]} for i in range(len(X))]
    cov = X[:, window:window * (1 + n_cov)].reshape(len(X), n_cov, window)
    return [{"target": tgt[i], "past_covariates": {nm: cov[i, k] for k, nm in enumerate(names)}}
            for i in range(len(X))]


@register("model", "Chronos-2")
def chronos2(Xtr, ytr, Xte, ctx):
    import torch

    window = ctx.get("seq_window")
    if window is None or ctx.get("n_cov") is None:
        raise SystemExit("⚠ Chronos-2 は窓の配列でしか呼べない（検知器 `S2 ／ S3 Chronos-2（60日窓・先10日・…）` から呼ぶ）。"
                         "選別 × モデルの経路で `model = \"Chronos-2\"` にしていないか確かめる")
    window, n_cov, n_exog = int(window), int(ctx["n_cov"]), int(ctx.get("n_exog", 0))
    names = list(ctx.get("cov_names") or [f"c{k}" for k in range(n_cov)])
    width = window * (1 + n_cov)
    Xtr = np.asarray(Xtr, dtype=np.float32)
    Xte = np.asarray(Xte, dtype=np.float32)
    if Xtr.ndim != 2 or Xtr.shape[1] != width + n_exog or Xte.shape[1:] != Xtr.shape[1:] or len(names) != n_cov:
        raise SystemExit(f"⚠ Chronos-2 の入力の形が違う: 訓練 {Xtr.shape} ／ 検証 {Xte.shape}"
                         f"（目的 {window} ＋ 共変量 {n_cov} × {window} ＋ leak {n_exog} 列のはず）")
    dev = torch_device()
    pipe = _pipeline(str(dev))
    levels = list(pipe.quantiles)
    step = int(SETUP["step"])
    t0 = time.time()
    ps = []
    for s in range(0, len(Xte), CHUNK_ROWS):
        chunk = Xte[s:s + CHUNK_ROWS]
        if not len(chunk):
            continue
        with torch.no_grad():
            qs, _ = pipe.predict_quantiles(_inputs(chunk, window, n_cov, names),
                                           prediction_length=int(SETUP["prediction_length"]),
                                           quantile_levels=levels, batch_size=BATCH_SERIES)
        q = torch.stack([x[0, step - 1, :] for x in qs]).float().cpu().numpy()   # (行, 水準) ＝ 10 日先
        # ⚠ **「いまの値」（文脈の最後の点）を引いてから 0 を読む**（目的が相対価格〔最後 1.0〕でも道筋〔最後 0〕でも同じ読みになる。
        # 2026-09-27 の最初の実行はこれを忘れ、相対価格に 0 を当てて P(上) が全行 0.99 になった ＝ 捨てた）
        last = chunk[:, window - 1].astype(float)[:, None]
        ps.append(prob_up(q - last, levels))
    p = np.concatenate(ps) if ps else np.empty(0)
    pred = np.log(p / (1.0 - p))
    w = None
    if n_exog:
        # ⚠ **配線の検査だけ**（leak 対照）: `LEAK_` 列の重みを訓練の行で最小二乗し、標準化した尺度でロジットに足す
        ytr = np.asarray(ytr, dtype=float)
        L = Xtr[:, width:].astype(float)
        w, *_ = np.linalg.lstsq(L, ytr, rcond=None)
        sd = float(np.std(ytr)) or 1.0
        pred = pred + (Xte[:, width:].astype(float) @ w) / sd
    doc = {"device": str(dev), "model": MODEL_ID, "revision": revision(), "context": window,
           "prediction_length": int(SETUP["prediction_length"]), "step": step, "quantile_levels": levels,
           "p_floor": SETUP["p_floor"], "p_ceil": SETUP["p_ceil"], "n_cov": n_cov, "covariates": names,
           "予測した行": int(len(Xte)), "seconds": round(time.time() - t0, 1),
           "p_up_mean": None if not len(p) else round(float(p.mean()), 4),
           "p_up_sd": None if not len(p) else round(float(p.std()), 4),
           "leak_weight": None if w is None else [round(float(x), 6) for x in w]}
    ctx[DOC_KEY] = doc
    return pred
