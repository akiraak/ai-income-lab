"""先 W 営業日を当てにいく検知器（[記録](../../../../docs/specs/experiments/forward10-target.md) §0）。

⚠ **動かす軸は 1 つだけ — 学習の対象。** 実売買の 3 人（T1〜T3）は「明日の符号」（`y`）を学ぶ。
ここでは ⚠ **同じ表の説明変数を全部そのまま**使い、⚠ **先 W 営業日の累積対数リターン `y_fwd_W`** を学ぶ。
買い% = P(y_fwd_W > 0) の Platt 較正。損益の対象（シミュレータ）は 1 日の `y` のまま（rules.md 15-3）。

⚠ **`scale.py`（D1〜D4）との違いは見る数字だけ**: D は `trend` 層の 6 列（そのスケールのものだけ）、
ここは ⚠ **表の説明変数 全部**（`own` 35 ／ `ownex` 124 ／ `ownseq` 95 ＝ 「いまの 3 本が見ている数字」）。
⚠ **既存の `SCALES`・D1〜D4・C1〜C3 は 1 文字も変えない**（登録名は記録の識別項目 ＝ rules.md 10-1）。

⚠ **数値は 1 つも手で置かない**（rules.md 14-9）: 事前固定するのは 窓 W・列 ＝ 全部・出力が買い% 1 本 だけ。
学習器は config の `model`（Ridge ／ LightGBM）を `ctx["model"]` で受け取る（T1〜T3 の検知器と同じ形）。

⚠ **fit は訓練分割の内側だけ**（rules.md 3 章 B）: 標準化・学習器・較正のすべて。
⚠ **較正の holdout は日付で切り、W 営業日ぶん（暦 1.5 倍）パージする**（15-5。`scale._fit_calibration` を共用）。
⚠ **`LEAK_` の列は `feats` に入っていればそのまま説明変数に入る**（13-10 の配線の検査。`LEAK_fwd_W` で跳ねる）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail.detectors.scale import _fit_calibration
from ail.features import labels, trend
from ail.registry import register

# ⚠ **窓は事前固定**（K4）。5 日（cGAN）と 20 日（D1）のあいだの空白。増やすときは新しい登録名で足す
WINDOW = 10
# ⚠ **2026-10-18 に先 5 ／ 20 ／ 40 日を新しい登録名で足した**（rules.md 25 章 ＝ 買い係・売り係で先の期間を分ける人。
#    記録 horizon-roles-trader.md §0）。⚠ `WINDOW`・`H1 …` は 1 文字も変えない（登録名は記録の識別項目 ＝ rules.md 10-1）
EXTRA_WINDOWS = {"H2": 5, "H3": 20, "H4": 40}
# ⚠ **列の絞り**（rules.md 25-1 の 2）: `ctx["fwd_columns"]`（config の `[model_args] fwd_columns`）が "own" なら、
#    損切りの規則のためだけに表に入っている高値 20 日の列（`own_trend*`）を見ない ＝ 入口と同じ own 35 列。
#    無ければ今までどおり表の全列（⚠ 項目の無い config は 1 ビットも変わらない ＝ 指紋テスト）
COLUMNS_OWN = "own"


def forward_gate(w: int, tr: pd.DataFrame, te: pd.DataFrame, feats, ctx: dict, center: bool = False):
    """先 w 営業日のゲート。⚠ **戻り値は買い%（0〜100・検証の行数ぶん）と、記録に残す辞書**（rules.md 15-1）。

    `center`（rules.md 25-7。2026-10-19）: 較正した確率 p から訓練の行の上がる割合 π を logit で引く ＝ 買い% ＝ 100 × σ(logit(p) − logit(π))。
    π と同じなら 50。⚠ 既定 False ＝ 既存の H1〜H5 は 1 ビットも変わらない（指紋テスト）。
    """
    target = f"{labels.SCALE_PREFIX}{w}"
    if target not in tr:
        raise SystemExit(f"⚠ {target} が表に無い。config の `label_scales` に {w} を入れて `cli.build` し直す")
    # ⚠ **説明変数は表の全部**（`feats` は呼ぶ側が META を外して渡す ＝ `y_fwd_` は接頭辞ごと外れている）
    cols = [c for c in feats if not c.startswith(labels.SCALE_PREFIX)]
    which = ctx.get("fwd_columns")
    if which == COLUMNS_OWN:
        cols = [c for c in cols if not c.startswith(f"{trend.PREFIX}trend")]   # ⚠ `LEAK_` の列はそのまま残る（13-10）
    elif which not in (None, "all"):
        raise SystemExit(f"⚠ fwd_columns = {which!r} は受けない（\"own\" か \"all\"。rules.md 25-1 の 2）")
    # ⚠ **末尾はラベルが取れない行**（表の尻）。訓練からだけ落とす（検証の行はラベルを使わない）
    ok = tr[target].notna().to_numpy()
    tr_ok = tr.loc[ok]
    model = ctx["model"]
    sc = StandardScaler().fit(tr_ok[cols])           # ⚠ 標準化も訓練の内側で fit（rules.md 3 章 B）
    Xtr = pd.DataFrame(sc.transform(tr_ok[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    ytr = tr_ok[target].to_numpy(dtype=float)
    cal = _fit_calibration(model, Xtr, ytr, tr_ok["ts"], w, ctx)   # ⚠ w 営業日ぶんパージした holdout で較正
    pred = model(Xtr, ytr, Xte, ctx)
    base = float((ytr > 0).mean())
    doc = {"scale": w, "target": target, "columns": cols, "fwd_columns": which or "all",
           "訓練の行": int(len(tr_ok)), "上がる割合": round(base, 4), **cal.doc}
    buy = cal.buy_pct(pred)
    if center:
        buy = center_by_base(buy, base)
        doc = {**doc, "中心化": True, "土台": round(base, 4)}
    return buy, doc


def center_by_base(buy_pct, base: float):
    """土台を引いた買い%（rules.md 25-7 の 1）: 100 × σ(logit(p) − logit(π))。p ＝ 買い% ／ 100 を [0.005, 0.995] に収めてから。"""
    p = np.clip(np.asarray(buy_pct, dtype=float) / 100.0, 0.005, 0.995)
    pi = min(max(float(base), 0.005), 0.995)
    z = np.log(p / (1.0 - p)) - np.log(pi / (1.0 - pi))
    return 100.0 / (1.0 + np.exp(-z))


@register("detector", f"H1 先{WINDOW}日ゲート（全列・学習）")
def h1_forward10(tr, te, feats, ctx):
    return forward_gate(WINDOW, tr, te, feats, ctx)


def name_of(tag: str, w: int) -> str:
    return f"{tag} 先{w}日ゲート（全列・学習）"


def _register_extra(tag: str, w: int) -> None:
    @register("detector", name_of(tag, w))
    def _h(tr, te, feats, ctx, _w=w):
        return forward_gate(_w, tr, te, feats, ctx)


for _tag, _w in EXTRA_WINDOWS.items():
    _register_extra(_tag, _w)

EXTRA_NAMES = tuple(name_of(t, w) for t, w in EXTRA_WINDOWS.items())

# ⚠ **25-7（2026-10-19）: 土台を引いた買い%。** H1〜H4 と同じ学び方・同じ較正で、出力だけを π で中心化した別の登録名。
CENTERED_WINDOWS = {"H1": WINDOW, **EXTRA_WINDOWS}


def centered_name_of(tag: str, w: int) -> str:
    return f"{tag} 先{w}日ゲート（全列・学習・土台差）"


def _register_centered(tag: str, w: int) -> None:
    @register("detector", centered_name_of(tag, w))
    def _h(tr, te, feats, ctx, _w=w):
        return forward_gate(_w, tr, te, feats, ctx, center=True)


for _tag, _w in CENTERED_WINDOWS.items():
    _register_centered(_tag, _w)

CENTERED_NAMES = tuple(centered_name_of(t, w) for t, w in CENTERED_WINDOWS.items())



# ⚠ **25-5（2026-10-18）: 1 本で「買い」と「何日持つか」を出す検知器。** 4 本の先 W 日ゲートを同じ学び方で持ち、
#    買い% ＝ 4 本の最大・保有日数 ＝ その期間（同点は短いほう）・出口% ＝ 0（売り係を持たない）。
#    ⚠ `forward_gate` は 1 文字も変えない（H1〜H4 の行の指紋）。期間の候補は足さない（25-5 の 8）
PICK_WINDOWS = (5, 10, 20, 40)
PICK_NAME = "H5 先5〜40日から期間を選ぶゲート（全列・学習）"
HOLD_KEY = "_hold_days"      # ⚠ `fold_buy_pct` が保有日数を置く ctx の項目名。呼んだ側が `drain_holds` で取り出す（25-5 の 2）


@register("detector", PICK_NAME)
def h5_pick(tr, te, feats, ctx):
    """⚠ **4 つ返す**（買い%, 出口%, 保有日数, 記録）＝ rules.md 25-5 の 2。"""
    preds, docs = [], {}
    for w in PICK_WINDOWS:
        bp, doc = forward_gate(w, tr, te, feats, ctx)
        preds.append(np.asarray(bp, dtype=float))
        docs[str(w)] = doc
    stack = np.vstack(preds)                                  # 期間 × 行
    pick = np.argmax(stack, axis=0)                           # ⚠ 同点は先頭 ＝ 短いほう
    buy = stack[pick, np.arange(stack.shape[1])]
    hold = np.asarray(PICK_WINDOWS, dtype=int)[pick]
    counts = {str(w): int((hold == w).sum()) for w in PICK_WINDOWS}
    doc = {"windows": list(PICK_WINDOWS), "columns": docs[str(PICK_WINDOWS[0])]["columns"],
           "fwd_columns": docs[str(PICK_WINDOWS[0])]["fwd_columns"],
           "選んだ期間の分布": counts, "per_window": docs}
    return buy, np.zeros(len(buy)), hold, doc


def drain_holds(ctx: dict) -> dict:
    """`fold_buy_pct` が ctx に置いた 手法 → 保有日数 を取り出して消す（⚠ 取り残すと fold をまたいで混ざる）。"""
    return ctx.pop(HOLD_KEY, None) or {}
