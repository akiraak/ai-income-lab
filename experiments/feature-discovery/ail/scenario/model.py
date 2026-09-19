"""条件付き WGAN-GP — 過去 60 日を条件に、次の 5 日の日次対数リターンをまとめて生成する（プラン §4）。

    Generator(X_t, z)  -> Y_generated         X_t: [batch, 60, features]・z: [batch, latent]
    Critic(X_t, Y)     -> 実数スコア           Y:   [batch, 5]（⚠ sigmoid を掛けない）

⚠ **生成器は実測の未来 Y_real を 1 度も受け取らない**（学習時に Critic だけが見る）。
⚠ **勾配ペナルティは、同じ X_t の下で実測と生成の未来系列を補間し、その未来系列に関する勾配に掛ける**（X には掛けない）。
⚠ **checkpoint は検証期間の CRPS で選ぶ**（敵対的損失では選ばない）。⚠ **MSE の補助損失は入れない**（分布が縮む）。

⚠ **増強 GAN（`ail/models/gan.py`）とは別物**で、部品も共有しない（記録 `cgan-scenario.md` §0 決定 4）。
共有するのはデバイスの選び方と種の固定（`ail/models/deep.py`）だけ。入力は `data.Scaler` を通した後の値。
"""

from __future__ import annotations

import copy

import numpy as np

from ail.models.deep import _seed_all, torch_device
from ail.scenario import metrics

# ⚠ **モード崩壊の見張り**（診断であって、checkpoint の選択には使わない。数値は 2026-09-18 に事前固定）
#   多様性 ＝ シナリオ間の 5 日累積リターンの σ（起点の平均）÷ 学習区間の実測の 5 日累積リターンの σ
COLLAPSE_RATIO = 0.1         # これ未満 ＝ z を変えてもほぼ同じ未来しか出していない
EXTREME_SIGMA = 10.0         # 日次リターンが学習区間の σ の何倍を超えたら「極端な値」と数えるか
EXTREME_SHARE = 0.01         # 極端な値の割合がこれを超えたら印を付ける


def build(n_features: int, horizon: int, cfg: dict):
    """(生成器, Critic)。⚠ **エンコーダの重みは別々**（共有すると Critic の勾配が生成器の条件の読み方を引っぱる）。"""
    import torch
    from torch import nn

    if cfg.get("encoder", "gru") != "gru":
        raise SystemExit(f"⚠ encoder は gru だけ（{cfg.get('encoder')}）。足すなら記録 §0-3 に新しい構成として登録してから")
    hidden, layers, latent = int(cfg["hidden"]), int(cfg["layers"]), int(cfg["latent_dim"])

    class Encoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = nn.GRU(n_features, hidden, num_layers=layers, batch_first=True)

        def forward(self, x):
            return self.gru(x)[0][:, -1]           # 起点の足の隠れ状態

    class Generator(nn.Module):
        def __init__(self):
            super().__init__()
            self.enc = Encoder()
            self.head = nn.Sequential(nn.Linear(hidden + latent, hidden), nn.LeakyReLU(0.2),
                                      nn.Linear(hidden, horizon))

        def forward(self, x, z):
            return self.head(torch.cat([self.enc(x), z], dim=1))

    class Critic(nn.Module):
        # ⚠ **BatchNorm を入れない**（勾配ペナルティは標本ごとの勾配に掛けるので、batch を跨ぐ正規化と両立しない）
        def __init__(self):
            super().__init__()
            self.enc = Encoder()
            self.head = nn.Sequential(nn.Linear(hidden + horizon, hidden), nn.LeakyReLU(0.2),
                                      nn.Linear(hidden, hidden), nn.LeakyReLU(0.2),
                                      nn.Linear(hidden, 1))

        def score(self, h, y):
            return self.head(torch.cat([h, y], dim=1)).squeeze(1)

        def forward(self, x, y):
            return self.score(self.enc(x), y)

    gen = Generator()
    gen.latent_dim = latent
    return gen, Critic()


def gradient_penalty(critic, x, y_real, y_fake, eps):
    """⚠ **同じ X_t の下で未来系列だけを補間し、未来系列に関する勾配のノルムを 1 に寄せる。**"""
    import torch

    mix = (eps * y_real + (1 - eps) * y_fake).requires_grad_(True)
    grad = torch.autograd.grad(critic(x, mix).sum(), mix, create_graph=True)[0]
    return ((grad.norm(2, dim=1) - 1) ** 2).mean()


def generate(gen, X: np.ndarray, n: int, seed: int, device=None, chunk: int = 256) -> np.ndarray:
    """起点ごとに n 本。返り値 `[len(X), n, horizon]`（⚠ **scaler を通した後の単位**。戻すのは呼び元）。

    ⚠ **z は CPU の乱数列から作る**（デバイスに依らず同じ種で同じ z。決定性）。
    """
    import torch

    device = device or next(gen.parameters()).device
    g = torch.Generator(device="cpu").manual_seed(int(seed))
    gen.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(X), chunk):
            x = torch.as_tensor(X[i:i + chunk], device=device)
            h = gen.enc(x).repeat_interleave(n, dim=0)             # 条件は 1 度だけ符号化して n 本に配る
            z = torch.randn(len(x) * n, gen.latent_dim, generator=g).to(device)
            y = gen.head(torch.cat([h, z], dim=1))
            out.append(y.reshape(len(x), n, -1).cpu().numpy())
    return np.concatenate(out)


def diagnose(paths_scaled: np.ndarray, real_5d_sigma_scaled: float) -> dict:
    """モード崩壊と極端な値の見張り（プラン §4）。入力は scaler の単位の日次リターン `[n, m, horizon]`。"""
    spread = float(paths_scaled.sum(axis=2).std(axis=1).mean())
    ratio = spread / real_5d_sigma_scaled if real_5d_sigma_scaled > 0 else float("nan")
    extreme = float((np.abs(paths_scaled) > EXTREME_SIGMA).mean())
    return {"diversity_ratio": ratio, "extreme_share": extreme,
            "finite": bool(np.isfinite(paths_scaled).all()),
            "collapsed": bool(ratio < COLLAPSE_RATIO), "extreme": bool(extreme > EXTREME_SHARE)}


def fit(Xtr, Ytr, Xva, Yva, cfg: dict, seed: int, y_scale: float, log=print, device=None):
    """学習して、⚠ **検証の CRPS が最良だった時点の生成器**を返す。入力は全部 scaler を通した後の値。

    `y_scale` は検証の CRPS を元の単位（累積単純リターン）で測るためだけに使う。
    返り値 `(gen, history)`。`history` は検証を測った epoch ごとの 1 行（損失・CRPS・見張り）。
    """
    import torch

    _seed_all(seed)
    device = device or torch_device()
    gen, critic = build(Xtr.shape[2], Ytr.shape[1], cfg)
    gen, critic = gen.to(device), critic.to(device)
    betas = tuple(cfg["betas"])
    opt_g = torch.optim.Adam(gen.parameters(), lr=float(cfg["lr"]), betas=betas)
    opt_c = torch.optim.Adam(critic.parameters(), lr=float(cfg["lr"]), betas=betas)
    X, Y = torch.as_tensor(Xtr, device=device), torch.as_tensor(Ytr, device=device)
    g = torch.Generator(device="cpu").manual_seed(int(seed))       # 並べ替え・z・ε は全部 CPU の乱数列から
    batch, n_critic, lam = min(int(cfg["batch"]), len(X)), int(cfg["n_critic"]), float(cfg["gp_lambda"])
    latent = gen.latent_dim
    real_sigma = float(np.asarray(Ytr).sum(axis=1).std())

    def noise(n):
        return torch.randn(n, latent, generator=g).to(device)

    best = {"crps": float("inf"), "epoch": 0, "state": None}
    history, stale = [], 0
    for epoch in range(1, int(cfg["max_epochs"]) + 1):
        gen.train()
        loss_c = loss_g = torch.zeros(())
        for i in torch.randperm(len(X), generator=g).split(batch):
            x, y = X[i.to(device)], Y[i.to(device)]
            for _ in range(n_critic):
                fake = gen(x, noise(len(x))).detach()              # ⚠ Critic の更新では生成器に勾配を流さない
                eps = torch.rand(len(x), 1, generator=g).to(device)
                loss_c = critic(x, fake).mean() - critic(x, y).mean() \
                    + lam * gradient_penalty(critic, x, y, fake, eps)
                opt_c.zero_grad()
                loss_c.backward()
                opt_c.step()
            loss_g = -critic(x, gen(x, noise(len(x)))).mean()      # ⚠ こちらは生成器まで勾配が届く
            opt_g.zero_grad()
            loss_g.backward()
            opt_g.step()
        if epoch % int(cfg["eval_every"]) and epoch != int(cfg["max_epochs"]):
            continue
        # ⚠ **検証の z は毎回同じ種**（checkpoint どうしを同じ乱数で比べる。学習の乱数列は進めない）
        paths = generate(gen, Xva, int(cfg["eval_scenarios"]), seed=seed + 10_000, device=device)
        crps = metrics.crps_5d(paths * y_scale, np.asarray(Yva) * y_scale)
        row = {"epoch": epoch, "loss_critic": float(loss_c.detach()), "loss_generator": float(loss_g.detach()),
               "val_crps": crps, **diagnose(paths, real_sigma)}
        history.append(row)
        log(f"  epoch {epoch:>3}  critic {row['loss_critic']:+.4f}  gen {row['loss_generator']:+.4f}  "
            f"検証 CRPS {crps:.6f}  多様性 {row['diversity_ratio']:.2f}"
            + ("  ⚠ 崩壊" if row["collapsed"] else "") + ("  ⚠ 極端な値" if row["extreme"] else ""))
        if not np.isfinite(crps):
            log("  ⚠ 検証の CRPS が有限でない。ここで止める")
            break
        if crps < best["crps"]:
            best = {"crps": crps, "epoch": epoch, "state": copy.deepcopy(gen.state_dict())}
            stale = 0
        else:
            stale += 1
            if stale >= int(cfg["patience"]):
                break
    if best["state"] is None:
        raise SystemExit("⚠ 有限な検証 CRPS が 1 度も出なかった（学習が発散した）")
    gen.load_state_dict(best["state"])
    for row in history:
        row["selected"] = row["epoch"] == best["epoch"]
    return gen, history


def save(path: str, gen, scaler_doc: dict, cfg: dict, n_features: int, horizon: int, meta: dict) -> None:
    """重み ＋ 前処理 ＋ 特徴量の順序 ＋ 設定を 1 ファイルに（プラン §8）。⚠ **予測の入口はこれだけで動く**。"""
    import torch

    torch.save({"state": {k: v.cpu() for k, v in gen.state_dict().items()}, "scaler": scaler_doc,
                "model_cfg": dict(cfg), "n_features": n_features, "horizon": horizon, "meta": meta}, path)


def load(path: str, device=None):
    """`save` の逆。返り値 `(gen, doc)`。⚠ **weights_only で読む**（保存したのはテンソルと素の値だけ）。"""
    import torch

    doc = torch.load(path, map_location="cpu", weights_only=True)
    gen, _ = build(doc["n_features"], doc["horizon"], doc["model_cfg"])
    gen.load_state_dict(doc["state"])
    return gen.to(device or torch_device()), doc
