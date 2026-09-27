"""損切りを「出口だけを言うモデル」として検知器に載せる（[記録](../../../../docs/specs/experiments/stoploss-as-model.md) §0）。

⚠ **動かす軸は出口だけ。** 入口は 4 つの形で**同じ 1 本**（T1 と同じ `own` 35 列 → Ridge → Platt。学習の対象は 1 日の `y`）。
⚠ **`own_trend*`（窓 20 の 6 列）は入口に入れない** — 損切りの規則が読むためだけに表に入っている。

| 形 | 出口% | 位置（買値）を知るか |
| --- | --- | --- |
| L0 入口のみ | 100 − 入口%（検知器は 2 つ返す ＝ 1 出力の契約の退化。対照） | — |
| L1 高値からの下落 | `own_trend20_dd < log(1 − x/100)` なら 100・でなければ 0 | 知らない（高値） |
| L2 σ で割った下落 | `own_trend20_dd < −k × own_trend20_vol` なら 100・でなければ 0 | 知らない |
| L3 学ぶ出口 | 100 × P(y_fwd_10 ≤ 0)（`fwd.forward_gate(10)` の補数 ＝ rules.md 16-3 の 2） | 知らない |

⚠ **合わせ方は max(100 − 入口%, 損切りの出口%)** ＝ 本番の `unanimous`（出口は max ＝ どちらかが言ったら降りる）を
机上でそのまま写す。⚠ **損切りの出口だけにすると主モデルの出口が消え、本番と違う形を測ることになる。**
形 B（買値からの下落）は位置を知るので検知器では書けない → `simulate(stop_loss_pct=)`（`cli/run.py` の `[trading.position_exits]`）。

⚠ **水準は事前固定**（記録 §0-3。x ∈ {5, 10, 20}%・k ∈ {2, 4, 8}・窓 20）。⚠ **結果を見て足さない・動かさない**（rules.md 14-9）。
⚠ **手で置いた数値は 1 つずつが自由度**なので、水準の数だけ登録名を分け、全部 `n_trials` に数える。
⚠ **L1・L2 の出口% は 0 か 100 しか取らない**ので θ で結果が変わらないが、3 検証と数える（13-3 の 5）。
⚠ **fit は訓練分割の内側だけ**（標準化・学習器・較正。rules.md 3 章 B）。`LEAK_` の列は `feats` に入っていればそのまま入口に入る（13-10）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail.detectors import fwd
from ail.detectors.scale import _fit_calibration
from ail.features import labels, trend
from ail.registry import register

WINDOW = 20                 # ⚠ 高値・σ の窓（事前固定。60 ／ 200 は 16 章の出口に近づくので足さない）
LEARN_WINDOW = 10           # ⚠ L3 学ぶ出口の地平（K5 と同じ 10 営業日）
DD_LEVELS = (5, 10, 20)     # ⚠ L1 高値からの下落 %（事前固定。20 は実売買の警告の線）
VOL_LEVELS = (2, 4, 8)      # ⚠ L2 σ の倍数（事前固定。1 日 σ ≈ 1.5% なら 3 ／ 6 ／ 12% に当たる【推測】）

NAME_L0 = "L0 入口のみ（損切りなし）"
NAME_L3 = f"L3 先{LEARN_WINDOW}日の下げを学んで降りる"


def name_l1(x: int) -> str:
    return f"L1 高値{WINDOW}日から−{x}%で降りる"


def name_l2(k: int) -> str:
    return f"L2 高値{WINDOW}日からσの{k}倍で降りる"


def entry_columns(feats) -> list[str]:
    """入口が見る列 ＝ `own` の列（⚠ `own_trend*` と学習の対象 `y_fwd_*` は入れない）。`LEAK_` はそのまま入る。"""
    head = f"{trend.PREFIX}trend"
    return [c for c in feats if not c.startswith(head) and not c.startswith(labels.SCALE_PREFIX)]


def entry(tr: pd.DataFrame, te: pd.DataFrame, feats, ctx: dict):
    """入口 1 本: own 35 列 → 標準化 → `ctx["model"]`（Ridge）→ Platt（日付で切る holdout・1 営業日パージ）。学習の対象は `y`。"""
    cols = entry_columns(feats)
    if not cols:
        raise SystemExit("⚠ 入口の列が 1 本も無い（`feature_layers` に \"own\" が要る）")
    model = ctx["model"]
    sc = StandardScaler().fit(tr[cols])              # ⚠ 標準化も訓練の内側で fit（rules.md 3 章 B）
    Xtr = pd.DataFrame(sc.transform(tr[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    ytr = tr["y"].to_numpy(dtype=float)
    cal = _fit_calibration(model, Xtr, ytr, tr["ts"], 1, ctx)
    pred = model(Xtr, ytr, Xte, ctx)
    doc = {"target": "y", "columns": cols, "訓練の行": int(len(tr)),
           "上がる割合": round(float((ytr > 0).mean()), 4), **cal.doc}
    return np.asarray(cal.buy_pct(pred), dtype=float), doc


def _need(te: pd.DataFrame, col: str) -> np.ndarray:
    if col not in te:
        raise SystemExit(f"⚠ {col} が表に無い。`feature_layers` に \"trend\"・`[features] trend_windows = [{WINDOW}]` を入れる")
    return te[col].to_numpy(dtype=float)


def rule_drawdown(te: pd.DataFrame, x_pct: int) -> tuple[np.ndarray, dict]:
    """L1: 直近 WINDOW 日の最高値からの下落（対数）が log(1 − x/100) を割ったら 100。⚠ 学習しない。"""
    col = f"{trend.PREFIX}trend{WINDOW}_dd"
    dd = _need(te, col)
    line = float(np.log1p(-x_pct / 100.0))
    fire = np.where(np.isnan(dd), False, dd < line)
    return np.where(fire, 100.0, 0.0), {"rule": f"{col} < log(1 − {x_pct}/100) = {line:.5f}", "level_pct": x_pct}


def rule_drawdown_vol(te: pd.DataFrame, k: int) -> tuple[np.ndarray, dict]:
    """L2: 同じ下落を直近 WINDOW 日の 1 日 σ で割り、−k を割ったら 100。⚠ 学習しない。"""
    dd_col = f"{trend.PREFIX}trend{WINDOW}_dd"
    vol_col = f"{trend.PREFIX}trend{WINDOW}_vol"
    dd, vol = _need(te, dd_col), _need(te, vol_col)
    with np.errstate(invalid="ignore"):
        fire = np.where(np.isnan(dd) | np.isnan(vol) | (vol <= 0), False, dd < -k * vol)
    return np.where(fire, 100.0, 0.0), {"rule": f"{dd_col} < −{k} × {vol_col}", "level_sigma": k}


def learned_exit(tr: pd.DataFrame, te: pd.DataFrame, feats, ctx: dict) -> tuple[np.ndarray, dict]:
    """L3: 100 × P(y_fwd_10 ≤ 0) ＝ `fwd.forward_gate(10)` の買い% の補数（rules.md 16-3 の 2）。⚠ 見る列は入口と同じ own 35 列。"""
    buy_out, doc = fwd.forward_gate(LEARN_WINDOW, tr, te, entry_columns(feats), ctx)
    return 100.0 - np.asarray(buy_out, dtype=float), doc


def combine(entry_pct: np.ndarray, stop_pct: np.ndarray) -> np.ndarray:
    """出口% ＝ max(主モデルの出口 ＝ 100 − 入口%, 損切りの出口%)。⚠ 本番の `unanimous`（出口は max）と同じ。"""
    return np.maximum(100.0 - entry_pct, np.asarray(stop_pct, dtype=float))


@register("detector", NAME_L0)
def l0_entry_only(tr, te, feats, ctx):
    """⚠ 対照。2 つ返す ＝ 出口% は呼ぶ側が 100 − 入口% で補う（rules.md 16-1 の 4）。"""
    bp, doc = entry(tr, te, feats, ctx)
    return bp, {**doc, "出口": "100 − 入口%（損切りなし）"}


def _register_dd(x: int) -> None:
    @register("detector", name_l1(x))
    def _l1(tr, te, feats, ctx, _x=x):
        bp, doc = entry(tr, te, feats, ctx)
        stop, sdoc = rule_drawdown(te, _x)
        return bp, combine(bp, stop), {**doc, "出口": sdoc, "source": f"入口 {doc.get('source')} ／ 出口 none（規則）"}


def _register_ddvol(k: int) -> None:
    @register("detector", name_l2(k))
    def _l2(tr, te, feats, ctx, _k=k):
        bp, doc = entry(tr, te, feats, ctx)
        stop, sdoc = rule_drawdown_vol(te, _k)
        return bp, combine(bp, stop), {**doc, "出口": sdoc, "source": f"入口 {doc.get('source')} ／ 出口 none（規則）"}


for _x in DD_LEVELS:
    _register_dd(_x)
for _k in VOL_LEVELS:
    _register_ddvol(_k)


@register("detector", NAME_L3)
def l3_learned(tr, te, feats, ctx):
    bp, doc = entry(tr, te, feats, ctx)
    stop, sdoc = learned_exit(tr, te, feats, ctx)
    return bp, combine(bp, stop), {**doc, "出口": sdoc,
                                   "source": f"入口 {doc.get('source')} ／ 出口 {sdoc.get('source')}（補数）"}


ALL_NAMES = (NAME_L0, *[name_l1(x) for x in DD_LEVELS], *[name_l2(k) for k in VOL_LEVELS], NAME_L3)
