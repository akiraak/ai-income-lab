"""日付をずらした偽の実行で、上乗せの「差なしの分布」を作る（登録簿 `20261107-date-shuffle-null`）。⚠ **読むだけ。**

    python3 -m cli.null_shuffle [--experiment trade_own_ridge_a] [--perms 200] [--block 20] [--json out.json] [--save]

作り方（登録簿の structure・水準は事前固定）:
  - 本物の表（既定 `trade_own_ridge_a` ＝ own_2018・63 本・2018-01-31〜）の 日付・銘柄・特徴量・fold の切れ目をそのまま使う。
  - 各 fold の **訓練分割と評価分割それぞれの中で**、日付を 20 営業日の塊に切り、塊の順を乱数で入れ替える（種 0〜199）。
    行 (銘柄, 日 d_i) の `y` を (同じ銘柄, 入れ替えた並びの i 番目の日) の `y` に差し替える（⚠ 銘柄の断面は保つ ＝ 同じ日の全銘柄が
    同じ日から `y` を借りる。fold をまたいで `y` を動かさない。借り先にその銘柄の行が無ければその行を落とす）。
    特徴量は動かさない ＝ 特徴量とラベルの時間の対応だけが壊れる。
  - 器 ＝ 素の own × Ridge（`全部使う（基準）`・形式 (A)）を `cli.run.fold_buy_pct` で・θ 50 ／ 55 ／ 60 は同じ買い% から。
    純利は `cli.perm_split.fold_net`・B&H ＝ 買い% 100 の同じ器（入れ替えた `y` で）。判定は `cli.planted_power.verdict`（catalog と同じ 3 値）。
  - 元の行（入れ替えなし）も同じ関数で回し、偽の分布の中の分位を出す。
  ⚠ 判定の式は変えない（並べて見せるだけ）。⚠ 塊の長さ ／ 回数は結果を見て変えない（登録簿の failure_modes 1・4）。

⚠ **検証結果一覧に行を作らない・`n_trials` に数えない・`runs/` を作らない**（偽の実行 ＝ 対照）。
⚠ `--save` のときだけ DB の `outputs` の `diag/<時刻>_null_shuffle/` に JSON を入れる（`runs.put_output`・上書きしない）。
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

SELECTOR = "全部使う（基準）"
THETAS = (50.0, 55.0, 60.0)    # ⚠ 事前固定（登録簿 ＝ 素の own × Ridge の 3 行）
PERMS = 200                    # ⚠ 事前固定（種 0〜199）
BLOCK = 20                     # ⚠ 事前固定（営業日）


def date_permutation(days: np.ndarray, block: int, rng: np.random.Generator) -> np.ndarray:
    """日付の並び days（昇順）を block 日の塊に切り、塊の順を入れ替えた並び（同じ長さ）。"""
    n = len(days)
    blocks = [np.arange(i, min(i + block, n)) for i in range(0, n, block)]
    order = rng.permutation(len(blocks))
    return days[np.concatenate([blocks[j] for j in order])] if n else days


def shuffle_y(df: pd.DataFrame, block: int, rng: np.random.Generator) -> pd.DataFrame:
    """df の中で y を日付の塊ごとに入れ替えた写し（借り先が無い行は落とす）。"""
    ts = pd.to_datetime(df["ts"])
    days = pd.DatetimeIndex(ts.unique()).sort_values()
    src = pd.Series(date_permutation(days, block, rng), index=days)
    look = df.assign(_ts=ts).set_index(["symbol", "_ts"])["y"]
    look = look[~look.index.duplicated()]
    key = pd.MultiIndex.from_arrays([df["symbol"].to_numpy(), pd.DatetimeIndex(src.reindex(ts).array)])
    y = look.reindex(key).to_numpy()
    out = df.assign(y=y)
    out = out[np.isfinite(out["y"].to_numpy(dtype=float))].reset_index(drop=True)
    if "y_sign" in out:
        out["y_sign"] = np.sign(out["y"])
    return out


def one(panel, feats, exp, edges_dates, seed: int | None, block: int = BLOCK, thetas=THETAS) -> dict:
    """1 回の（偽の）実行 → θ ごとの fold の上乗せと判定。seed が None なら入れ替えない（元の行）。"""
    from cli import run as run_mod
    from cli.perm_split import fold_net
    from cli.planted_power import verdict

    v = exp.get("validation", {})
    cost_bp = float(exp.get("cost_bp", 5.0))
    model = registry.resolve("model", exp.get("model", "Ridge"))
    ctx = {"seed": int(v.get("seed", 0)), "model": model, "k": int(exp.get("k", 8)), **exp.get("model_args", {})}
    sel = {SELECTOR: registry.resolve("selector", SELECTOR)}
    rng = None if seed is None else np.random.default_rng([int(seed), 20261107])
    edges = {th: [] for th in thetas}
    for f, tr, te in splits.folds_by_dates(panel, edges_dates, float(exp["horizon_min"]), int(v.get("embargo_bars", 0)),
                                           float(exp.get("bar_minutes", 0.0))):
        tr, te = tr.reset_index(drop=True), te.reset_index(drop=True)
        if rng is not None:
            tr, te = shuffle_y(tr, block, rng), shuffle_y(te, block, rng)
        groups = te.groupby("symbol").indices
        buy, _ex, _n, _doc = run_mod.fold_buy_pct(tr, te, feats, exp, ctx, model=model, selectors=sel, detectors={},
                                                  baselines={}, form="shared", k=int(exp.get("k", 8)), groups=groups,
                                                  f=f, picked=[], log=lambda *a, **k: None)
        bp = np.asarray(buy[SELECTOR], dtype=float)
        y = te["y"].to_numpy(dtype=float)
        for th in thetas:
            net = fold_net(te, bp, None, y, th, cost_bp)
            bh = fold_net(te, np.full(len(te), 100.0), None, y, th, cost_bp)
            edges[th].append(float("nan") if net is None or bh is None else net - bh)
    return {str(int(th)): {"edges": e, "verdict": verdict(e), "mean": float(np.nanmean(e))} for th, e in edges.items()}


def summarize(null: list[dict], orig: dict, thetas=THETAS) -> list[dict]:
    rows = []
    for th in thetas:
        k = str(int(th))
        m = np.array([r[k]["mean"] for r in null])
        allpos = np.array([np.all(np.asarray(r[k]["edges"]) > 0) for r in null])
        vs = [r[k]["verdict"] for r in null]
        o = orig[k]["mean"]
        rows.append({"theta": int(th), "perms": len(null), "null_p05": float(np.quantile(m, 0.05)),
                     "null_p50": float(np.quantile(m, 0.5)), "null_p95": float(np.quantile(m, 0.95)),
                     "全fold正の割合": float(allpos.mean()), "採る": vs.count("採る") / len(vs),
                     "保留": vs.count("保留") / len(vs), "落とす": vs.count("落とす") / len(vs),
                     "元の上乗せ": o, "元の判定": orig[k]["verdict"], "元の分位": float((m < o).mean() + 0.5 * (m == o).mean())})
    return rows


def null_dist(experiment: str = "trade_own_ridge_a", perms: int = PERMS, block: int = BLOCK, log=print) -> dict:
    from cli.run import load_panel

    exp = config.resolve_experiment(experiment)
    ds = exp["_dataset"]
    exp.setdefault("horizon_min", 1440.0 if ds["period"] == "d" else float(exp["horizon"]))
    panel, path = load_panel(experiment, ds["period"], False, exp.get("features_from"))
    feats = [c for c in panel.columns if c not in META_COLUMNS]
    edges_dates = splits.date_edges(panel["ts"], int(exp.get("validation", {}).get("folds", 5)),
                                    splits.cut_offset(exp.get("validation", {})))   # rules.md 33 章（無ければ None）
    t0 = time.perf_counter()
    orig = one(panel, feats, exp, edges_dates, None, block)
    log("元の行: " + " ／ ".join(f"θ{k} {v['mean']:+.0f}bp/fold {v['verdict']}" for k, v in orig.items()))
    null = []
    for s in range(perms):
        null.append(one(panel, feats, exp, edges_dates, s, block))
        if (s + 1) % 20 == 0:
            log(f"{s + 1}/{perms} 回・{time.perf_counter() - t0:.0f} 秒")
    out = {"experiment": experiment, "table": path, "selector": SELECTOR, "block_days": block, "perms": perms,
           "thetas": list(THETAS), "orig": orig, "null": null, "summary": summarize(null, orig)}
    out["seconds"] = time.perf_counter() - t0
    for r in out["summary"]:
        log(f"θ{r['theta']}: 偽の上乗せ 5/50/95% {r['null_p05']:+.0f} ／ {r['null_p50']:+.0f} ／ {r['null_p95']:+.0f}bp/fold・"
            f"全 fold 正 {r['全fold正の割合']:.1%}・採る {r['採る']:.1%} 保留 {r['保留']:.1%}・"
            f"元 {r['元の上乗せ']:+.0f}（{r['元の判定']}・分位 {r['元の分位']:.2f}）")
    return out


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="日付の塊を入れ替えた偽の実行で上乗せの差なしの分布を作る（読むだけ・n_trials に数えない）")
    p.add_argument("--experiment", default="trade_own_ridge_a")
    p.add_argument("--perms", type=int, default=PERMS)
    p.add_argument("--block", type=int, default=BLOCK)
    p.add_argument("--json")
    p.add_argument("--save", action="store_true", help="DB の outputs の diag/<時刻>_null_shuffle/ にも入れる")
    return p


def main() -> None:
    a = _parser().parse_args()
    doc = null_dist(a.experiment, a.perms, a.block)
    text = json.dumps(doc, ensure_ascii=False, indent=1, default=float)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text)
    if a.save:
        print("→ " + runs.put_output(f"diag/{time.strftime('%Y-%m-%dT%H-%M-%S')}_null_shuffle/null_shuffle.json", text))


if __name__ == "__main__":
    main()
