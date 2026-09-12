"""検証（`experiments/feature-discovery/runs/`）を一覧にする。

⚠ **読むだけ。** 検査（fold の符号・上乗せ・実効標本数・デフレーテッド SR）は
⚠ **実験側が `checks.json` に書いたものをそのまま出す**（[プラン §2](../../docs/plans/archive/dashboard-experiments.md)）。
⚠ **同じ数字を 2 か所で計算しない**ので、仕様書と画面がずれない。

⚠ **標準ライブラリだけで書く。** 管理画面に pandas / scipy を持ち込まない。

⚠ **スコアは「最良手法（基準線を除く）の純利 bp」**（利用者が 2026-09-08 に決めた）。
⚠ **1 つの数字なので fold の偏りも多重検定も出ない。** だから検査の列を必ず横に並べる。

⚠ **前置きの門（rules.md 14-5）を通らなかった実行も出す**（2026-09-11。仕様 §10-6）。
⚠ **`summary.csv` を持たないので黙って落ちていた。** 台帳には「門前」で残るので、画面からも消さない。
⚠ **ただし検証としては数えない**（回していないから）ので、`index` が別枠（`gated_runs`）に出す。
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
        # 閾値つき売買（rules.md 13 章）の列。旧実行には無いので、あるときだけ持つ
        for k in ("閾値", "取引回数", "保有日率"):
            if r.get(k) not in (None, ""):
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
    t = config.get("trading") or {}
    if t.get("style") == "threshold":
        # 閾値つき売買（rules.md 13 章）。形式 (A) 共通 / (B) 銘柄別 もタイトルで見分ける
        parts.append("閾値売買・" + ("銘柄別" if t.get("form") == "per_symbol" else "共通"))
    title = "・".join(p for p in parts if p and p != "—")
    return f"{title}（先読みの検査）" if leak else title


def _mark(ok: bool | None) -> str:
    return "✅" if ok is True else "⚠" if ok is False else "⏳"


def gate_methods(gate: dict) -> list[dict]:
    """前置きの門（rules.md 14-5）の写し。手法ごとに AUC・買い% 幅・通過。

    ⚠ **写すだけ。** 水準（AUC ≥ 0.52・幅 ≥ 20 点）と通過は実験側が `checks.json` に
    書いたものをそのまま出す（⚠ **画面で門を再判定しない**）。
    """
    blocked = [str(m) for m in (gate.get("blocked") or [])]
    methods = gate.get("methods") or {}
    names = list(methods) + [m for m in blocked if m not in methods]   # ⚠ 門前は必ず 1 行出す
    out = []
    for name in names:
        g = methods.get(name) or {}
        out.append({"method": str(name), "auc": g.get("auc"), "width_pt": g.get("width_pt"),
                    "auc_folds": list(g.get("auc_folds") or []),
                    "width_folds": list(g.get("width_folds") or []),
                    "passed": str(name) not in blocked, "note": g.get("注記")})
    return out


def is_gated(summary: list[dict], gate: dict) -> bool:
    """⚠ **全手法が門前 ＝ 閾値売買を回していない実行か**（`summary.csv` を持たない）。

    ⚠ **条件は台帳（`ail/catalog.py`）と同じ。** `--ignore-gate`（`forced`）で summary が
    無いのは「回したのに結果が無い」なので門前とは読まない。
    """
    return not summary and bool(gate.get("blocked")) and not gate.get("forced")


def load_run(d: Path) -> dict | None:
    """1 実行ぶん。⚠ **summary.csv が無い実行は「門前」だけ拾う**（rules.md 14-5）。

    ⚠ **門前は「計算していない」ではなく「回していない」。** 台帳には残るので、
    ⚠ **画面からも消さない**（隠さない）。検証としては数えないので `index` が別枠に出す。
    """
    summary = _read_summary(d / "summary.csv")
    ch = _read_json(d / "checks.json")
    gate = ch.get("gate") or {}
    gated = is_gated(summary, gate)
    if not summary and not gated:
        return None
    methods = gate_methods(gate)
    config, inputs = _read_json(d / "config.json"), _read_json(d / "inputs.json")
    env = _read_json(d / "env.json")
    leak = bool(ch.get("leak")) or d.name.endswith("_leak")
    best = ch.get("best") or {}
    # ⚠ 閾値つき売買の実行は「上乗せ」が対 B&H（edge_vs_bh。rules.md 13-7）。旧実行は対「常に上」
    folds, edge, dsr, breadth = (ch.get("folds"),
                                 ch.get("edge_vs_bh") or ch.get("edge_vs_drift"),
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
        # 閾値つき売買（rules.md 13 章）。⚠ **checks.json の写しを出すだけ。画面側で数え直さない**
        "style": ch.get("style"),
        "form": ch.get("form"),
        "thresholds": ch.get("thresholds"),
        "by_threshold": ch.get("by_threshold"),
        "bh_net": ch.get("bh_純利bp"),
        "per_symbol": ch.get("per_symbol"),
        "has_checks": bool(ch),
        "summary": summary,
        # 前置きの門（rules.md 14-5）。⚠ **門前も残す**（数えないが隠さない）
        "gated": gated,
        "gate": gate or None,
        "gate_methods": methods,
        "gate_blocked": [m for m in methods if not m["passed"]],
        "gate_forced": bool(gate.get("forced")),
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
    """検査の列に ✅ / ⚠ / ⏳ を付ける。⚠ **スコアの横に必ず出すもの。**

    ⚠ **門前の実行には付けない**（rules.md 14-5）。⏳ を 5 つ並べると「計算待ち」に見えるが、
    ⚠ **門前は計算していないのではなく検証を回していない。** 代わりに門の 2 値を出す。
    """
    if run.get("gated"):
        run["marks"] = {}
        return run
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
    """一覧。⚠ **スコアの降順。先読みの検査と門前は別枠に出す**（混ぜると全部が嘘になる）。

    ⚠ **門前の実行は `total` / `positive` / `kinds` に数えない**（rules.md 14-5）。
    ⚠ **検証を回していないので「検証 N 件」に足すと水増しになる**（n_trials に数えないのと同じ）。
    """
    runs = [with_marks(r) for r in load_all(runs_dir)]
    gated = [r for r in runs if r["gated"]]
    scored = [r for r in runs if not r["gated"]]
    real = [r for r in scored if not r["leak"]]
    leak = [r for r in scored if r["leak"]]
    key = lambda r: (r["score"] is not None, r["score"] or 0.0)   # noqa: E731
    real.sort(key=key, reverse=True)
    leak.sort(key=key, reverse=True)
    gated.sort(key=lambda r: r["run_id"], reverse=True)           # 門前はスコアが無いので新しい順
    kinds: dict[str, int] = {}
    for r in real:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    return {
        "runs": real,
        "leak_runs": leak,
        "gated_runs": gated,
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
