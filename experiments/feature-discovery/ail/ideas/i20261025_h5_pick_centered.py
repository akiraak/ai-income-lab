"""登録簿 `20261025-h5-pick-centered`（参考あり・組み合わせる。rules.md 31 章・記録 dev-loop.md §2）:
先 5 ／ 10 ／ 20 ／ 40 日のゲート 4 本を **土台を引いた買い%**（25-7 ＝ 100 × σ(logit(p_W) − logit(π_W))）で比べ、最大の期間を保有日数にする検知器。

⚠ 中身は `fwd.h5_pick`（25-5）と同じ流れで、`forward_gate(..., center=True)` を 4 回呼ぶだけ。既存の H1〜H5 は触らない（`center` の既定 False）。
⚠ 「土台差の最大」＝ logit(p_W) − logit(π_W) の最大（σ は単調）。期間の候補・π の取り方（訓練の行の割合）・θ は動かさない。
出力は 4 つ（買い%・出口% 0・保有日数・記録）＝ 25-5 の 2 の契約のまま。
"""

from __future__ import annotations

import numpy as np

from ail.detectors import fwd
from ail.registry import register

NAME = "H5 先5〜40日から期間を選ぶゲート（全列・学習・土台差）"


@register("detector", NAME)
def h5_pick_centered(tr, te, feats, ctx):
    preds, docs = [], {}
    for w in fwd.PICK_WINDOWS:
        bp, doc = fwd.forward_gate(w, tr, te, feats, ctx, center=True)
        preds.append(np.asarray(bp, dtype=float))
        docs[str(w)] = doc
    stack = np.vstack(preds)                                  # 期間 × 行
    pick = np.argmax(stack, axis=0)                           # ⚠ 同点は先頭 ＝ 短いほう（25-5 の 1 と同じ）
    buy = stack[pick, np.arange(stack.shape[1])]
    hold = np.asarray(fwd.PICK_WINDOWS, dtype=int)[pick]
    counts = {str(w): int((hold == w).sum()) for w in fwd.PICK_WINDOWS}
    first = docs[str(fwd.PICK_WINDOWS[0])]
    doc = {"windows": list(fwd.PICK_WINDOWS), "columns": first["columns"], "fwd_columns": first["fwd_columns"],
           "中心化": True, "土台": {w: docs[w]["土台"] for w in docs},
           "選んだ期間の分布": counts, "per_window": docs}
    return buy, np.zeros(len(buy)), hold, doc
