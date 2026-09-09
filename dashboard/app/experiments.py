"""検証（`experiments/feature-discovery/runs/`）を一覧にする。

⚠ **読むだけ。** 検査（fold の符号・上乗せ・実効標本数・デフレーテッド SR）は
⚠ **実験側が `checks.json` に書いたものをそのまま出す**（[プラン §2](../../docs/plans/archive/dashboard-experiments.md)）。
⚠ **同じ数字を 2 か所で計算しない**ので、仕様書と画面がずれない。

⚠ **標準ライブラリだけで書く。** 管理画面に pandas / scipy を持ち込まない。

⚠ **スコアは「最良手法（基準線を除く）の純利 bp」**（利用者が 2026-09-08 に決めた）。
⚠ **1 つの数字なので fold の偏りも多重検定も出ない。** だから検査の列を必ず横に並べる。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

# 特徴量の層 → 検証の種類
CROSS_LAYERS = ("cs", "rel", "ll")
LAYER_LABEL = {"adjusted": "調整後", "raw": "調整前"}


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _read_summary(path: Path) -> list[dict]:
    """`summary.csv` を辞書の一覧にする。⚠ **数に直せない欄は None のまま残す。**"""
    try:
        with path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    out = []
    for r in rows:
        d = {"手法": (r.get("手法") or "").strip()}
        for k in ("本数", "的中率", "IC", "粗利bp", "純利bp", "fold数"):
            d[k] = _num(r.get(k))
        out.append(d)
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def granularity(config: dict) -> tuple[str, float]:
    m = config.get("bar_minutes")
    if not m:
        m = {"daily": 1440.0, "min1": 1.0}.get(config.get("dataset", ""), 0.0)
    m = float(m or 0.0)
    return {1440.0: "日足", 1.0: "1 分足"}.get(m, f"{m:g} 分足" if m else "—"), m


def horizon(config: dict, bar_minutes: float) -> str:
    h = float(config.get("horizon", 0) or 0)
    total = h * bar_minutes
    if not total:
        return f"{h:g} 本"
    if total >= 1440:
        return f"{total / 1440:g} 日先"
    if total >= 390 and total % 390 == 0:
        return f"{total / 390:g} 取引日先"
    return f"{total:g} 分先"


def base_kind(config: dict) -> str:
    """特徴量の層に断面（`cs` `rel` `ll`）が入っているか。"""
    layers = config.get("feature_layers") or []
    return "断面" if any(l in CROSS_LAYERS for l in layers) else "プーリング"


def kind_of(config: dict, leak: bool) -> str:
    """⚠ **検証の種類。** 先読みの検査は種類として別に立てる（一覧に混ぜないため）。"""
    return "先読みの検査" if leak else base_kind(config)


def title_of(config: dict, inputs: dict, leak: bool) -> str:
    """⚠ **タイトルは付けずに設定から組み立てる。** 手で書くと実行のたびにずれる。

    ⚠ **先読みの検査でも、どの検証の対照なのかが読めるようにする**
    （「先読みの検査」だけだと、断面のものかプーリングのものか分からない）。
    """
    gran, bar = granularity(config)
    layer = LAYER_LABEL.get((inputs.get("layer") or "").strip(), inputs.get("layer") or "—")
    parts = [base_kind(config), gran, horizon(config, bar), layer]
    title = "・".join(p for p in parts if p and p != "—")
    return f"{title}（先読みの検査）" if leak else title


def _mark(ok: bool | None) -> str:
    return "✅" if ok is True else "⚠" if ok is False else "⏳"


def load_run(d: Path) -> dict | None:
    """1 実行ぶん。⚠ **summary.csv が無いものは検証として数えない。**"""
    summary = _read_summary(d / "summary.csv")
    if not summary:
        return None
    config, inputs = _read_json(d / "config.json"), _read_json(d / "inputs.json")
    env, ch = _read_json(d / "env.json"), _read_json(d / "checks.json")
    leak = bool(ch.get("leak")) or d.name.endswith("_leak")
    best = ch.get("best") or {}
    folds, edge, dsr, breadth = (ch.get("folds"), ch.get("edge_vs_drift"),
                                 ch.get("dsr"), ch.get("breadth"))
    gran, bar = granularity(config)
    return {
        "run_id": d.name,
        "title": title_of(config, inputs, leak),
        "kind": kind_of(config, leak),
        "leak": leak,
        "gran": gran,
        "horizon": horizon(config, bar),
        "layer": (inputs.get("layer") or "—"),
        "layer_label": LAYER_LABEL.get(inputs.get("layer") or "", inputs.get("layer") or "—"),
        "feature_layers": " ".join(config.get("feature_layers") or []) or "—",
        "features": inputs.get("features"),
        "symbols": inputs.get("symbols"),
        "rows": inputs.get("rows_before_sample"),
        "k": config.get("k"),
        "cost_bp": config.get("cost_bp"),
        "seed": env.get("seed"),
        "commit": env.get("git_commit"),
        "started_at": env.get("started_at") or d.name.split("_")[0],
        # ⚠ スコア = 最良手法（基準線を除く）の純利 bp
        "score": best.get("純利bp"),
        "best": best,
        "folds": folds,
        "edge": edge,
        "dsr": dsr,
        "breadth": breadth,
        "drift_gross": ch.get("drift_粗利bp"),
        "panel_note": ch.get("panel"),
        "has_checks": bool(ch),
        "summary": summary,
    }


def load_all(runs_dir: Path) -> list[dict]:
    """⚠ **`runs/` が無くても落とさない**（git 管理外なので別環境では空になる）。"""
    if not runs_dir.is_dir():
        return []
    out = []
    for d in sorted(runs_dir.iterdir()):
        if d.is_dir() and (run := load_run(d)):
            out.append(run)
    return out


def with_marks(run: dict) -> dict:
    """検査の列に ✅ / ⚠ / ⏳ を付ける。⚠ **スコアの横に必ず出すもの。**"""
    folds, edge, dsr = run.get("folds"), run.get("edge"), run.get("dsr")
    all_pos = (folds and folds.get("folds") and folds["positive"] == folds["folds"]) or None
    t = (edge or {}).get("t")
    d = (dsr or {}).get("DSR")
    run["marks"] = {
        "層": _mark(True if run["layer"] == "adjusted"
                   else (False if run["gran"] == "日足" else None)),
        "純利": _mark(None if run["score"] is None else run["score"] > 0),
        "fold": _mark(None if not folds else (True if all_pos else False)),
        "上乗せ": _mark(None if t is None else t > 3.0),
        "DSR": _mark(None if d is None else d > 0.95),
    }
    return run


def index(runs_dir: Path) -> dict:
    """一覧。⚠ **スコアの降順。先読みの検査は別枠に出す**（混ぜると全部が嘘になる）。"""
    runs = [with_marks(r) for r in load_all(runs_dir)]
    real = [r for r in runs if not r["leak"]]
    leak = [r for r in runs if r["leak"]]
    key = lambda r: (r["score"] is not None, r["score"] or 0.0)   # noqa: E731
    real.sort(key=key, reverse=True)
    leak.sort(key=key, reverse=True)
    kinds: dict[str, int] = {}
    for r in real:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    return {
        "runs": real,
        "leak_runs": leak,
        "kinds": kinds,
        "total": len(real),
        "positive": sum(1 for r in real if (r["score"] or 0) > 0),
        "missing_checks": [r["run_id"] for r in runs if not r["has_checks"]],
        "runs_dir": str(runs_dir),
    }


def one(runs_dir: Path, run_id: str) -> dict | None:
    """1 検証の詳細。⚠ **`..` を含む run_id は受けない。**"""
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return None
    d = runs_dir / run_id
    if not d.is_dir() or d.parent != runs_dir:
        return None
    run = load_run(d)
    return with_marks(run) if run else None
