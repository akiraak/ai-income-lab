"""本物のリターンに既知の上乗せを埋めて、器と判定式がどこから「採る」を出すかを測る（登録簿 `20261108-signal-inject-power`）。⚠ **読むだけ。**

    python3 -m cli.inject_power [--experiment trade_own_ridge_a] [--seeds 5] [--json out.json] [--save]

`cli.planted_power`（登録簿 `20261106-planted-signal-power`）との違いは 1 つ: **リターンを合成（多変量正規）に差し替えず、本物の `y` のまま** 埋める
＝ 本物の太い裾・ボラの塊・fold ごとの偏りが残る（11/6 の版が「検出力を甘く見積もる側」と書いた穴）。

作り方（登録簿の structure・水準は回す前に固定）:
  - 本物の表（既定 `trade_own_ridge_a` ＝ own_2018・63 本）の行・特徴量の列・fold の切れ目をそのまま使う。
  - 表の外で作った乱数の列 `own_planted`（行ごとに独立な N(0,1) ＝ 本物の列と相関しない）を足し、
    `y` ← 本物の `y` ＋ k bp × 列の符号（訓練分割と評価分割の両方）。⚠ 同じ種では k をまたいで列が同じ（対の曲線）。
  - k の 3 段 ＝ 検出限界 L の 0.1 倍 ／ 1 倍 ／ 10 倍。**L ＝ 300 bp/fold ÷ 評価期間 1 つの営業日数の平均**
    （契約 §2「机上で測れるのは数百 bp/fold の上乗せ」の「数百」を 300 と読んだ ＝ 2026-11-13 回す前に固定。日数は表の切れ目から数える）。
  - 各段 × θ 50 ／ 55 ／ 60 × 種 5。器 ・ 純利 ・ 判定は `cli.planted_power.one` をそのまま呼ぶ
    （素の own × Ridge ＝ `全部使う（基準）`・形式 (A)・B&H ＝ 買い% 100 の同じ器・採る ＝ 平均 > 0 で fold 全部が正）。

⚠ **検証結果一覧に行を作らない・`n_trials` に数えない・`runs/` を作らない**（偽の信号の行 ＝ leak 対照 ・ 基準線と同じ扱い）。
⚠ `--save` のときだけ DB の `outputs` の `diag/<時刻>_inject_power/` に JSON を入れる（`runs.put_output`・上書きしない）。
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from ail import config, runs
from ail.contracts import META_COLUMNS
from ail.validation import splits
from cli import planted_power as pp

LIMIT_BP_PER_FOLD = 300.0          # ⚠ 事前固定（契約 §2 の「数百 bp/fold」）
MULTIPLES = (0.1, 1.0, 10.0)       # ⚠ 事前固定（検出限界の 0.1 倍 ／ 1 倍 ／ 10 倍）
THETAS = (50.0, 55.0, 60.0)        # ⚠ 事前固定
SEEDS = 5                          # ⚠ 事前固定


def inject(panel: pd.DataFrame, k_bp: float, seed: int) -> pd.DataFrame:
    """本物の y に k bp × 乱数の列の符号を足した写し。⚠ 種が同じなら k によらず列は同じ。"""
    x = np.random.default_rng([int(seed), 20261108]).standard_normal(len(panel))
    y = panel["y"].to_numpy(dtype=float) + float(k_bp) * 1e-4 * np.sign(x)
    out = panel.assign(y=y, **{pp.PLANTED: x})
    if "y_sign" in out:
        out["y_sign"] = np.sign(y)
    return out


def eval_days(panel: pd.DataFrame, exp: dict, edges_dates) -> list[int]:
    v = exp.get("validation", {})
    return [int(te["ts"].nunique()) for _f, _tr, te in splits.folds_by_dates(
        panel, edges_dates, float(exp["horizon_min"]), int(v.get("embargo_bars", 0)), float(exp.get("bar_minutes", 0.0)))]


def levels(days: list[int]) -> tuple[float, list[float]]:
    """(検出限界 L〔bp/日〕, k の 3 段〔bp/日〕)。"""
    L = LIMIT_BP_PER_FOLD / float(np.mean(days))
    return L, [m * L for m in MULTIPLES]


def curve(experiment: str = "trade_own_ridge_a", seeds: int = SEEDS, thetas=THETAS, log=print) -> dict:
    from cli.run import load_panel

    exp = config.resolve_experiment(experiment)
    ds = exp["_dataset"]
    exp.setdefault("horizon_min", 1440.0 if ds["period"] == "d" else float(exp["horizon"]))
    panel, path = load_panel(experiment, ds["period"], False, exp.get("features_from"))
    feats = [c for c in panel.columns if c not in META_COLUMNS] + [pp.PLANTED]
    edges_dates = splits.date_edges(panel["ts"], int(exp.get("validation", {}).get("folds", 5)),
                                    splits.cut_offset(exp.get("validation", {})))
    days = eval_days(panel, exp, edges_dates)
    L, ks = levels(days)
    out = {"experiment": experiment, "table": path, "rows": len(panel), "symbols": int(panel["symbol"].nunique()),
           "eval_days_per_fold": days, "limit_bp_per_fold": LIMIT_BP_PER_FOLD, "limit_bp_per_day": L, "multiples": list(MULTIPLES),
           "k_bp": ks, "thetas": list(thetas), "seeds": seeds, "runs": [], "summary": []}
    log(f"評価期間の営業日数 {days}（平均 {np.mean(days):.0f}）→ 検出限界 L ＝ {L:.3f} bp/日・k ＝ " + " ／ ".join(f"{k:.3f}" for k in ks))
    t0 = time.perf_counter()
    for mult, k in zip(MULTIPLES, ks):
        tables = [inject(panel, k, seed) for seed in range(seeds)]
        for th in thetas:
            got = []
            for seed, tab in enumerate(tables):
                r = pp.one(tab, feats, exp, edges_dates, th)
                r.update(k_bp=float(k), multiple=float(mult), theta=float(th), seed=seed)
                got.append(r)
                out["runs"].append(r)
            e = np.array([np.nanmean(r["edges"]) for r in got])
            vs = [r["verdict"] for r in got]
            row = {"multiple": float(mult), "k_bp": float(k), "theta": float(th), "採る": vs.count("採る") / seeds,
                   "保留": vs.count("保留") / seeds, "落とす": vs.count("落とす") / seeds, "edge_mean_bp": float(np.mean(e)),
                   "edge_min_bp": float(np.min(e)), "edge_max_bp": float(np.max(e)),
                   "corr_buy_planted": float(np.mean([r["corr_buy_planted"] for r in got]))}
            out["summary"].append(row)
            log(f"k {k:>6.3f} bp/日（L の {mult:g} 倍）・θ {th:g}: 採る {row['採る']:.0%} ／ 保留 {row['保留']:.0%} ／ 落とす {row['落とす']:.0%}・"
                f"上乗せ 平均 {row['edge_mean_bp']:+.0f}bp/fold（{row['edge_min_bp']:+.0f}〜{row['edge_max_bp']:+.0f}）・"
                f"買い%×列 ρ {row['corr_buy_planted']:.3f}・{time.perf_counter() - t0:.0f} 秒")
    out["seconds"] = time.perf_counter() - t0
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="本物のリターンに既知の上乗せを埋めて検出力を測る（読むだけ・n_trials に数えない）")
    p.add_argument("--experiment", default="trade_own_ridge_a")
    p.add_argument("--seeds", type=int, default=SEEDS)
    p.add_argument("--json")
    p.add_argument("--save", action="store_true", help="DB の outputs の diag/<時刻>_inject_power/ にも入れる")
    a = p.parse_args()
    doc = curve(a.experiment, a.seeds)
    text = json.dumps(doc, ensure_ascii=False, indent=1, default=float)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text)
    if a.save:
        print("→ " + runs.put_output(f"diag/{time.strftime('%Y-%m-%dT%H-%M-%S')}_inject_power/inject_power.json", text))


if __name__ == "__main__":
    main()
