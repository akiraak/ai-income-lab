"""GAN 増強（WGAN-GP）。⚠ **GAN は予測器ではなく、学習データの増強に使う**（[プラン §2](../../../../docs/plans/archive/gpu-models.md)）。

⚠ **標本から学ぶ変換なので、fold ごとに訓練分割の内側で fit する**（rules.md 3 章 B。
キャッシュしない・毎回学習し直す）。(X, y) の**同時分布**を学び、合成行を**訓練にだけ**足す。
⚠ **検証は常に実データのみ**（このモデル関数は Xte を予測にしか使わない）。

⚠ **合成行は実データの「前」に置く。** 基底モデルの早期打ち切り（`tail_holdout`）は
末尾を検証に使うので、⚠ **後ろに足すと合成行で早期打ち切りしてしまう。**

増強の対照は「増強なし」＝ 同じ基底モデルの既存の実行（追加の実行は要らない）。
合成の量は実データと同数（α = 100%）に固定（⚠ **α を振ると水準の数だけ n_trials が増える**）。
重みは保存せず、⚠ **種＋コード版＋入力の指紋で再現する**（既存の配線が StandardScaler を
保存しないのと同じ扱い。プラン §2 の規約 5）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.registry import register
from ail.models.deep import torch_device, _seed_all


def wgan_gp_sample(Xtr, ytr, ctx) -> tuple[np.ndarray, np.ndarray]:
    """訓練分割の (X, y) に WGAN-GP を fit し、同数の合成行を返す。"""
    import torch
    from torch import nn

    seed = int(ctx.get("seed", 0))
    epochs = int(ctx.get("gan_epochs", 300))
    latent = 32
    _seed_all(seed + 1)                 # ⚠ 基底モデルと同じ種にしない（乱数列の共有を避ける）
    dev = torch_device()

    X = np.asarray(Xtr, dtype=np.float32)
    ysd = float(np.std(ytr)) or 1.0
    real = np.column_stack([X, np.asarray(ytr, dtype=np.float32) / ysd])
    d = real.shape[1]
    data = torch.as_tensor(real, device=dev)

    def block(i, o):
        return [nn.Linear(i, o), nn.LeakyReLU(0.2)]

    gen = nn.Sequential(*block(latent, 128), *block(128, 128), *block(128, 128),
                        nn.Linear(128, d)).to(dev)
    critic = nn.Sequential(*block(d, 128), *block(128, 128), *block(128, 128),
                           nn.Linear(128, 1)).to(dev)
    opt_g = torch.optim.Adam(gen.parameters(), lr=1e-4, betas=(0.5, 0.9))
    opt_c = torch.optim.Adam(critic.parameters(), lr=1e-4, betas=(0.5, 0.9))
    g = torch.Generator(device="cpu").manual_seed(seed + 1)
    # ⚠ **既定は 1024（2026-09-09 に事前固定した形）。`ctx["gan_batch"]` でだけ動く。**
    # ⚠ **batch を変えると最適化の軌跡が変わる ＝ 別の手法である**（rules.md 13-6 の 3・14-9）。
    # ⚠ **だから別のモデル名で登録し、台帳の鍵を割る**（下の登録。同じ鍵にまとめると
    # ⚠ **違う手法の数字が「再現の幅」に化ける**。rules.md 13-9 の 5）
    batch = min(int(ctx.get("gan_batch", 1024)), len(data))

    def gradient_penalty(real_b, fake_b):
        eps = torch.rand(len(real_b), 1, device=dev)
        mix = (eps * real_b + (1 - eps) * fake_b).requires_grad_(True)
        score = critic(mix)
        grad = torch.autograd.grad(score.sum(), mix, create_graph=True)[0]
        return ((grad.norm(2, dim=1) - 1) ** 2).mean()

    for _epoch in range(epochs):
        for i in torch.randperm(len(data), generator=g).split(batch):
            real_b = data[i.to(dev)]
            # critic を 5 回、生成器を 1 回（WGAN-GP の通例）
            for _ in range(5):
                z = torch.randn(len(real_b), latent, device=dev)
                fake_b = gen(z).detach()
                loss_c = critic(fake_b).mean() - critic(real_b).mean() \
                    + 10.0 * gradient_penalty(real_b, fake_b)
                opt_c.zero_grad()
                loss_c.backward()
                opt_c.step()
            z = torch.randn(len(real_b), latent, device=dev)
            loss_g = -critic(gen(z)).mean()
            opt_g.zero_grad()
            loss_g.backward()
            opt_g.step()

    gen.eval()
    with torch.no_grad():
        z = torch.randn(len(data), latent, generator=g).to(dev)   # 乱数は CPU 側で（決定性）
        synth = gen(z).cpu().numpy()
    return synth[:, :-1], synth[:, -1] * ysd


def _augmented(base_name: str, batch: int | None = None):
    def fn(Xtr, ytr, Xte, ctx):
        from ail import registry

        if batch is not None:
            ctx = {**ctx, "gan_batch": batch}       # ⚠ 呼び元の ctx は書き換えない
        Xs, ys = wgan_gp_sample(Xtr, ytr, ctx)
        cols = list(Xtr.columns) if hasattr(Xtr, "columns") else None
        if cols:
            Xa = pd.concat([pd.DataFrame(Xs, columns=cols), Xtr.reset_index(drop=True)],
                           ignore_index=True)      # ⚠ 合成行は前（冒頭の注記）
        else:
            Xa = np.vstack([Xs, Xtr])
        ya = np.concatenate([ys, np.asarray(ytr)])
        return registry.resolve("model", base_name)(Xa, ya, Xte, ctx)

    fn.__name__ = (f"gan_augmented_{base_name}"
                   + (f"_batch{batch}" if batch is not None else ""))
    return fn


# ⚠ **batch 16,384 を別の名前で登録する**（2026-09-13。利用者の決定「両方回す」）。
# ⚠ **既定の 1024 と同じ鍵にまとめない。** 台帳の鍵はモデル名を含むので、名前を割れば行も割れる。
# ⚠ **速さのために入れた水準だが、入れた以上は普通の試行として数える**（rules.md 14-9・13-9 の 3）。
# ⚠ **1 反復の時間は batch にほとんど依らない**【実測 2026-09-13】ので、
# ⚠ **反復数が 1/9 になるぶんそのまま速くなる**（記録 `gan-threshold-trading.md`）。
GAN_BATCH_LEVELS = {"": None, "(batch16k)": 16384}

for _base in ("Ridge", "LightGBM", "MLP"):
    for _suffix, _batch in GAN_BATCH_LEVELS.items():
        register("model", f"{_base}+GAN増強{_suffix}")(_augmented(_base, _batch))
