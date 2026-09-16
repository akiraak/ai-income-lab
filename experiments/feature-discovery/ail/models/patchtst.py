"""PatchTST — 系列モデル 1 本（[プラン](../../../../docs/plans/patchtst-threshold.md)）。

⚠ **公式実装の移植である**（[yuqinie98/PatchTST](https://github.com/yuqinie98/PatchTST) commit `204c21e` の
`PatchTST_supervised/layers/PatchTST_backbone.py`・`RevIN.py`。論文 [arXiv:2211.14730](https://arxiv.org/abs/2211.14730)）。
⚠ **属性の名前と作る順番を公式と揃えてある**ので、公式の `state_dict` がそのまま読める
（⚠ **同じ重みで出力が一致することを 2026-09-15 に確かめた**。記録 `docs/specs/experiments/patchtst-threshold.md`）。

| 部品 | 値（⚠ 振らない） | 出所 |
| --- | --- | --- |
| 大きさ | 層 3 ・ 頭 4 ・ D 16 ・ F 128 ・ ドロップアウト 0.3 | 縮小側のスクリプト `etth1.sh`・`etth2.sh`（選んだ規則はプラン §0-2 の 2） |
| パッチ | 長さ 16 ・ ずらし 8 ・ 末尾を最後の値で 8 点埋める（長さ 60 → 7 パッチ） | `run_longExp.py` の既定 |
| 正規化 | RevIN（affine なし・平均を引く） | 同上 |
| 学習 | Adam ・ 1e-4 ・ MSE ・ batch 128（端数は捨てる）・ 100 epoch ・ `type3` ・ 我慢 100 | スクリプト ＋ `run_longExp.py` の既定 ＋ `exp_main.py` |

⚠ **公式と違うところ**（プラン §0-2 の 5〜7）:
  - 末尾の埋め方を `ReplicationPad1d` ではなく「最後の値をつなぐ」で書く（同じ値。CUDA の逆伝播が非決定的なので）
  - 早期打ち切りと重みの選択に使う検証は、⚠ **訓練分割の尻 10%**（`tail_holdout`。検証 fold には触れない）
  - `LEAK_` 列（leak 対照）があるときだけ、RevIN で戻した後の予測に ⚠ **重み 0 で始まる線形の項**を足す。
    ⚠ **本番の表には無いので、本番は公式の構造そのまま**

⚠ **窓の配列でしか呼べない。** 検知器（`ail/detectors/seqmodel.py`）が ctx に `seq_window` を入れて呼ぶ。
選別 × モデルの経路（標準化した 35 列）で呼ばれたら止める（35 列を系列と見なして黙って動かないように）。

デバイスは `AIL_TORCH_DEVICE`（`deep.py` と同じ）。⚠ **本番は cpu / cuda を明示して 1 実行の中で混ぜない。**
"""

from __future__ import annotations

import functools

import numpy as np

from ail.models.deep import _seed_all, torch_device
from ail.models.holdout import tail_holdout
from ail.registry import register

# ⚠ **水準は事前固定**（プラン §0-1）。⚠ **結果を見て動かさない**（rules.md 13-6 の 3）
ARCH = {"n_layers": 3, "n_heads": 4, "d_model": 16, "d_ff": 128, "dropout": 0.3,
        "head_dropout": 0.0, "patch_len": 16, "stride": 8}
TRAIN = {"epochs": 100, "batch": 128, "lr": 1e-4, "patience": 100, "lradj": "type3"}
# ⚠ **学習の記録を検知器へ返す置き場**（モデルの関数は予測しか返せないので、検知器が渡した ctx の写しに書く）
DOC_KEY = "_patchtst_fit"


def patch_num(window: int, patch_len: int = ARCH["patch_len"], stride: int = ARCH["stride"]) -> int:
    """公式の `PatchTST_backbone` と同じ数え方（`padding_patch='end'` で 1 つ増える）。"""
    return int((window - patch_len) / stride + 1) + 1


@functools.lru_cache(maxsize=None)
def _net_class():
    """⚠ **torch はここで初めて読む**（`deep.py` と同じ。既存の実行は torch を読み込まない経路がある）。"""
    import torch
    import torch.nn.functional as F
    from torch import nn

    class RevIN(nn.Module):
        """`RevIN.py`（affine=False・subtract_last=False）。統計は切り離す（`detach`）。"""

        def __init__(self, eps: float = 1e-5):
            super().__init__()
            self.eps = eps

        def norm(self, x):                                   # x: [bs, 長さ, 変数]
            self.mean = torch.mean(x, dim=1, keepdim=True).detach()
            self.stdev = torch.sqrt(torch.var(x, dim=1, keepdim=True, unbiased=False) + self.eps).detach()
            return (x - self.mean) / self.stdev

        def denorm(self, x):
            return x * self.stdev + self.mean

    class Transpose(nn.Module):
        def __init__(self, *dims):
            super().__init__()
            self.dims = dims

        def forward(self, x):
            return x.transpose(*self.dims)

    class ScaledDotProductAttention(nn.Module):
        """`_ScaledDotProductAttention`（attn_dropout=0・lsa=False・残差注意）。"""

        def __init__(self, d_model: int, n_heads: int):
            super().__init__()
            self.attn_dropout = nn.Dropout(0.0)
            self.scale = nn.Parameter(torch.tensor((d_model // n_heads) ** -0.5), requires_grad=False)

        def forward(self, q, k, v, prev):
            scores = torch.matmul(q, k) * self.scale
            if prev is not None:
                scores = scores + prev                       # ⚠ 前の層の注意スコアを足す（res_attention）
            w = self.attn_dropout(F.softmax(scores, dim=-1))
            return torch.matmul(w, v), scores

    class MultiheadAttention(nn.Module):
        def __init__(self, d_model: int, n_heads: int, proj_dropout: float):
            super().__init__()
            d_k = d_v = d_model // n_heads
            self.n_heads, self.d_k, self.d_v = n_heads, d_k, d_v
            self.W_Q = nn.Linear(d_model, d_k * n_heads, bias=True)
            self.W_K = nn.Linear(d_model, d_k * n_heads, bias=True)
            self.W_V = nn.Linear(d_model, d_v * n_heads, bias=True)
            self.sdp_attn = ScaledDotProductAttention(d_model, n_heads)
            self.to_out = nn.Sequential(nn.Linear(n_heads * d_v, d_model), nn.Dropout(proj_dropout))

        def forward(self, x, prev):
            bs = x.size(0)
            q = self.W_Q(x).view(bs, -1, self.n_heads, self.d_k).transpose(1, 2)
            k = self.W_K(x).view(bs, -1, self.n_heads, self.d_k).permute(0, 2, 3, 1)
            v = self.W_V(x).view(bs, -1, self.n_heads, self.d_v).transpose(1, 2)
            out, scores = self.sdp_attn(q, k, v, prev)
            out = out.transpose(1, 2).contiguous().view(bs, -1, self.n_heads * self.d_v)
            return self.to_out(out), scores

    class EncoderLayer(nn.Module):
        """`TSTEncoderLayer`（後置の BatchNorm・GELU・残差注意）。"""

        def __init__(self, d_model: int, n_heads: int, d_ff: int, dropout: float):
            super().__init__()
            self.self_attn = MultiheadAttention(d_model, n_heads, dropout)
            self.dropout_attn = nn.Dropout(dropout)
            self.norm_attn = nn.Sequential(Transpose(1, 2), nn.BatchNorm1d(d_model), Transpose(1, 2))
            self.ff = nn.Sequential(nn.Linear(d_model, d_ff, bias=True), nn.GELU(),
                                    nn.Dropout(dropout), nn.Linear(d_ff, d_model, bias=True))
            self.dropout_ffn = nn.Dropout(dropout)
            self.norm_ffn = nn.Sequential(Transpose(1, 2), nn.BatchNorm1d(d_model), Transpose(1, 2))

        def forward(self, src, prev):
            src2, scores = self.self_attn(src, prev)
            src = self.norm_attn(src + self.dropout_attn(src2))
            src = self.norm_ffn(src + self.dropout_ffn(self.ff(src)))
            return src, scores

    class Encoder(nn.Module):
        def __init__(self, n_layers: int, **kw):
            super().__init__()
            self.layers = nn.ModuleList([EncoderLayer(**kw) for _ in range(n_layers)])

        def forward(self, src):
            scores = None
            for mod in self.layers:
                src, scores = mod(src, scores)
            return src

    class TSTiEncoder(nn.Module):
        """`TSTiEncoder`（系列ごとに独立 ＝ channel-independent）。位置埋め込みは `zeros`（一様 ±0.02・学習する）。"""

        def __init__(self, patch_num: int, patch_len: int, n_layers: int, d_model: int,
                     n_heads: int, d_ff: int, dropout: float):
            super().__init__()
            self.patch_num, self.patch_len = patch_num, patch_len
            self.W_P = nn.Linear(patch_len, d_model)
            w_pos = torch.empty((patch_num, d_model))
            nn.init.uniform_(w_pos, -0.02, 0.02)
            self.W_pos = nn.Parameter(w_pos, requires_grad=True)
            self.dropout = nn.Dropout(dropout)
            self.encoder = Encoder(n_layers, d_model=d_model, n_heads=n_heads, d_ff=d_ff, dropout=dropout)

        def forward(self, x):                                # x: [bs, 変数, パッチ長, パッチ数]
            n_vars = x.shape[1]
            x = self.W_P(x.permute(0, 1, 3, 2))              # [bs, 変数, パッチ数, D]
            u = torch.reshape(x, (x.shape[0] * x.shape[1], x.shape[2], x.shape[3]))
            z = self.encoder(self.dropout(u + self.W_pos))
            z = torch.reshape(z, (-1, n_vars, z.shape[-2], z.shape[-1]))
            return z.permute(0, 1, 3, 2)                     # [bs, 変数, D, パッチ数]

    class FlattenHead(nn.Module):
        def __init__(self, nf: int, target_window: int, head_dropout: float):
            super().__init__()
            self.flatten = nn.Flatten(start_dim=-2)
            self.linear = nn.Linear(nf, target_window)
            self.dropout = nn.Dropout(head_dropout)

        def forward(self, x):
            return self.dropout(self.linear(self.flatten(x)))

    class PatchTSTNet(nn.Module):
        """`PatchTST_backbone`（1 変数・予測の長さ 1）。⚠ **入力は [bs, 長さ]、出力は [bs]。**"""

        def __init__(self, window: int, n_exog: int = 0, n_layers: int = ARCH["n_layers"],
                     n_heads: int = ARCH["n_heads"], d_model: int = ARCH["d_model"],
                     d_ff: int = ARCH["d_ff"], dropout: float = ARCH["dropout"],
                     head_dropout: float = ARCH["head_dropout"], patch_len: int = ARCH["patch_len"],
                     stride: int = ARCH["stride"]):
            super().__init__()
            self.revin_layer = RevIN()
            self.patch_len, self.stride = patch_len, stride
            pn = patch_num(window, patch_len, stride)
            self.backbone = TSTiEncoder(pn, patch_len, n_layers, d_model, n_heads, d_ff, dropout)
            self.head = FlattenHead(d_model * pn, 1, head_dropout)
            # ⚠ **leak 対照のときだけ作る**（プラン §0-2 の 6）。⚠ **重み 0 で始める**（最初は本番と同じ予測）
            self.exog = None
            if n_exog:
                self.exog = nn.Linear(n_exog, 1, bias=False)
                nn.init.zeros_(self.exog.weight)

        def pad_end(self, z):
            """`ReplicationPad1d((0, stride))` と同じ値（最後の値を stride 回つなぐ）。"""
            return torch.cat([z, z[..., -1:].expand(*z.shape[:-1], self.stride)], dim=-1)

        def forward(self, x, exog=None):
            z = self.revin_layer.norm(x.unsqueeze(-1))       # [bs, 長さ, 1]
            z = self.pad_end(z.permute(0, 2, 1))             # [bs, 1, 長さ + stride]
            z = z.unfold(dimension=-1, size=self.patch_len, step=self.stride).permute(0, 1, 3, 2)
            z = self.head(self.backbone(z))                  # [bs, 1, 1]
            out = self.revin_layer.denorm(z.permute(0, 2, 1)).reshape(-1)
            if self.exog is not None:
                out = out + self.exog(exog).reshape(-1)
            return out

    return PatchTSTNet


def build(window: int, n_exog: int = 0):
    return _net_class()(window, n_exog)


def _lr_type3(epoch_1based: int, lr0: float) -> float:
    """`utils/tools.py` の `type3`: 3 epoch 目までは lr0、それ以降は ×0.9/epoch（epoch の終わりに当てる）。"""
    return lr0 if epoch_1based < 3 else lr0 * (0.9 ** (epoch_1based - 3))


@register("model", "PatchTST")
def patchtst(Xtr, ytr, Xte, ctx):
    import torch
    from torch import nn

    window = ctx.get("seq_window")
    if window is None:
        raise SystemExit("⚠ PatchTST は窓の配列でしか呼べない（検知器 `S1 PatchTST（60日窓）` から呼ぶ）。"
                         "選別 × モデルの経路で `model = \"PatchTST\"` にしていないか確かめる")
    window, n_exog = int(window), int(ctx.get("n_exog", 0))
    Xtr = np.asarray(Xtr, dtype=np.float32)
    Xte = np.asarray(Xte, dtype=np.float32)
    if Xtr.ndim != 2 or Xtr.shape[1] != window + n_exog or Xte.shape[1:] != Xtr.shape[1:]:
        raise SystemExit(f"⚠ PatchTST の入力の形が違う: 訓練 {Xtr.shape} ／ 検証 {Xte.shape}"
                         f"（窓 {window} ＋ 外生 {n_exog} 列のはず）")
    seed = int(ctx.get("seed", 0))
    # ⚠ **`patchtst_epochs`・`patchtst_lr` はテスト用の口**（config の `model_args` には書かない）。記録に実際の値を残す
    epochs = int(ctx.get("patchtst_epochs", TRAIN["epochs"]))
    lr0 = float(ctx.get("patchtst_lr", TRAIN["lr"]))
    batch, patience = TRAIN["batch"], TRAIN["patience"]
    _seed_all(seed)
    dev = torch_device()

    (Xf, yf), holdout = tail_holdout(Xtr, np.asarray(ytr, dtype=float))
    # ⚠ **窓・外生・y を同じ尺度で割る**（公式は訓練区間で標準化してから入れる。プラン §0-1 の「尺度」）
    ysd = float(np.std(yf)) or 1.0

    def tensors(X):
        t = torch.as_tensor(X, device=dev) / ysd
        return t[:, :window].contiguous(), (t[:, window:].contiguous() if n_exog else None)

    net = build(window, n_exog).to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=lr0)
    loss_fn = nn.MSELoss()
    g = torch.Generator(device="cpu").manual_seed(seed)
    xf, ef = tensors(Xf)
    yf_t = torch.as_tensor((np.asarray(yf) / ysd).astype(np.float32), device=dev)

    def predict(x, e):
        net.eval()
        outs = []
        with torch.no_grad():
            for s in range(0, len(x), 65536):
                outs.append(net(x[s:s + 65536], None if e is None else e[s:s + 65536]))
        return torch.cat(outs) if outs else torch.empty(0, device=dev)

    val = None
    if holdout is not None:
        xv, ev = tensors(holdout[0])
        yv_t = torch.as_tensor((np.asarray(holdout[1]) / ysd).astype(np.float32), device=dev)
        val = (xv, ev, yv_t)

    n, history = len(xf), []
    best, best_state, best_epoch, bad = float("inf"), None, 0, 0
    epochs_run = 0
    for epoch in range(1, epochs + 1):
        net.train()
        perm = torch.randperm(n, generator=g).to(dev)
        for s in range(n // batch):                          # ⚠ 端数のバッチは捨てる（公式の drop_last）
            i = perm[s * batch:(s + 1) * batch]
            opt.zero_grad()
            loss = loss_fn(net(xf[i], None if ef is None else ef[i]), yf_t[i])
            loss.backward()
            opt.step()
        epochs_run = epoch
        if val is not None:
            v = float(loss_fn(predict(val[0], val[1]), val[2]))
            history.append(round(v, 6))
            # ⚠ 公式の EarlyStopping と同じ向き（同じ値も「良くなった」に数える）
            if best_state is None or v <= best:
                best, best_epoch, bad = v, epoch, 0
                best_state = {k: w.detach().clone() for k, w in net.state_dict().items()}
            else:
                bad += 1
                if bad >= patience:
                    break
        for group in opt.param_groups:
            group["lr"] = _lr_type3(epoch, lr0)
    if best_state is not None:
        net.load_state_dict(best_state)

    xte, ete = tensors(Xte)
    pred = predict(xte, ete).cpu().numpy().astype(float) * ysd
    doc = {"device": str(dev), "arch": dict(ARCH), "epochs": epochs, "epochs_run": epochs_run,
           "lr": lr0, "batch": batch, "patience": patience, "lradj": TRAIN["lradj"],
           "params": int(sum(p.numel() for p in net.parameters())),
           "学習の行": int(n), "検証の行": 0 if val is None else int(len(val[0])), "ysd": ysd,
           "best_epoch": best_epoch if val is not None else None,
           "val_mse_best": None if val is None else round(best, 6),
           # ⚠ **「0 と予測した」ときの検証 MSE**（尺度は ysd で割った後）。これを下回らなければ何も学んでいない
           "val_mse_zero": None if val is None else round(float((val[2] ** 2).mean()), 6),
           "val_mse_by_epoch": history,
           "exog_weight": (None if net.exog is None
                           else [round(float(w), 6) for w in net.exog.weight.detach().cpu().reshape(-1)])}
    ctx[DOC_KEY] = doc
    return pred
