"""前置きの門（rules.md 14-5）: 確率に情報が無い手法は閾値売買を回さない。

指標はどちらも**訓練分割の内側の tail holdout**（較正の fit と同じ場所。13-2 の 2）から計算する。
⚠ **検証 fold には特徴量にも触れない。** だから門は検証データの選別にならず、
⚠ **門前で落とした手法は n_trials に数えない**（validation-power.md §5-4 問 2。2026-09-11 利用者決定）。
⚠ **門の値は採否に使わない。** 使うのは「回すかどうか」だけ（判定に混ぜると門自体が選べた自由度になる）。
⚠ **門は (A) プール形式で 1 回だけ計算する**（§5-1。門を通らない手法は (B) も回さない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail import registry
from ail.models import calibrate
from ail.models.holdout import tail_holdout
from ail.validation import splits

# ⚠ **水準は事前固定**（2026-09-11 の利用者決定。rules.md 14-5）。**結果を見て動かさない。**
# 動かすなら、動かした理由と数を rules.md 14-5 に追記してから
AUC_MIN = 0.52
WIDTH_MIN_PT = 20.0


def is_gated(name: str) -> bool:
    """門に掛ける手法か。基準線（「基準 」・乱択）は掛けない（採否の対象ではない）。"""
    return not (name.startswith("基準 ") or name == "乱択（基準）")


def _auc(target, pred) -> float | None:
    """holdout AUC。片側ラベルでは定義できない（None。⚠ 0.5 で埋めない）。"""
    from sklearn.metrics import roc_auc_score

    up = np.asarray(target, dtype=float) > 0
    if up.all() or (~up).all():
        return None
    return float(roc_auc_score(up, np.asarray(pred, dtype=float)))


def _median(vals) -> float | None:
    xs = [x for x in vals if x is not None]
    return round(float(np.median(xs)), 4) if xs else None


def evaluate_gate(panel: pd.DataFrame, feats: list[str], exp: dict, run=None) -> dict:
    """手法ごとに門の 2 指標を測り、通過を判定する。

    fold の切れ目は `evaluate_trading` と同じ日付基準（13-6 の 1）。各 fold の**訓練分割だけ**を
    使い、選別 → tail holdout の頭で fit → 尻の予測から AUC と（Platt を当てた）買い% の
    p05-p95 幅を測る。5 fold の中央値が **AUC ≥ 0.52 かつ 幅 ≥ 20 点** なら通過。
    ⚠ **holdout が 1 fold も切れない（訓練が薄い）指標は素通し**（回せば普通に試行として
    数えるので、DSR が甘くなる向きではない）。
    """
    v = exp.get("validation", {})
    k = int(exp.get("k", 8))
    ctx = {"seed": int(v.get("seed", 0)), **exp.get("model_args", {})}
    model = registry.resolve("model", exp.get("model", "Ridge"))
    selectors = registry.resolve_all("selector", exp["selectors"])
    methods = {n: fn for n, fn in selectors.items() if is_gated(n)}

    per: dict[str, dict[str, list]] = {n: {"auc": [], "width": []} for n in methods}
    edges = splits.date_edges(panel["ts"], int(v.get("folds", 5)))
    for _f, tr, _te in splits.folds_by_dates(panel, edges, float(exp["horizon_min"]),
                                             int(v.get("embargo_bars", 0)),
                                             float(exp.get("bar_minutes", 0.0))):
        # ⚠ te には特徴量にも触れない（14-5。門が検証データの選別にならない根拠）
        sc = StandardScaler().fit(tr[feats])
        Xtr = pd.DataFrame(sc.transform(tr[feats]), columns=feats)
        ytr = np.asarray(tr["y"].values, dtype=float)
        (Xh, yh), holdout = tail_holdout(Xtr, ytr)
        if holdout is None:
            continue
        Xv, yv = holdout
        for n, fn in methods.items():
            cols = fn(Xtr, ytr, k, ctx)                # 選別は較正と同じく訓練全体で（13-2 の 2）
            pred = np.asarray(model(Xh[cols], yh, Xv[cols], ctx), dtype=float)
            per[n]["auc"].append(_auc(yv, pred))
            buy = calibrate.fit_from_predictions(pred, yv, "holdout").buy_pct(pred)
            per[n]["width"].append(round(float(np.percentile(buy, 95) - np.percentile(buy, 5)), 4))

    methods_doc: dict[str, dict] = {}
    passed: list[str] = []
    blocked: list[str] = []
    for n in methods:
        auc, width = _median(per[n]["auc"]), _median(per[n]["width"])
        ok = (auc is None or auc >= AUC_MIN) and (width is None or width >= WIDTH_MIN_PT)
        d = {"auc": auc, "width_pt": width, "auc_folds": per[n]["auc"],
             "width_folds": per[n]["width"], "passed": ok}
        if auc is None and width is None:
            d["注記"] = "⚠ holdout が切れず計算できない（訓練が薄い）。素通し"
        methods_doc[n] = d
        (passed if ok else blocked).append(n)
        if run is not None:
            fmt_a = "—" if auc is None else f"{auc:.3f}"
            fmt_w = "—" if width is None else f"{width:.1f}"
            run.log(f"  門 {n}: holdout AUC {fmt_a} / 買い% 幅 {fmt_w} 点 → "
                    + ("通過" if ok else "⚠ **門前**"))
    return {"auc_min": AUC_MIN, "width_min_pt": WIDTH_MIN_PT, "form": "shared",
            "methods": methods_doc, "passed": passed, "blocked": blocked,
            "注記": "⚠ 訓練内 holdout の fold 中央値。門は (A) プール形式で 1 回だけ測り、"
                    "値は採否に使わない・門前は n_trials に数えない（rules.md 14-5）"}
