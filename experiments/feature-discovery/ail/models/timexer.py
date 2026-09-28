"""TimeXer — 内生 1 本 ＋ 外生 K 本の系列モデル（[記録](../../../../docs/specs/experiments/exog-deep-models.md) §0）。

⚠ **公式実装の移植である**（[thuml/Time-Series-Library](https://github.com/thuml/Time-Series-Library) commit `4e938a1`・
取得 2026-09-27 の `models/TimeXer.py`・`layers/Embed.py`・`layers/SelfAttention_Family.py`。論文 [NeurIPS 2024](https://proceedings.neurips.cc//paper_files/paper/2024/hash/0113ef4642264adc2e6924a3cbbdf532-Abstract-Conference.html)）。
⚠ **`features = "MS"` の形**（外生 K 本を見て内生 1 本の先を当てる）だけを写した。内生はパッチ ＋ 大域トークン、
外生は 1 系列 1 トークン（inverted）で、大域トークンだけが外生へ cross-attention する — 公式と同じ。

| 部品 | 値（⚠ 振らない） | 出所（規則 ＝ 公式の外生つき予測のスクリプト `scripts/forecast_exogenous/EPF/TimeXer.sh` 5 本の多数決 ＋ `run.py` の既定） |
| --- | --- | --- |
| 大きさ | 層 2 ・ 頭 8 ・ D 512 ・ F 2048 ・ ドロップアウト 0.1 ・ GELU | 層 ＝ 5 本（3・3・2・2・1）の中央値 ／ D ＝ 5 本とも 512 ／ F ＝ 3 本が既定 2048 ／ 頭・ドロップアウト・活性は `run.py` の既定 |
| パッチ | 長さ 10（窓 60 → 6 パッチ ＋ 大域 1） | 公式は「窓 ÷ パッチ長」の切り捨て。ETT のスクリプト（96 ÷ 16 ＝ 6 パッチ）と同じ 6 パッチになる長さを選んだ |
| 正規化 | 窓ごとの平均・分散（Non-stationary Transformer の形。⚠ **外生も含む全変数**）。戻すのは内生の統計で | 公式 `use_norm = 1` |
| 学習 | Adam ・ 1e-4 ・ MSE ・ batch 16 ・ 10 epoch ・ `type1`（epoch ごとに半減）・ 我慢 3（検証 MSE が最小の epoch の重み） | batch ＝ 5 本のうち 3 本が 16 ／ ほかは `run.py` の既定 |

⚠ **公式と違うところ**（記録 §0-2）:
  - 予測の長さは 1 で、⚠ **その 1 点は「10 営業日先の道筋の値」＝ `y_fwd_10`**（公式は連続する `pred_len` 本）。
    道筋 path[j] = log c[t−59+j] − log c[t] の続きなので、先 10 日の点 ＝ log c[t+10] − log c[t] ＝ 学習の対象そのもの
  - 時刻の印（`x_mark`。時・曜日）は入れない（日足で、暦は別の層 `cal` の役目）
  - 尺度: 公式は訓練区間の `StandardScaler` を全変数に当てる。ここでは内生と `y` を同じ 1 つの数（訓練の内生の値の標準偏差）で割り、
    外生は 1 変数ずつ訓練の標準偏差で割る（窓ごとの正規化があるので外生の尺度は数値の安定のためだけ）
  - 早期打ち切りの検証は ⚠ **訓練分割の尻を日付で切り、10 営業日ぶん（暦 15 日）パージ**（`date_holdout`。検知器が `ts_tr` を渡す。
    渡されなければ行数の `tail_holdout`）。検証 fold には触れない
  - `LEAK_` 列（leak 対照）があるときだけ、戻した後の予測に ⚠ **重み 0 で始まる線形の項**を足す（PatchTST と同じ）

⚠ **窓の配列でしか呼べない。** 検知器（`ail/detectors/exomodel.py`）が ctx に `seq_window`・`n_exo` を入れて呼ぶ。
入力は 1 行 ＝ [内生 60 ｜ 外生 K × 60（変数ごとに 60 が並ぶ）｜ `LEAK_` 列]。

デバイスは `AIL_TORCH_DEVICE`（`deep.py` と同じ）。⚠ **本番は cuda を明示して 1 実行の中で混ぜない。**
"""

from __future__ import annotations

import functools
import math

import numpy as np

from ail.models.deep import _seed_all, torch_device
from ail.models.holdout import date_holdout, tail_holdout
from ail.registry import register

# ⚠ **水準は事前固定**（記録 §0-2）。⚠ **結果を見て動かさない**（rules.md 13-6 の 3・14-9）
ARCH = {"e_layers": 2, "n_heads": 8, "d_model": 512, "d_ff": 2048, "dropout": 0.1,
        "patch_len": 10, "activation": "gelu", "use_norm": True}
TRAIN = {"epochs": 10, "batch": 16, "lr": 1e-4, "patience": 3, "lradj": "type1"}
# ⚠ **学習の記録を検知器へ返す置き場**（モデルの関数は予測しか返せないので、検知器が渡した ctx の写しに書く）
DOC_KEY = "_timexer_fit"
# 早期打ち切りの holdout に使うラベルの長さ（営業日）。⚠ 検知器の地平（先 10 日）と同じ
LABEL_BARS = 10


def patch_num(window: int, patch_len: int = ARCH["patch_len"]) -> int:
    """公式 `Model.__init__` の `int(seq_len // patch_len)`。"""
    return int(window // patch_len)


@functools.lru_cache(maxsize=None)
def _net_class():
    """⚠ **torch はここで初めて読む**（`deep.py`・`patchtst.py` と同じ）。"""
    import torch
    import torch.nn.functional as F
    from torch import nn

    class PositionalEmbedding(nn.Module):
        """`Embed.PositionalEmbedding`（sin ／ cos の固定表。学習しない）。"""

        def __init__(self, d_model: int, max_len: int = 5000):
            super().__init__()
            pe = torch.zeros(max_len, d_model).float()
            position = torch.arange(0, max_len).float().unsqueeze(1)
            div_term = (torch.arange(0, d_model, 2).float() * -(math.log(10000.0) / d_model)).exp()
            pe[:, 0::2] = torch.sin(position * div_term)
            pe[:, 1::2] = torch.cos(position * div_term)
            self.register_buffer("pe", pe.unsqueeze(0))

        def forward(self, x):
            return self.pe[:, :x.size(1)]

    class EnEmbedding(nn.Module):
        """`TimeXer.EnEmbedding`（内生: パッチ → 線形 ＋ 位置、末尾に大域トークン）。"""

        def __init__(self, n_vars: int, d_model: int, patch_len: int, dropout: float):
            super().__init__()
            self.patch_len = patch_len
            self.value_embedding = nn.Linear(patch_len, d_model, bias=False)
            self.glb_token = nn.Parameter(torch.randn(1, n_vars, 1, d_model))
            self.position_embedding = PositionalEmbedding(d_model)
            self.dropout = nn.Dropout(dropout)

        def forward(self, x):                                # x: [bs, 変数, 長さ]
            n_vars = x.shape[1]
            glb = self.glb_token.repeat((x.shape[0], 1, 1, 1))
            x = x.unfold(dimension=-1, size=self.patch_len, step=self.patch_len)
            x = torch.reshape(x, (x.shape[0] * x.shape[1], x.shape[2], x.shape[3]))
            x = self.value_embedding(x) + self.position_embedding(x)
            x = torch.reshape(x, (-1, n_vars, x.shape[-2], x.shape[-1]))
            x = torch.cat([x, glb], dim=2)
            x = torch.reshape(x, (x.shape[0] * x.shape[1], x.shape[2], x.shape[3]))
            return self.dropout(x), n_vars

    class DataEmbeddingInverted(nn.Module):
        """`Embed.DataEmbedding_inverted`（外生: 1 系列まるごと → 1 トークン）。⚠ 時刻の印は使わない。"""

        def __init__(self, c_in: int, d_model: int, dropout: float):
            super().__init__()
            self.value_embedding = nn.Linear(c_in, d_model)
            self.dropout = nn.Dropout(p=dropout)

        def forward(self, x):                                # x: [bs, 長さ, 変数]
            return self.dropout(self.value_embedding(x.permute(0, 2, 1)))

    class FullAttention(nn.Module):
        """`SelfAttention_Family.FullAttention`（mask_flag=False）。"""

        def __init__(self, attention_dropout: float):
            super().__init__()
            self.dropout = nn.Dropout(attention_dropout)

        def forward(self, queries, keys, values):
            B, L, H, E = queries.shape
            scale = 1.0 / math.sqrt(E)
            scores = torch.einsum("blhe,bshe->bhls", queries, keys)
            A = self.dropout(torch.softmax(scale * scores, dim=-1))
            return torch.einsum("bhls,bshd->blhd", A, values).contiguous()

    class AttentionLayer(nn.Module):
        def __init__(self, d_model: int, n_heads: int, attention_dropout: float):
            super().__init__()
            d_keys = d_model // n_heads
            self.inner_attention = FullAttention(attention_dropout)
            self.query_projection = nn.Linear(d_model, d_keys * n_heads)
            self.key_projection = nn.Linear(d_model, d_keys * n_heads)
            self.value_projection = nn.Linear(d_model, d_keys * n_heads)
            self.out_projection = nn.Linear(d_keys * n_heads, d_model)
            self.n_heads = n_heads

        def forward(self, queries, keys, values):
            B, L, _ = queries.shape
            _, S, _ = keys.shape
            H = self.n_heads
            q = self.query_projection(queries).view(B, L, H, -1)
            k = self.key_projection(keys).view(B, S, H, -1)
            v = self.value_projection(values).view(B, S, H, -1)
            out = self.inner_attention(q, k, v).view(B, L, -1)
            return self.out_projection(out)

    class EncoderLayer(nn.Module):
        """`TimeXer.EncoderLayer`（内生の自己注意 → 大域トークンだけ外生へ cross → 1×1 conv の FFN）。"""

        def __init__(self, d_model: int, n_heads: int, d_ff: int, dropout: float, activation: str):
            super().__init__()
            self.self_attention = AttentionLayer(d_model, n_heads, dropout)
            self.cross_attention = AttentionLayer(d_model, n_heads, dropout)
            self.conv1 = nn.Conv1d(in_channels=d_model, out_channels=d_ff, kernel_size=1)
            self.conv2 = nn.Conv1d(in_channels=d_ff, out_channels=d_model, kernel_size=1)
            self.norm1 = nn.LayerNorm(d_model)
            self.norm2 = nn.LayerNorm(d_model)
            self.norm3 = nn.LayerNorm(d_model)
            self.dropout = nn.Dropout(dropout)
            self.activation = F.relu if activation == "relu" else F.gelu

        def forward(self, x, cross):
            B, L, D = cross.shape
            x = x + self.dropout(self.self_attention(x, x, x))
            x = self.norm1(x)
            x_glb_ori = x[:, -1, :].unsqueeze(1)
            x_glb = torch.reshape(x_glb_ori, (B, -1, D))
            x_glb_attn = self.dropout(self.cross_attention(x_glb, cross, cross))
            x_glb_attn = torch.reshape(x_glb_attn, (x_glb_attn.shape[0] * x_glb_attn.shape[1],
                                                    x_glb_attn.shape[2])).unsqueeze(1)
            x_glb = self.norm2(x_glb_ori + x_glb_attn)
            y = x = torch.cat([x[:, :-1, :], x_glb], dim=1)
            y = self.dropout(self.activation(self.conv1(y.transpose(-1, 1))))
            y = self.dropout(self.conv2(y).transpose(-1, 1))
            return self.norm3(x + y)

    class Encoder(nn.Module):
        def __init__(self, layers, d_model: int):
            super().__init__()
            self.layers = nn.ModuleList(layers)
            self.norm = nn.LayerNorm(d_model)

        def forward(self, x, cross):
            for layer in self.layers:
                x = layer(x, cross)
            return self.norm(x)

    class FlattenHead(nn.Module):
        def __init__(self, nf: int, target_window: int, head_dropout: float):
            super().__init__()
            self.flatten = nn.Flatten(start_dim=-2)
            self.linear = nn.Linear(nf, target_window)
            self.dropout = nn.Dropout(head_dropout)

        def forward(self, x):                                # x: [bs, 変数, D, パッチ数 + 1]
            return self.dropout(self.linear(self.flatten(x)))

    class TimeXerNet(nn.Module):
        """`TimeXer.Model`（features='MS'・内生 1 本・予測の長さ 1）。⚠ **入力は [bs, 長さ, 外生 K ＋ 1]（内生が最後）、出力は [bs]。**"""

        def __init__(self, window: int, n_exo: int, n_exog: int = 0, e_layers: int = ARCH["e_layers"],
                     n_heads: int = ARCH["n_heads"], d_model: int = ARCH["d_model"], d_ff: int = ARCH["d_ff"],
                     dropout: float = ARCH["dropout"], patch_len: int = ARCH["patch_len"],
                     activation: str = ARCH["activation"], use_norm: bool = ARCH["use_norm"]):
            super().__init__()
            if n_exo < 1:
                raise ValueError("TimeXer（MS）は外生が 1 本以上要る")
            self.window, self.n_exo, self.use_norm = window, n_exo, use_norm
            self.patch_len = patch_len
            self.patch_num = patch_num(window, patch_len)
            self.en_embedding = EnEmbedding(1, d_model, patch_len, dropout)
            self.ex_embedding = DataEmbeddingInverted(window, d_model, dropout)
            self.encoder = Encoder([EncoderLayer(d_model, n_heads, d_ff, dropout, activation)
                                    for _ in range(e_layers)], d_model)
            self.head = FlattenHead(d_model * (self.patch_num + 1), 1, head_dropout=dropout)
            # ⚠ **leak 対照のときだけ作る**。⚠ **重み 0 で始める**（最初は本番と同じ予測）
            self.exog = None
            if n_exog:
                self.exog = nn.Linear(n_exog, 1, bias=False)
                nn.init.zeros_(self.exog.weight)

        def forward(self, x_enc, exog=None):                 # x_enc: [bs, 長さ, K + 1]
            if self.use_norm:
                means = x_enc.mean(1, keepdim=True).detach()
                x_enc = x_enc - means
                stdev = torch.sqrt(torch.var(x_enc, dim=1, keepdim=True, unbiased=False) + 1e-5)
                x_enc = x_enc / stdev
            en_embed, n_vars = self.en_embedding(x_enc[:, :, -1].unsqueeze(-1).permute(0, 2, 1))
            ex_embed = self.ex_embedding(x_enc[:, :, :-1])
            enc_out = self.encoder(en_embed, ex_embed)
            enc_out = torch.reshape(enc_out, (-1, n_vars, enc_out.shape[-2], enc_out.shape[-1]))
            enc_out = enc_out.permute(0, 1, 3, 2)            # [bs, 変数, D, パッチ数 + 1]
            dec_out = self.head(enc_out).reshape(-1)         # [bs]
            if self.use_norm:
                dec_out = dec_out * stdev[:, 0, -1] + means[:, 0, -1]
            if self.exog is not None:
                dec_out = dec_out + self.exog(exog).reshape(-1)
            return dec_out

    return TimeXerNet


def build(window: int, n_exo: int, n_exog: int = 0):
    return _net_class()(window, n_exo, n_exog)


def _lr_type1(epoch_1based: int, lr0: float) -> float:
    """`utils/tools.py` の `type1`: epoch ごとに半減（1 epoch 目は lr0）。"""
    return lr0 * (0.5 ** (epoch_1based - 1))


def _split(Xtr, ytr, ctx: dict):
    """早期打ち切りの (訓練, 検証)。⚠ **日付があれば日付で切ってパージ**（15-5）、無ければ行数で。"""
    ts = ctx.get("ts_tr")
    if ts is not None and len(ts) == len(Xtr):
        split = date_holdout(ts, label_bars=LABEL_BARS)
        if split is not None:
            head, hold = split
            return (Xtr[head], ytr[head]), (Xtr[hold], ytr[hold]), "date"
        return (Xtr, ytr), None, "none"
    (Xf, yf), holdout = tail_holdout(Xtr, ytr)
    return (Xf, yf), holdout, ("rows" if holdout is not None else "none")


@register("model", "TimeXer")
def timexer(Xtr, ytr, Xte, ctx):
    import torch
    from torch import nn

    window = ctx.get("seq_window")
    if window is None or ctx.get("n_exo") is None:
        raise SystemExit("⚠ TimeXer は窓の配列でしか呼べない（検知器 `S4 TimeXer（60日窓・先10日・外生あり）` から呼ぶ）。"
                         "選別 × モデルの経路で `model = \"TimeXer\"` にしていないか確かめる")
    window, n_exo, n_exog = int(window), int(ctx["n_exo"]), int(ctx.get("n_exog", 0))
    width = window * (1 + n_exo)
    Xtr = np.asarray(Xtr, dtype=np.float32)
    Xte = np.asarray(Xte, dtype=np.float32)
    if Xtr.ndim != 2 or Xtr.shape[1] != width + n_exog or Xte.shape[1:] != Xtr.shape[1:]:
        raise SystemExit(f"⚠ TimeXer の入力の形が違う: 訓練 {Xtr.shape} ／ 検証 {Xte.shape}"
                         f"（内生 {window} ＋ 外生 {n_exo} × {window} ＋ leak {n_exog} 列のはず）")
    seed = int(ctx.get("seed", 0))
    # ⚠ **`timexer_epochs`・`timexer_lr` はテスト用の口**（config の `model_args` には書かない）。記録に実際の値を残す
    epochs = int(ctx.get("timexer_epochs", TRAIN["epochs"]))
    lr0 = float(ctx.get("timexer_lr", TRAIN["lr"]))
    # ⚠ **`timexer_d_model`・`timexer_d_ff` もテスト用の口**（小さい合成データで leak の跳ねを測るため。本番は ARCH のまま）
    arch = {**ARCH, "d_model": int(ctx.get("timexer_d_model", ARCH["d_model"])),
            "d_ff": int(ctx.get("timexer_d_ff", ARCH["d_ff"]))}
    batch, patience = TRAIN["batch"], TRAIN["patience"]
    _seed_all(seed)
    dev = torch_device()

    ytr = np.asarray(ytr, dtype=float)
    (Xf, yf), holdout, split_kind = _split(Xtr, ytr, ctx)
    # ⚠ **尺度は訓練の行で決める**（公式の StandardScaler に当たる）: 内生と y は同じ 1 つの数、外生は変数ごと
    s_endo = float(np.std(Xf[:, :window])) or 1.0
    s_exo = Xf[:, window:width].reshape(len(Xf), n_exo, window).std(axis=(0, 2)).astype(np.float32)
    s_exo[s_exo == 0] = 1.0
    scale = np.concatenate([np.full(window, s_endo, np.float32), np.repeat(s_exo, window)]).astype(np.float32)

    def tensors(X):
        """[bs, 長さ, K + 1]（外生 K 本の後ろに内生 ＝ 公式の MS の並び）と leak の列。"""
        t = torch.as_tensor(X[:, :width] / scale, device=dev)
        endo = t[:, :window].unsqueeze(-1)                                    # [bs, 長さ, 1]
        exo = t[:, window:].reshape(len(X), n_exo, window).permute(0, 2, 1)   # [bs, 長さ, K]
        x = torch.cat([exo, endo], dim=-1).contiguous()
        # ⚠ leak の列も内生と同じ数で割る（PatchTST と同じ。学ぶ重みが 1 前後になる）
        e = torch.as_tensor(X[:, width:] / s_endo, device=dev).contiguous() if n_exog else None
        return x, e

    net = _net_class()(window, n_exo, n_exog, d_model=arch["d_model"], d_ff=arch["d_ff"]).to(dev)
    opt = torch.optim.Adam(net.parameters(), lr=lr0)
    loss_fn = nn.MSELoss()
    g = torch.Generator(device="cpu").manual_seed(seed)
    xf, ef = tensors(Xf)
    yf_t = torch.as_tensor((yf / s_endo).astype(np.float32), device=dev)

    def predict(x, e):
        net.eval()
        outs = []
        with torch.no_grad():
            for s in range(0, len(x), 4096):
                outs.append(net(x[s:s + 4096], None if e is None else e[s:s + 4096]))
        return torch.cat(outs) if outs else torch.empty(0, device=dev)

    val = None
    if holdout is not None:
        xv, ev = tensors(holdout[0])
        yv_t = torch.as_tensor((np.asarray(holdout[1]) / s_endo).astype(np.float32), device=dev)
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
            # ⚠ 公式の EarlyStopping と同じ向き（小さくなったら保存。同じ値では「良くなった」に数えない）
            if best_state is None or v < best:
                best, best_epoch, bad = v, epoch, 0
                best_state = {k: w.detach().clone() for k, w in net.state_dict().items()}
            else:
                bad += 1
                if bad >= patience:
                    break
        for group in opt.param_groups:
            group["lr"] = _lr_type1(epoch + 1, lr0)          # 次の epoch の学習率（公式は epoch の終わりに当てる）
    if best_state is not None:
        net.load_state_dict(best_state)

    xte, ete = tensors(Xte)
    pred = predict(xte, ete).cpu().numpy().astype(float) * s_endo
    doc = {"device": str(dev), "arch": arch, "epochs": epochs, "epochs_run": epochs_run,
           "lr": lr0, "batch": batch, "patience": patience, "lradj": TRAIN["lradj"],
           "params": int(sum(p.numel() for p in net.parameters())),
           "n_exo": n_exo, "patch_num": net.patch_num,
           "学習の行": int(n), "検証の行": 0 if val is None else int(len(val[0])), "holdout": split_kind,
           "s_endo": s_endo, "s_exo_min": float(s_exo.min()), "s_exo_max": float(s_exo.max()),
           "best_epoch": best_epoch if val is not None else None,
           "val_mse_best": None if val is None else round(best, 6),
           # ⚠ **「0 と予測した」ときの検証 MSE**（尺度は s_endo で割った後）。これを下回らなければ何も学んでいない
           "val_mse_zero": None if val is None else round(float((val[2] ** 2).mean()), 6),
           "val_mse_by_epoch": history,
           "exog_weight": (None if net.exog is None
                           else [round(float(w), 6) for w in net.exog.weight.detach().cpu().reshape(-1)])}
    ctx[DOC_KEY] = doc
    return pred
