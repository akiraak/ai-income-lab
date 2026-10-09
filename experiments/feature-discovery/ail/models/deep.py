"""深層（表形式の MLP）。⚠ **「深層にすれば出るのか」への最短の答え**（[プラン §1](../../../../docs/plans/archive/gpu-models.md)）。

⚠ **入力は他モデルと同じ行**（`cli/run.py` が標準化した特徴量ベクトル）。
系列モデル（LSTM 等）は⚠ **入力の表現ごと変える必要がある**ので置かない（プランで見送り）。

デバイスは環境変数 `AIL_TORCH_DEVICE`（auto / cpu / cuda）。auto は
⚠ **空き VRAM 4GB 未満なら CPU に落とす**（llama-server と取り合わないため。プラン §4）。

⚠ **ハイパーパラメータは評価期間の結果で動かさない**（rules.md 11 章 規約 4・13-6 規約 3）。
⚠ **層と幅・Dropout・学習率は ctx から読む**（`mlp_hidden`・`mlp_dropout`・`mlp_lr`。2026-10-09）。
渡すのは `MLP（内側選抜）`（`tuned.py`。訓練分割の尻で選ぶ ＝ rules.md 14-12）だけで、
⚠ **既定の値はいままでの定数と同じ**（(64, 32)・0.2・1e-3）＝ 項目の無い config は 1 ビットも変わらない
（層の組み立ての順も同じなので、乱数の消費も同じ ＝ 重みの初期値も同じ）。
"""

from __future__ import annotations

import os

import numpy as np

from ail.registry import register
from ail.models.holdout import tail_holdout


def _free_vram_mib() -> int | None:
    """⚠ **CUDA コンテキストを作らずに**空き VRAM を読む（nvidia-smi 経由）。

    ⚠ **`torch.cuda.mem_get_info()` は問い合わせだけで約 350MiB のコンテキストを GPU に置く**
    （2026-09-09 に踏んだ。CPU に落ちる判定のためだけに llama-server の空きを削っていた）。
    """
    import subprocess

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        return int(out[0]) if out else None
    except Exception:
        return None


def torch_device():
    """auto は「空き VRAM が 4GB 以上」のときだけ GPU。⚠ **判定では GPU に触れない**（上）。"""
    import torch

    want = os.environ.get("AIL_TORCH_DEVICE", "auto")
    if want in ("cpu", "cuda"):
        return torch.device(want)
    free = _free_vram_mib()
    if free is not None and free >= 4096 and torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def _seed_all(seed: int) -> None:
    import torch

    torch.manual_seed(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


@register("model", "MLP")
def mlp(Xtr, ytr, Xte, ctx):
    import torch
    from torch import nn

    seed = int(ctx.get("seed", 0))
    max_epochs = int(ctx.get("mlp_epochs", 200))
    hidden = tuple(int(h) for h in ctx.get("mlp_hidden", (64, 32)))
    dropout = float(ctx.get("mlp_dropout", 0.2))
    lr = float(ctx.get("mlp_lr", 1e-3))
    _seed_all(seed)
    dev = torch_device()

    (Xf, yf), holdout = tail_holdout(Xtr, ytr)
    # ⚠ **y はスケールが小さい**（日次の対数リターン ≒ 0.02）。学習だけ標準化し、予測で戻す。
    # 符号もICも定数倍では変わらないが、最適化が回りやすくなる
    ysd = float(np.std(yf)) or 1.0

    def t(a, shape=None):
        v = torch.as_tensor(np.asarray(a, dtype=np.float32), device=dev)
        return v.reshape(shape) if shape else v

    Xf_t, yf_t = t(Xf), t(yf / ysd, (-1, 1))
    layers: list = []
    n_in = Xf_t.shape[1]
    for width in hidden:                      # ⚠ 既定 (64, 32) ＝ いままでの Linear → ReLU → Dropout × 2 と同じ並び
        layers += [nn.Linear(n_in, width), nn.ReLU(), nn.Dropout(dropout)]
        n_in = width
    model = nn.Sequential(*layers, nn.Linear(n_in, 1)).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()
    g = torch.Generator(device="cpu").manual_seed(seed)
    batch = 4096

    val = None
    if holdout is not None:
        Xv, yv = holdout
        val = (t(Xv), t(yv / ysd, (-1, 1)))
    best, best_state, patience = float("inf"), None, 0
    for _epoch in range(max_epochs):
        model.train()
        for i in torch.randperm(len(Xf_t), generator=g).split(batch):
            opt.zero_grad()
            loss = loss_fn(model(Xf_t[i.to(dev)]), yf_t[i.to(dev)])
            loss.backward()
            opt.step()
        if val is None:
            continue
        model.eval()
        with torch.no_grad():
            v = float(loss_fn(model(val[0]), val[1]))
        if v < best - 1e-6:
            best, patience = v, 0
            best_state = {k: w.detach().clone() for k, w in model.state_dict().items()}
        else:
            patience += 1
            if patience >= 10:          # 10 epoch 良くならなければ打ち切り
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        p = model(t(Xte)).reshape(-1).cpu().numpy()
    return p * ysd
