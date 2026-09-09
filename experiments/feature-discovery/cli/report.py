"""`runs/` を横に並べて比べる。⚠ **「昨日より良くなったか」はここで見る**（rules.md 10 章）。

    python3 -m cli.report                      # 直近 10 実行の見出し
    python3 -m cli.report --metric 純利bp      # 手法 × 実行 の表
    python3 -m cli.report --diff A B           # ⚠ 2 実行の差分（入力の指紋も並べる）
"""

from __future__ import annotations

import argparse
import json
import os

import pandas as pd

from ail import runs


def _load(name: str) -> dict:
    d = os.path.join(runs.RUNS, name)
    doc = {"run": name, "dir": d}
    for f in ("config.json", "inputs.json", "env.json"):
        path = os.path.join(d, f)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                doc[f[:-5]] = json.load(fh)
    s = os.path.join(d, "summary.csv")
    doc["summary"] = pd.read_csv(s, index_col=0) if os.path.exists(s) else None
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default=None, help="手法 × 実行 の表にする指標（例 純利bp）")
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"))
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    names = runs.list_runs()
    if not names:
        raise SystemExit("runs/ が空")

    if args.diff:
        for name in args.diff:
            d = _load(name)
            inp, env = d.get("inputs", {}), d.get("env", {})
            print(f"--- {name}")
            print(f"    入力 {inp.get('features_file')} / 層 {inp.get('layer')} / "
                  f"行 {inp.get('rows_before_sample'):,} / 特徴量 {inp.get('features')}")
            print(f"    指紋 {inp.get('data_manifest')} / 種 {env.get('seed')} / "
                  f"commit {env.get('git_commit')}")
        a, b = (_load(n)["summary"] for n in args.diff)
        if a is None or b is None:
            raise SystemExit("summary.csv が無い実行がある")
        j = a.join(b, lsuffix="_A", rsuffix="_B", how="outer")
        for c in ("的中率", "粗利bp", "純利bp"):
            j[f"Δ{c}"] = j[f"{c}_B"] - j[f"{c}_A"]
        print()
        print(j[[c for c in j.columns if c.startswith("Δ")]].round(4).to_string())
        print("\n⚠ **数字が変わったら、まず配線を疑う**（rules.md 11 章 規約 7）。")
        return

    if args.metric:
        cols = {}
        for n in names[-args.limit:]:
            s = _load(n)["summary"]
            if s is not None and args.metric in s:
                cols[n] = s[args.metric]
        print(pd.DataFrame(cols).round(4).to_string())
        return

    rows = []
    for n in names[-args.limit:]:
        d = _load(n)
        inp, env = d.get("inputs", {}), d.get("env", {})
        best = (d["summary"]["純利bp"].idxmax() if d["summary"] is not None
                and "純利bp" in d["summary"] else None)
        rows.append({"実行": n, "層": inp.get("layer"), "行": inp.get("rows_before_sample"),
                     "特徴量": inp.get("features"), "種": env.get("seed"),
                     "commit": env.get("git_commit"), "純利の最良": best,
                     "純利bp": (round(d["summary"]["純利bp"].max(), 3)
                                if d["summary"] is not None else None)})
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
