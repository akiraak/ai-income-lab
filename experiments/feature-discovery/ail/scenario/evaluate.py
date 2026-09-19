"""起点ごとの成績の表と、採用基準の判定（記録 `cgan-scenario.md` §3-2。⚠ **規則は結果を見る前に固定**）。

表は 1 行 ＝ (fold, 起点, モデル, 種)。モデルは `cgan` / `hist` / `light` / `ridge`。出せない指標は NaN（＝ 対象外）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.scenario import metrics

LEVELS = tuple(int(p * 100) for p in metrics.INTERVALS)


def per_origin_from_samples(samples_5d: np.ndarray, y5: np.ndarray) -> dict:
    """5 日累積リターンの標本 `[n, m]` と実測 `[n]` → 起点ごとの列。"""
    out = {"crps": metrics.crps_samples(samples_5d, y5), "prob_up": (samples_5d > 0).mean(axis=1),
           "median": np.median(samples_5d, axis=1)}
    for p, lv in zip(metrics.INTERVALS, LEVELS):
        lo, hi = np.quantile(samples_5d, [(1 - p) / 2, (1 + p) / 2], axis=1)
        out[f"in_{lv}"] = ((y5 >= lo) & (y5 <= hi)).astype(float)
        out[f"width_{lv}"] = hi - lo
    return out


def per_origin_from_paths(paths: np.ndarray, Y: np.ndarray) -> dict:
    """シナリオ `[n, m, horizon]` と実測 `[n, horizon]`（日次対数）→ 起点ごとの列（下落リスクつき）。"""
    out = per_origin_from_samples(metrics.cumulative_returns(paths)[..., -1], metrics.cumulative_returns(Y)[..., -1])
    out["prob_drop"] = metrics.drop_event(paths).mean(axis=1)
    return out


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """(モデル, 種, fold) ごとのまとめ ＋ 5 塊を合わせた行（fold = "all"）。⚠ **CRPS は低いほどよい。**"""
    rows = []
    for (model, seed), g in df.groupby(["model", "seed"], dropna=False):
        for fold, h in [*g.groupby("fold"), ("all", g)]:
            up, drop = (h["y5"] > 0).astype(float), h["drop_real"].astype(float)
            row = {"model": model, "seed": seed, "fold": fold, "n": len(h), "crps": h["crps"].mean(),
                   "brier_up": metrics.brier(h["prob_up"], up) if h["prob_up"].notna().all() else np.nan,
                   "mae_median": (h["median"] - h["y5"]).abs().mean(),
                   "brier_drop": metrics.brier(h["prob_drop"], drop) if h["prob_drop"].notna().all() else np.nan,
                   "drop_events": int(drop.sum())}
            for lv in LEVELS:
                row[f"cover_{lv}"], row[f"width_{lv}"] = h[f"in_{lv}"].mean(), h[f"width_{lv}"].mean()
            rows.append(row)
    return pd.DataFrame(rows)


def judge(df: pd.DataFrame, ev: dict, flagged: list[str]) -> dict:
    """採用基準 (a)〜(e)。`flagged` ＝ 選ばれた checkpoint に崩壊・極端な値の印が付いた (fold, 種) の名前。"""
    folds = sorted(df["fold"].unique())
    key = ["fold", "origin"]
    hist = df[df["model"] == "hist"].set_index(key)["crps"]
    light = df[df["model"] == "light"].set_index(key)["crps"]
    gan = df[df["model"] == "cgan"]
    by_seed = {int(s): g.set_index(key)["crps"] for s, g in gan.groupby("seed")}
    mean_gan = pd.concat(by_seed.values(), axis=1).mean(axis=1)        # 起点ごとに種の平均
    d = (mean_gan - hist.loc[mean_gan.index]).sort_index(level="origin")
    a = metrics.block_bootstrap_mean(d.to_numpy(), int(ev["block_len"]), int(ev["n_boot"]), int(ev["boot_seed"]))
    thin = d.to_numpy()[::5]                                             # 補助: 5 営業日おきの重ならない起点
    a["non_overlapping"] = {"n": int(len(thin)), "mean": float(thin.mean()),
                            "t": float(thin.mean() / (thin.std(ddof=1) / np.sqrt(len(thin))))}
    per_fold = {f: float(d.xs(f, level="fold").mean()) for f in folds}
    per_seed = {s: float((c - hist.loc[c.index]).mean()) for s, c in by_seed.items()}
    cover = {lv: float(gan.groupby("seed")[f"in_{lv}"].mean().mean()) for lv in (80, 95)}
    better = sum(v < 0 for v in per_fold.values())
    ok = {"a": a["mean"] < 0 and a["ci_high"] < 0,
          "b": better >= int(ev["min_blocks_better"]),
          "c": all(v < 0 for v in per_seed.values()),
          "d": (ev["coverage_80"][0] <= cover[80] <= ev["coverage_80"][1]
                and ev["coverage_95"][0] <= cover[95] <= ev["coverage_95"][1] and not flagged),
          "e": float(mean_gan.mean()) <= float(light.loc[mean_gan.index].mean())}
    if all(ok.values()):
        verdict = "採る"
    elif a["mean"] < 0 and better >= 3:
        verdict = "保留"
    else:
        verdict = "落とす"
    return {"verdict": verdict, "ok": ok, "a_diff_vs_hist": a, "b_per_fold": per_fold, "b_better": better,
            "c_per_seed": per_seed, "d_coverage": cover, "d_flagged": flagged,
            "e_crps": {"cgan": float(mean_gan.mean()), "light": float(light.loc[mean_gan.index].mean()),
                       "hist": float(hist.loc[mean_gan.index].mean())}}
