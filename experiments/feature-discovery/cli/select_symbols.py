"""いまの選抜（rules.md 29-8）: 訓練分割の成績で銘柄を選ぶ規則を**全期間を訓練にして** 1 回当て、K 本を出す。

⚠ **読むだけ**（`runs/` を作らない・DB に書かない）。config の `[[trading.trader]] select` の条件をそのまま使う
（物差し・K・予算 ＝ 枠 ＝ 候補の線）。表は `cli.build.assemble`（`features_from` の持ち主）・訓練 ＝ ラベルの確かめられている行の全部。
出すのは候補の人の銘柄の**案**。⚠ 人を作るかは利用者（成績で採らない）。

    .venv/bin/python -m cli.select_symbols --experiment sel_small_cs [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import config, registry
from ail.contracts import META_COLUMNS
from ail.validation import prep
from ail.validation import select as symsel
from cli import build as build_cli


def current_selection(experiment: str, layer: str = "adjusted") -> dict:
    """{条件の tag: {by, k, cap, candidates, chosen: {θ: [銘柄]}, table: DataFrame}} と表の記録。"""
    exp = config.resolve_experiment(experiment)
    t = exp.get("trading") or {}
    conds = [c for c in (t.get("trader") or []) if c.get("select")]
    if not conds:
        raise SystemExit(f"⚠ {experiment} に select の条件（[[trading.trader]] select）が無い（rules.md 29-1）")
    if exp.get("detectors"):
        raise SystemExit("⚠ いまの選抜は選別 × モデルの config だけ（rules.md 29-3）")
    table_exp = exp.get("features_from") or experiment
    df, _panel, texp = build_cli.assemble(table_exp, layer)
    df = df.replace([np.inf, -np.inf], np.nan)
    start = texp.get("start_date") or exp.get("start_date")
    if start:
        df = df[df["ts"] >= pd.Timestamp(start, tz="UTC")]
    tr = df.dropna().sort_values("ts", kind="stable").reset_index(drop=True)
    feats = [c for c in tr.columns if c not in META_COLUMNS]
    thresholds = [float(x) for x in t.get("thresholds", (50.0, 55.0, 60.0))]
    v = exp.get("validation", {})
    k_cols = int(exp.get("k", 8))
    model = registry.resolve("model", exp.get("model", "Ridge"))
    ctx = {"seed": int(v.get("seed", 0)), "model": model, "k": k_cols, **exp.get("model_args", {})}
    selectors = {prep.label(exp, n): fn for n, fn in registry.resolve_all("selector", exp.get("selectors", [])).items()}
    out: dict = {"experiment": experiment, "table": table_exp, "rows": int(len(tr)),
                 "train_start": str(tr["ts"].min().date()), "train_end": str(tr["ts"].max().date()),
                 "symbols": int(tr["symbol"].nunique()), "conditions": {}}
    for c in conds:
        sel, tag = c["select"], str(c["tag"])
        K, budget = int(sel["k"]), float(c["budget_usd"])
        cap = budget / K
        cands = symsel.pool(tr, cap)
        if str(sel["by"]) == "ret":
            chosen, table = symsel.by_return(tr, K, cap)
            chosen_by_th = {th: list(chosen) for th in thresholds}
        else:
            methods = [m for m in selectors if c.get("methods") is None or m in c["methods"]]
            if len(methods) != 1:
                raise SystemExit(f"⚠ {tag!r}: モデルの成績の選抜は手法 1 本だけ（いま {methods}。rules.md 29-3）")
            chosen_by_th, table = symsel.by_model_net(tr, feats, exp, ctx, model, selectors[methods[0]], k_cols,
                                                      thresholds, float(exp.get("cost_bp", 5.0)), K, cap)
        out["conditions"][tag] = {"by": str(sel["by"]), "k": K, "budget_usd": budget, "cap": cap,
                                  "candidates": cands, "chosen": {str(int(th)): v for th, v in chosen_by_th.items()},
                                  "table": table}
    return out


def _md(res: dict) -> str:
    lines = [f"## いまの選抜 — {res['experiment']}（表 {res['table']}・訓練 {res['train_start']}〜{res['train_end']}・"
             f"{res['rows']:,} 行・{res['symbols']} 銘柄）", ""]
    for tag, c in res["conditions"].items():
        lines.append(f"### 〔トレーダー・{tag}〕（{c['by']}・K {c['k']}・予算 ${c['budget_usd']:,.0f}・枠 ${c['cap']:,.0f}・候補 {len(c['candidates'])} 本）")
        lines.append("")
        lines.append("| θ | 選んだ銘柄 |")
        lines.append("| --- | --- |")
        for th, syms in c["chosen"].items():
            lines.append(f"| {th} | {' '.join(syms) or '—'} |")
        lines.append("")
        tb = c["table"]
        if len(tb):
            top = tb.sort_values(["閾値", "score"], ascending=[True, False], na_position="last").head(15 if tb["閾値"].isna().all() else 45)
            lines.append("| 銘柄 | θ | 行 | 成績 | 選んだ |")
            lines.append("| --- | ---: | ---: | ---: | --- |")
            for r in top.itertuples(index=False):
                th = "—" if pd.isna(r.閾値) else f"{int(r.閾値)}"
                sc = "—" if pd.isna(r.score) else f"{r.score:+.4f}" if c["by"] == "ret" else f"{r.score:+,.1f}"
                lines.append(f"| {r.symbol} | {th} | {r.rows} | {sc} | {'✅' if r.chosen else ''} |")
            lines.append("")
    lines.append("⚠ 成績で人を選ばない。これは候補の人の銘柄の案（rules.md 29-8）。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--layer", default="adjusted")
    ap.add_argument("--json", default=None, help="結果を JSON で書く道（表は除く）")
    args = ap.parse_args(argv)
    res = current_selection(args.experiment, args.layer)
    print(_md(res))
    if args.json:
        slim = {**res, "conditions": {k: {kk: vv for kk, vv in v.items() if kk != "table"} for k, v in res["conditions"].items()}}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, indent=1)
        print(f"wrote {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
