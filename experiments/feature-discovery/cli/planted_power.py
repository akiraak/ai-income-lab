"""既知の強さの信号を埋めた合成データで、器と判定式の検出力の曲線を測る（登録簿 `20261106-planted-signal-power`）。⚠ **読むだけ。**

    python3 -m cli.planted_power [--experiment trade_own_ridge_a] [--strengths 0,0.1,1,5,20] [--seeds 20] [--theta 50]
                                 [--json out.json] [--save]

作り方（登録簿の structure・水準は事前固定）:
  - 本物の表（既定 `trade_own_ridge_a` ＝ own_2018・63 本・2018-01-31〜）の 日付・銘柄・行・特徴量の列・fold の切れ目をそのまま使う。
  - `y` だけを合成に差し替える: 日ごとに 多変量正規（平均 ＝ 銘柄ごとの本物の平均・共分散 ＝ 本物の `y` の共分散〔重なり 250 日未満の組は 0・
    固有値を 0 以上に切る〕）＋ 既知の列 `own_planted`（行ごとに独立な N(0,1)）× s bp。
    ⚠ 同じ種では s をまたいで雑音と既知の列が同じ（対の曲線 ＝ s の差だけが効く）。
  - 器 ＝ 素の own × Ridge（`全部使う（基準）`・形式 (A)・θ）を `cli.run.fold_buy_pct` で（既存の列 ＋ 既知の列）。
    純利は `cli.perm_split.fold_net`（`evaluate_trading._row` の素の枝の写し）・B&H ＝ 買い% 100 の同じ器。
  - 判定 ＝ `catalog._judge_trading` と同じ式: 上乗せの平均 > 0 で fold 全部が正 → 採る ／ 平均だけ正 → 保留 ／ それ以外 → 落とす。
    ⚠ DSR・門は掛けない（素の 3 値の割合を見る）。
  - 学習器が既知の列を拾ったかの記録: 評価の行の 買い% と 既知の列 の相関（fold の平均）。

⚠ **検証結果一覧に行を作らない・`n_trials` に数えない・`runs/` を作らない**（合成の表 ＝ 識別項目を持たない）。
⚠ 本物の太い裾・ボラの塊は写さない（多変量正規）＝ 検出力を甘く見積もる側（登録簿の failure_modes 1）。
⚠ `--save` のときだけ DB の `outputs` の `diag/<時刻>_planted_power/` に JSON を入れる（`runs.put_output`・上書きしない）。
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from ail import config, registry, runs
from ail.contracts import META_COLUMNS
from ail.validation import splits

PLANTED = "own_planted"
SELECTOR = "全部使う（基準）"
STRENGTHS = (0.0, 0.1, 1.0, 5.0, 20.0)     # ⚠ 事前固定（bp/日）
SEEDS = 20                                 # ⚠ 事前固定
MIN_OVERLAP = 250


def real_moments(panel: pd.DataFrame) -> tuple[list[str], np.ndarray, np.ndarray]:
    """(銘柄の並び, 平均, 共分散の平方根 L〔Σ ＝ L Lᵀ〕)。"""
    wide = panel.pivot_table(index="ts", columns="symbol", values="y", aggfunc="first")
    mu = wide.mean().fillna(0.0).to_numpy()
    cov = wide.cov(min_periods=MIN_OVERLAP)
    var = np.diag(cov.to_numpy())
    cov = cov.fillna(0.0).to_numpy()
    np.fill_diagonal(cov, np.where(np.isfinite(var), var, np.nanmean(var)))
    w, v = np.linalg.eigh((cov + cov.T) / 2.0)
    L = v * np.sqrt(np.clip(w, 0.0, None))
    return [str(s) for s in wide.columns], mu, L


def synth(panel: pd.DataFrame, syms: list[str], mu: np.ndarray, L: np.ndarray, s_bp: float, seed: int) -> pd.DataFrame:
    """y を合成に差し替え、既知の列を足した写し。⚠ 種が同じなら s によらず雑音と既知の列は同じ。"""
    rng = np.random.default_rng([int(seed), 20261106])
    days = np.sort(panel["ts"].unique())
    Z = rng.standard_normal((len(days), len(syms))) @ L.T + mu
    x = rng.standard_normal(len(panel))
    di = pd.Index(days).get_indexer(panel["ts"])
    si = pd.Index(syms).get_indexer(panel["symbol"].astype(str))
    y = Z[di, si] + float(s_bp) * 1e-4 * x
    out = panel.assign(y=y, **{PLANTED: x})
    if "y_sign" in out:
        out["y_sign"] = np.sign(y)
    return out


def verdict(edges: list[float]) -> str:
    e = np.asarray(edges, dtype=float)
    if not len(e) or np.isnan(e).any():
        return "保留"
    if e.mean() > 0:
        return "採る" if (e > 0).all() else "保留"
    return "落とす"


def one(panel: pd.DataFrame, feats: list[str], exp: dict, edges_dates, th: float) -> dict:
    """1 つの合成の表を器に通す → fold ごとの上乗せ・判定・既知の列との相関。"""
    from cli import run as run_mod
    from cli.perm_split import fold_net

    v = exp.get("validation", {})
    cost_bp = float(exp.get("cost_bp", 5.0))
    model = registry.resolve("model", exp.get("model", "Ridge"))
    ctx = {"seed": int(v.get("seed", 0)), "model": model, "k": int(exp.get("k", 8)), **exp.get("model_args", {})}
    sel = {SELECTOR: registry.resolve("selector", SELECTOR)}
    edges, corr = [], []
    for f, tr, te in splits.folds_by_dates(panel, edges_dates, float(exp["horizon_min"]), int(v.get("embargo_bars", 0)),
                                           float(exp.get("bar_minutes", 0.0))):
        te = te.reset_index(drop=True)
        groups = te.groupby("symbol").indices
        buy, _ex, _n, _doc = run_mod.fold_buy_pct(tr, te, feats, exp, ctx, model=model, selectors=sel, detectors={},
                                                  baselines={}, form="shared", k=int(exp.get("k", 8)), groups=groups,
                                                  f=f, picked=[], log=lambda *a, **k: None)
        bp = np.asarray(buy[SELECTOR], dtype=float)
        y = te["y"].to_numpy(dtype=float)
        net = fold_net(te, bp, None, y, th, cost_bp)
        bh = fold_net(te, np.full(len(te), 100.0), None, y, th, cost_bp)
        edges.append(float("nan") if net is None or bh is None else net - bh)
        c = np.corrcoef(bp, te[PLANTED].to_numpy())[0, 1] if np.std(bp) > 0 else 0.0
        corr.append(float(c))
    return {"edges": edges, "verdict": verdict(edges), "corr_buy_planted": float(np.mean(corr))}


def curve(experiment: str = "trade_own_ridge_a", strengths=STRENGTHS, seeds: int = SEEDS, th: float = 50.0,
          log=print) -> dict:
    from cli.run import load_panel

    exp = config.resolve_experiment(experiment)
    ds = exp["_dataset"]
    exp.setdefault("horizon_min", 1440.0 if ds["period"] == "d" else float(exp["horizon"]))
    panel, path = load_panel(experiment, ds["period"], False, exp.get("features_from"))
    feats = [c for c in panel.columns if c not in META_COLUMNS] + [PLANTED]
    syms, mu, L = real_moments(panel)
    edges_dates = splits.date_edges(panel["ts"], int(exp.get("validation", {}).get("folds", 5)),
                                    splits.cut_offset(exp.get("validation", {})))   # rules.md 33 章（無ければ None）
    out = {"experiment": experiment, "table": path, "theta": th, "strengths_bp": list(strengths), "seeds": seeds,
           "symbols": len(syms), "rows": len(panel), "daily_sigma_bp_median": float(np.median(np.sqrt((L ** 2).sum(1)))) * 1e4,
           "runs": [], "summary": []}
    t0 = time.perf_counter()
    for s in strengths:
        got = []
        for seed in range(seeds):
            r = one(synth(panel, syms, mu, L, s, seed), feats, exp, edges_dates, th)
            r.update(s_bp=float(s), seed=seed)
            got.append(r)
            out["runs"].append(r)
        e = np.array([np.nanmean(r["edges"]) for r in got])
        vs = [r["verdict"] for r in got]
        row = {"s_bp": float(s), "採る": vs.count("採る") / seeds, "保留": vs.count("保留") / seeds,
               "落とす": vs.count("落とす") / seeds, "edge_mean_bp": float(np.mean(e)), "edge_p05_bp": float(np.quantile(e, 0.05)),
               "edge_p95_bp": float(np.quantile(e, 0.95)),
               "corr_buy_planted": float(np.mean([r["corr_buy_planted"] for r in got]))}
        out["summary"].append(row)
        log(f"s {s:>5g} bp/日: 採る {row['採る']:.0%} ／ 保留 {row['保留']:.0%} ／ 落とす {row['落とす']:.0%}・"
            f"上乗せ 平均 {row['edge_mean_bp']:+.0f}bp/fold（5〜95% {row['edge_p05_bp']:+.0f}〜{row['edge_p95_bp']:+.0f}）・"
            f"買い%×既知の列 ρ {row['corr_buy_planted']:.3f}・{time.perf_counter() - t0:.0f} 秒")
    out["seconds"] = time.perf_counter() - t0
    return out


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="合成の表で器と判定式の検出力の曲線を測る（読むだけ・n_trials に数えない）")
    p.add_argument("--experiment", default="trade_own_ridge_a")
    p.add_argument("--strengths", default=",".join(f"{s:g}" for s in STRENGTHS))
    p.add_argument("--seeds", type=int, default=SEEDS)
    p.add_argument("--theta", type=float, default=50.0)
    p.add_argument("--json")
    p.add_argument("--save", action="store_true", help="DB の outputs の diag/<時刻>_planted_power/ にも入れる")
    return p


def main() -> None:
    a = _parser().parse_args()
    doc = curve(a.experiment, [float(x) for x in a.strengths.split(",")], a.seeds, a.theta)
    text = json.dumps(doc, ensure_ascii=False, indent=1, default=float)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text)
    if a.save:
        print("→ " + runs.put_output(f"diag/{time.strftime('%Y-%m-%dT%H-%M-%S')}_planted_power/planted_power.json", text))


if __name__ == "__main__":
    main()
