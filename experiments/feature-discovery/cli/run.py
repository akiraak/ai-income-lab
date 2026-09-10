"""実験 1 本を回す → `runs/`。⚠ **走り出す前に config の名前を全部 resolve する**。

    python3 -m cli.run --experiment own_only_h1
    python3 -m cli.run --experiment own_only_h1 --leak      # ⚠ 配線の検査（跳ね上がるはず）

⚠ **設計の要点は 3 つ**（rules.md 9 章）。
  1. ⚠ **特徴量の選別は訓練分割の内側だけで行う**（Ambroise-McLachlan 2002）
  2. ⚠ **パージとエンバーゴを入れる**（ラベルが未来 k 本を跨ぐので、訓練の末尾は捨てる）
  3. ⚠ **良い数字より先にコストを引く**（E8 §1-4。往復 5〜10bp）
"""

from __future__ import annotations

import argparse
import json
import os
import warnings

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail import config, registry, runs
from ail.contracts import META_COLUMNS
from ail.data import store
from ail.validation import checks, metrics
import ail.bootstrap  # noqa: F401

warnings.filterwarnings("ignore")


def load_panel(experiment: str, period: str, leak: bool) -> tuple[pd.DataFrame, str]:
    path = os.path.join(store.DATA, "features",
                        experiment + ("_leak" if leak else ""), f"{period}.parquet")
    if not os.path.exists(path):
        raise SystemExit(f"{os.path.relpath(path, store.ROOT)} が無い。先に `python3 -m cli.build` を回す")
    return pd.read_parquet(path), path


def evaluate(panel: pd.DataFrame, feats: list[str], exp: dict, run: runs.Run) -> pd.DataFrame:
    v = exp.get("validation", {})
    seed = int(v.get("seed", 0))
    k = int(exp.get("k", 8))
    cost_bp = float(exp.get("cost_bp", 5.0))
    horizon_min = float(exp["horizon_min"])
    ctx = {"seed": seed, **exp.get("model_args", {})}

    split = registry.resolve("split", v.get("split", "walk_forward"))
    selectors = registry.resolve_all("selector", exp["selectors"])
    baselines = registry.resolve_all("model", exp.get("baselines", []))
    model = registry.resolve("model", exp.get("model", "Ridge"))

    out: list[dict] = []
    picked: list[dict] = []
    for f, tr, te in split(panel, int(v.get("folds", 5)), horizon_min,
                           int(v.get("embargo_bars", 0)), float(exp.get("bar_minutes", 0.0))):
        yte = te["y"].values
        # ⚠ モデルを使わない基準線を先に測る。**これを超えない予測は「何も学んでいない」。**
        for bname, bfn in baselines.items():
            p = bfn(tr, te, feats, ctx)
            g = float(np.mean(p * yte))
            out.append({"手法": f"基準 {bname}", "fold": f, "選んだ本数": 0,
                        "的中率": float(np.mean(np.sign(p) == np.sign(yte))), "IC": 0.0,
                        "粗利bp": g * 1e4, "純利bp": g * 1e4 - cost_bp})

        # ⚠ 標準化は**訓練分割の内側で fit**（rules.md 3 章の B。全期間で fit すると漏れる）
        sc = StandardScaler().fit(tr[feats])
        Xtr = pd.DataFrame(sc.transform(tr[feats]), columns=feats)
        Xte = pd.DataFrame(sc.transform(te[feats]), columns=feats)
        ytr = tr["y"].values
        for name, fn in selectors.items():
            cols = fn(Xtr, ytr, k, ctx)                 # ⚠ 選別は訓練の内側だけ
            p = model(Xtr[cols], ytr, Xte[cols], ctx)
            out.append({"手法": name, "fold": f, "選んだ本数": len(cols),
                        **metrics.score(p, yte, cost_bp)})
            # ⚠ **何を選んだかを残す。** ⚠ **偽薬を選んだ割合が、そのまま偽発見率の実測になる**
            picked.extend({"手法": name, "fold": f, "列": c} for c in cols)
        run.log(f"  fold {f}: 訓練 {len(tr):,} / 検証 {len(te):,}")
    run.selected(pd.DataFrame(picked))
    return pd.DataFrame(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--leak", action="store_true", help="⚠ 配線の検査（未来を混ぜた表を使う）")
    ap.add_argument("--sample", type=int, default=60000, help="行が多いとき間引く（0 で間引かない）")
    ap.add_argument("--layer", default="adjusted", help="記録に残すだけ（表は cli.build が作る）")
    args = ap.parse_args()

    exp = config.resolve_experiment(args.experiment)     # ⚠ ここで名前を全部解決する
    ds = exp["_dataset"]
    panel, path = load_panel(args.experiment, ds["period"], args.leak)
    feats = [c for c in panel.columns if c not in META_COLUMNS]
    exp.setdefault("horizon_min", 1440.0 if ds["period"] == "d" else float(exp["horizon"]))

    name = args.experiment + ("_leak" if args.leak else "")
    run = runs.Run(name, {k: v for k, v in exp.items() if not k.startswith("_")},
                   int(exp.get("validation", {}).get("seed", 0)))
    # ⚠ **層は表の隣の sidecar を正とする。** `--layer` は自己申告なので、
    # ⚠ **食い違ったらここで言う**（黙って通すと台帳の「データの層」が嘘になる）
    meta = _features_meta(path)
    if meta and meta.get("layer") and meta["layer"] != args.layer:
        run.log(f"⚠ **--layer {args.layer} だが、表は層 {meta['layer']} から作られている**"
                f"（{os.path.basename(path)}）。⚠ **sidecar のほうを記録に残す。**")
    run.inputs({"features_file": os.path.relpath(path, store.ROOT),
                "layer": (meta or {}).get("layer") or args.layer,
                "layer_declared": args.layer, "features_meta": meta,
                "rows_before_sample": int(len(panel)),
                "features": len(feats), "symbols": int(panel["symbol"].nunique()),
                "sample": args.sample,
                "data_manifest": {p: _digest(p, ds["period"]) for p in ("raw", "adjusted")}})

    full_panel = panel          # ⚠ **相関は間引く前で測る**（checks.py の注記）
    if args.sample and len(panel) > args.sample:
        panel = panel.iloc[:: max(1, len(panel) // args.sample)]
    leaky = [c for c in feats if c.startswith("LEAK")]
    layer = (meta or {}).get("layer") or args.layer
    run.log(f"実験 {args.experiment} / 層 {layer} / {ds['period']} 足")
    run.log(f"行 {len(panel):,} / 特徴量 {len(feats)}"
            + (f"  ⚠ **わざとした先読みの列あり: {leaky}**" if leaky else ""))

    res = evaluate(panel, feats, exp, run)
    g = (res.groupby("手法")
            .agg(本数=("選んだ本数", "mean"), 的中率=("的中率", "mean"), IC=("IC", "mean"),
                 粗利bp=("粗利bp", "mean"), 純利bp=("純利bp", "mean"), fold数=("fold", "size"))
            .sort_values("純利bp", ascending=False).round(4))
    run.log("")
    run.log(g.to_string())
    run.log(f"\n⚠ 純利 = 粗利 − コスト {exp.get('cost_bp', 5.0)}bp。⚠ **正でなければその手法は使えない。**")
    run.result(res, g)

    # ⚠ **検査はここで 1 度だけ計算して記録に残す**（管理画面は読むだけ。プラン §2）
    # ⚠ **モデルが Ridge 以外なら「全部使う × モデル」も 1 試行**（plans/archive/gpu-models.md §3-4）
    n_sel = len([x for x in exp.get("selectors", []) if not checks.is_baseline(x)])
    if exp.get("model", "Ridge") != "Ridge" and "全部使う（基準）" in exp.get("selectors", []):
        n_sel += 1
    doc = checks.compute(res, g, exp, panel=panel, full_panel=full_panel,
                         n_trials=checks.n_trials_now(n_sel), leak=args.leak)
    run.checks(doc)
    run.log(_checks_line(doc))
    print(f"→ {os.path.relpath(run.close(), store.ROOT)}")


def _checks_line(doc: dict) -> str:
    b, f = doc.get("best"), doc.get("folds")
    if not b:
        return ""
    parts = [f"最良 {b['method']} 純利 {b['純利bp']:+.2f}bp"]
    if f:
        parts.append(f"fold {f['positive']}/{f['folds']} {f['pattern']}")
    if (e := doc.get("edge_vs_drift")) and e.get("t") is not None:
        parts.append(f"上乗せ {e['mean_bp']:+.2f}bp t={e['t']:.2f}")
    if d := doc.get("dsr"):
        parts.append(f"DSR {d['DSR']:.4f}（実効 n {d['n_obs']:,} / {d['n_trials']} 試行）")
    return "検査: " + " ／ ".join(parts)


def _features_meta(path: str) -> dict | None:
    """`cli.build` が表の隣に書いた層の記録。⚠ **無ければ古い表（自己申告のまま）。**"""
    p = os.path.splitext(path)[0] + ".meta.json"
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _digest(layer: str, period: str = "d") -> str | None:
    try:
        return store.manifest_digest(layer, period)
    except Exception:
        return None


if __name__ == "__main__":
    main()
