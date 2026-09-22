"""シナリオ予測の実行（`cli.scenario run`）から、表と図と再現の確認を出す（プラン §8 のテスト 6・§9 の納品物 4）。

    python3 -m cli.scenario_report --run runs/<実行> --out ../../docs/specs/experiments/assets

⚠ **学習はしない。** 読むのは `origins.csv`・`history.csv`・`fitted/*.pt` だけ。
⚠ **再現の確認**: 保存した重みを読み直し、同じ種で最後の塊のシナリオを作り直して、起点ごとの CRPS が `origins.csv` と合うかを見る。
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

from ail import runs
from ail.scenario import data, evaluate, figures, metrics, model
from cli.scenario import TEST_Z_SEED, _prepare


def tables(df: pd.DataFrame) -> tuple[dict, dict]:
    """(確率帯別の表, 被覆率)。cGAN は起点ごとに種の平均を取ってから帯に分ける。"""
    rel, cov = {}, {}
    for k in ("cgan", "hist", "light"):
        g = df[df["model"] == k].groupby(["fold", "origin"], sort=False).mean(numeric_only=True)
        rel[k] = metrics.reliability(g["prob_up"].to_numpy(), (g["y5"] > 0).to_numpy())
        cov[k] = {lv: float(g[f"in_{lv}"].mean()) for lv in evaluate.LEVELS}
    return rel, cov


def reproduce(run_dir: str, df: pd.DataFrame, fold: str, seed: int) -> dict:
    """保存 → 読込 → 同じ種で作り直し → `origins.csv` の CRPS と比べる。"""
    cfg = json.load(open(os.path.join(run_dir, "config.json"), encoding="utf-8"))
    _bars, _feats, names, s = _prepare(cfg)
    f = next(x for x in data.split(s, **cfg["split"]) if x.name == fold)
    gen, doc = model.load(os.path.join(run_dir, "fitted", f"{fold}_seed{seed}.pt"))
    sc = data.Scaler.from_dict(doc["scaler"], names)
    paths = sc.y_inverse(model.generate(gen, sc.x(s.X[f.test]), int(cfg["n_scenarios"]),
                                        seed=TEST_Z_SEED + seed).astype(np.float64))
    Y = s.Y[f.test].astype(np.float64)
    now = evaluate.per_origin_from_paths(paths, Y)["crps"]
    then = df[(df["model"] == "cgan") & (df["fold"] == fold) & (df["seed"] == seed)]["crps"].to_numpy()
    cum = metrics.cumulative_returns(paths)[..., -1]
    q = {k: np.quantile(cum, float(k) / 100, axis=1) for k in ("2.5", "10", "25", "50", "75", "90", "97.5")}
    return {"max_abs_diff": float(np.abs(now - then).max()), "mean_crps_now": float(now.mean()),
            "mean_crps_then": float(then.mean()), "n": int(len(now)), "device": str(next(gen.parameters()).device),
            "dates": s.origin[f.test].astype(str).tolist(), "q": q, "y5": metrics.cumulative_returns(Y)[:, -1]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True, help="図（SVG）を置くディレクトリ")
    ap.add_argument("--fold", default="f5")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    name = runs.resolve(args.run)                  # 実行の名前（記録は runs/research.sqlite）
    df = runs.read_csv(name, "origins.csv")
    rel, cov = tables(df)
    for k, rows in rel.items():
        print(f"== 上昇確率の帯（{figures.SERIES[k][0]}）")
        print(pd.DataFrame(rows).round(4).to_string(index=False))
    print("== 被覆率", json.dumps(cov, ensure_ascii=False))
    with runs.materialized(name) as d:             # ⚠ 重みを読むための写し（出たら消す）
        rep = reproduce(d, df, args.fold, args.seed)
    print(f"== 再現: {args.fold} 種 {args.seed}・{rep['n']} 起点・{rep['device']}  CRPS の平均 {rep['mean_crps_then']:.8f} → "
          f"{rep['mean_crps_now']:.8f}  起点ごとの差の最大 {rep['max_abs_diff']:.2e}")
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "cgan-calibration.svg"), "w", encoding="utf-8") as fh:
        fh.write(figures.calibration(rel, cov))
    with open(os.path.join(args.out, "cgan-intervals.svg"), "w", encoding="utf-8") as fh:
        fh.write(figures.intervals(rep["dates"], rep["q"], rep["y5"],
                                   f"cGAN・最後の塊 {args.fold}（{rep['dates'][0]}〜{rep['dates'][-1]}）・種 {args.seed}・実行 {name}"))
    print("図:", os.path.join(args.out, "cgan-calibration.svg"), os.path.join(args.out, "cgan-intervals.svg"))


if __name__ == "__main__":
    main()
