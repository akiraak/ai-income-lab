"""⚠ **既定経路の指紋**（rules.md 14-4 規約 2 の検算。プラン holding-days-distribution §4）。

固定した合成パネルで閾値売買を端から端まで回し、⚠ **既存の出力**（`per_symbol.csv` の既存 9 列・`summary.csv`・
`checks.json` の `best` / `edge_vs_bh` / `dsr`）の指紋（sha256）を返す。⚠ **保有日数・逆売買の列を足しても、
この指紋は 1 ビットも変わらないこと**を `test_trading_run.py` が縛る。

⚠ **指紋は 2026-09-17 に、列を足す前のコード（commit 696cf7b）で取った**（`tests/fixtures/trading_fingerprint.json`）。
数値は 6 桁に丸めてから文字列にする（BLAS の差で末尾の桁が揺れても指紋が変わらないように）。
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

PER_SYMBOL_COLUMNS = ["手法", "閾値", "fold", "銘柄", "純利bp", "粗利bp", "取引回数", "保有日率", "見送り日数"]
SUMMARY_COLUMNS = ["閾値", "本数", "的中率", "IC", "粗利bp", "純利bp", "取引回数", "保有日率", "乱択ゲートbp", "fold数"]
RESULT_COLUMNS = ["手法", "fold", "閾値", "選んだ本数", "的中率", "IC", "粗利bp", "純利bp", "取引回数", "保有日率",
                  "検証日数", "乱択ゲート純利bp", "乱択ゲート取引回数"]
CHECK_KEYS = ("best", "edge_vs_bh", "bh_純利bp", "dsr", "random_gate", "edge_bins")


def panel(n_days=800, n_sym=3, seed=0, leak=False):
    """`test_trading_run._panel` と同じ形。⚠ **ここで別に持つのは、テスト側を直しても指紋が動かないため。**"""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        y = rng.normal(0.0002, 0.01, n_days)
        d = pd.DataFrame({"symbol": f"S{i}", "ts": ts, "y": y,
                          "own_ret_1": np.r_[0.0, y[:-1]], "noise": rng.normal(0, 1, n_days)})
        if leak:
            d["LEAK_y"] = y
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def experiment(form="shared"):
    return {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": form},
            "validation": {"split": "walk_forward_dates", "folds": 5, "seed": 0},
            "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "selectors": ["全部使う（基準）", "乱択（基準）"], "model": "Ridge",
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _frame_text(df: pd.DataFrame, cols) -> str:
    d = df[cols].copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].round(6)
    return d.to_csv(index=False, float_format="%.6f")


def fingerprint(run, form="shared") -> dict:
    from ail.validation import checks
    from cli.run import evaluate_trading

    p = panel()
    feats = [c for c in p.columns if c not in ("symbol", "ts", "y")]
    exp = experiment(form)
    res, per_sym, summary, daily, extra = evaluate_trading(p, feats, exp, run)
    doc = checks.compute_trading(res, summary, per_sym, daily, exp, n_trials=70, extra=extra)
    ch = {th: {k: e.get(k) for k in CHECK_KEYS if k in e} for th, e in doc["by_threshold"].items()}
    return {"per_symbol": _digest(_frame_text(per_sym, PER_SYMBOL_COLUMNS)),
            "result": _digest(_frame_text(res, RESULT_COLUMNS)),
            "summary": _digest(_frame_text(summary.reset_index(), ["手法"] + SUMMARY_COLUMNS)),
            "checks": _digest(json.dumps(ch, ensure_ascii=False, sort_keys=True)),
            "sample": {"per_symbol_rows": int(len(per_sym)),
                       "純利bp_sum": round(float(per_sym["純利bp"].sum()), 4),
                       "best_50": doc["by_threshold"]["50"]["best"]}}


if __name__ == "__main__":
    import os, sys, tempfile
    from ail import runs
    runs.RUNS = tempfile.mkdtemp()
    out = {}
    for form in ("shared", "per_symbol"):
        r = runs.Run(f"fp_{form}", {}, seed=0)
        out[form] = fingerprint(r, form)
    path = os.path.join(os.path.dirname(__file__), "fixtures", "trading_fingerprint.json")
    if "--write" in sys.argv:
        json.dump({"taken_at": "2026-09-17", "commit": "696cf7b", **out}, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("→", path)
    print(json.dumps(out, ensure_ascii=False, indent=1))
