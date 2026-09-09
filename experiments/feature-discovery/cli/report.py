"""`runs/` を横に並べて比べる。⚠ **「昨日より良くなったか」はここで見る**（rules.md 10 章）。

    python3 -m cli.report                      # 直近 10 実行の見出し
    python3 -m cli.report --metric 純利bp      # 手法 × 実行 の表
    python3 -m cli.report --diff A B           # ⚠ 2 実行の差分（入力の指紋も並べる）
    python3 -m cli.report --catalog            # ⚠ 台帳（試した結果の一覧）を markdown で吐く
    python3 -m cli.report --recheck            # ⚠ 既存の実行に checks.json を後から書く

⚠ **`--catalog` は「試した結果」の一覧である**（spec §2 の「手法の一覧」とは別物）。
⚠ **カタログ・registry・`runs/` の 3 つを突き合わせて生成する。手で書き写す欄はひとつも無い。**
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


def _panel_for(doc: dict) -> "tuple | None":
    """⚠ **その実行が使ったパネルが今も同じかを確かめてから返す。**

    ⚠ **確かめずに読むと、層を作り直した後の表を古い実行の数字に当ててしまう**
    （`own_only_h1` は raw で回した後に adjusted で作り直してある）。
    """
    import pandas as pd
    from ail.data import store

    inp = doc.get("inputs") or {}
    rel = inp.get("features_file")
    if not rel:
        return None
    path = os.path.join(store.ROOT, rel)
    meta_path = os.path.splitext(path)[0] + ".meta.json"
    if not (os.path.exists(path) and os.path.exists(meta_path)):
        return None
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    # ⚠ **層と行数が両方合わなければ別物として扱う**（片方だけでは足りない）
    if meta.get("layer") != inp.get("layer") or meta.get("rows") != inp.get("rows_before_sample"):
        return None
    full = pd.read_parquet(path)
    sample = int(inp.get("sample") or 0)
    panel = full
    if sample and len(full) > sample:
        panel = full.iloc[:: max(1, len(full) // sample)]
    return panel, full


def recheck() -> None:
    """既存の実行に `checks.json` を書く。⚠ **埋められない鍵は書かない**（0 や null で埋めない）。"""
    import pandas as pd
    from ail.validation import checks

    n_trials = checks.n_trials_now()
    for name in runs.list_runs():
        d = os.path.join(runs.RUNS, name)
        res, summ = os.path.join(d, "result.csv"), os.path.join(d, "summary.csv")
        if not (os.path.exists(res) and os.path.exists(summ)):
            print(f"  {name}: result/summary が無い → とばす")
            continue
        doc = _load(name)
        pair = _panel_for(doc)
        panel, full = pair if pair else (None, None)
        out = checks.compute(pd.read_csv(res), pd.read_csv(summ, index_col=0),
                             doc.get("config", {}), panel=panel, full_panel=full,
                             n_trials=n_trials, leak=name.endswith("_leak"))
        if panel is None:
            # ⚠ **パネルを確かめられなかったことを記録に残す。** 「計算し忘れ」と区別する
            out["panel"] = "⚠ 当時のパネルを確かめられないので、実効標本数と DSR は出していない"
        with open(os.path.join(d, "checks.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        b = out.get("best") or {}
        print(f"  {name}: 最良 {b.get('method', '—')} 純利 {b.get('純利bp', float('nan')):+.2f}bp"
              + ("" if panel is not None else "  ⚠ パネル無し"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default=None, help="手法 × 実行 の表にする指標（例 純利bp）")
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"))
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--catalog", action="store_true",
                    help="⚠ 台帳（試した結果の一覧）を markdown で吐く")
    ap.add_argument("--recheck", action="store_true",
                    help="⚠ 既存の実行に checks.json を後から書く（パネルが一致する実行だけ全部）")
    args = ap.parse_args()

    if args.catalog:
        from cli import ledger
        print(ledger.build(), end="")
        return

    if args.recheck:
        recheck()
        return

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
