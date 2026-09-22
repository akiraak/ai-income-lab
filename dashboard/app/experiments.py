"""実行の記録（`experiments/feature-discovery/runs/research.sqlite`）を一覧にする（vibeboard の「実行」タブ）。

⚠ **記録は DB**（2026-09-21 にディレクトリから移した。書き手は実験側の `ail/rundb.py`）。
⚠ **読み取り専用で開く**（`mode=ro`。無ければ作らない ＝ git 管理外なので別環境では空）。
実行の名前・中のファイルの道（`summary.csv`・`checks.json` …）・中身は、いままでのディレクトリと同じ。

⚠ **読むだけ。** 検査（fold の符号・上乗せ・実効標本数・デフレーテッド SR）は
⚠ **実験側が `checks.json` に書いたものをそのまま出す**（[プラン §2](../../docs/plans/archive/dashboard-experiments.md)）。
⚠ **同じ数字を 2 か所で計算しない**ので、仕様書と画面がずれない。

⚠ **標準ライブラリだけで書く。** 管理画面に pandas / scipy を持ち込まない。

⚠ **スコアは「最良手法（基準線を除く）の純利 bp」**（利用者が 2026-09-08 に決めた）。
⚠ **1 つの数字なので fold の偏りも多重検定も出ない。** だから検査の列を必ず横に並べる。

⚠ **前置きの門（rules.md 14-5）を通らなかった実行も出す**（2026-09-11。仕様 §10-6）。
⚠ **`summary.csv` を持たないので黙って落ちていた。** 検証結果一覧には「門前」で残るので、画面からも消さない。
⚠ **ただし実行の件数には数えない**（回していないから）ので、`index` が別枠（`gated_runs`）に出す。
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import zlib
from pathlib import Path

# ⚠ 実験側 `ail/rundb.py` の `FILE_NAME` と同じ（`runs_dir` の中に置く）
DB_NAME = "research.sqlite"
# 見張り（`vibetab.exp_fingerprint`）が見るファイル
WATCHED = ("summary.csv", "checks.json", "config.json", "inputs.json", "env.json")


class Store:
    """実行の記録を読むだけ。⚠ **DB が無い・開けないときは空**（落とさない）。

    ⚠ 中身の形は実験側 `ail/rundb.py` と同じ（`codec` が 'text' ＝ UTF-8 の文字列のまま ／ 'zlib'）。
    """

    def __init__(self, runs_dir: Path):
        self.path = Path(runs_dir) / DB_NAME
        self.conn: sqlite3.Connection | None = None
        if self.path.is_file():
            try:
                self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=10)
                self.conn.execute("SELECT 1 FROM files LIMIT 1")
            except sqlite3.Error:
                self.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def names(self) -> list[str]:
        if self.conn is None:
            return []
        return [n for (n,) in self.conn.execute("SELECT name FROM runs ORDER BY name")]

    def has(self, run: str) -> bool:
        return self.conn is not None and self.conn.execute(
            "SELECT 1 FROM runs WHERE name = ?", (run,)).fetchone() is not None

    def get(self, run: str, path: str) -> bytes | None:
        if self.conn is None:
            return None
        row = self.conn.execute("SELECT codec, body FROM files WHERE run = ? AND path = ?", (run, path)).fetchone()
        if row is None:
            return None
        codec, body = row
        if codec == "text":
            return body.encode("utf-8") if isinstance(body, str) else bytes(body)
        if codec == "zlib":
            return zlib.decompress(body)
        return None

    def digests(self) -> dict[str, float]:
        """実行ごとの指紋（見張りの 5 ファイルの sha256 から）。中身が変われば値が変わる。"""
        if self.conn is None:
            return {}
        acc: dict[str, int] = {}
        q = ("SELECT run, path, sha256 FROM files WHERE path IN (" + ",".join("?" * len(WATCHED)) + ")"
             " ORDER BY run, path")
        for run, path, sha in self.conn.execute(q, WATCHED):
            acc[run] = zlib.crc32(f"{path}:{sha}".encode(), acc.get(run, 0))
        return {n: float(acc.get(n, 0)) for n in self.names()}

# 特徴量の層 → 実行の種類
CROSS_LAYERS = ("cs", "rel", "ll")
LAYER_LABEL = {"adjusted": "調整後", "raw": "調整前"}
# ⚠ **日付をずらした偽薬**（実験側 `ail/runs.py` の `variant` が付ける末尾）。⚠ **本物と同じタイトルになるので、
# 見分けないと同じ名前の行が一覧に何十本も並び、どれが本物か分からなくなる**（2026-09-16）
_SHIFT = re.compile(r"_shift(\d+)$")


def shift_days_of(name: str, config: dict) -> int:
    """偽薬ならずらし幅（日）、本物なら 0。名前の末尾か config の `features.ex_shift_days` で見分ける。"""
    m = _SHIFT.search(name)
    try:
        by_config = int(((config or {}).get("features") or {}).get("ex_shift_days") or 0)
    except (TypeError, ValueError):
        by_config = 0
    return (int(m.group(1)) if m else 0) or by_config


def _read_json(data: bytes | None) -> dict:
    try:
        return json.loads(data.decode("utf-8")) if data is not None else {}
    except (UnicodeDecodeError, ValueError):
        return {}


def _read_summary(data: bytes | None) -> list[dict]:
    """`summary.csv` を辞書の一覧にする。⚠ **数に直せない欄は None のまま残す。**"""
    if data is None:
        return []
    try:
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))
    except (UnicodeDecodeError, csv.Error):
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
    """⚠ **実行の種類。** 先読みの検査は種類として別に立てる（一覧に混ぜないため）。"""
    return "先読みの検査" if leak else base_kind(config)


def title_of(config: dict, inputs: dict, leak: bool) -> str:
    """⚠ **タイトルは付けずに設定から組み立てる。** 手で書くと実行のたびにずれる。

    ⚠ **先読みの検査でも、どの実行の対照なのかが読めるようにする**
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

    ⚠ **条件は検証結果一覧（`ail/catalog.py`）と同じ。** `forced`（門前の手法も回した印。既定・
    `--ignore-gate`）で summary が無いのは「回したのに結果が無い」なので門前とは読まない。
    ⚠ 2026-09-14 から門は既定で止めないので、門前の実行は `--gate` で足切りしたときだけ生まれる。
    """
    return not summary and bool(gate.get("blocked")) and not gate.get("forced")


def load_run(store: Store, name: str) -> dict | None:
    """1 実行ぶん。⚠ **summary.csv が無い実行は「門前」だけ拾う**（rules.md 14-5）。

    ⚠ **門前は「計算していない」ではなく「回していない」。** 検証結果一覧には残るので、
    ⚠ **画面からも消さない**（隠さない）。実行の件数には数えないので `index` が別枠に出す。
    """
    summary = _read_summary(store.get(name, "summary.csv"))
    ch = _read_json(store.get(name, "checks.json"))
    gate = ch.get("gate") or {}
    gated = is_gated(summary, gate)
    if not summary and not gated:
        return None
    methods = gate_methods(gate)
    config, inputs = _read_json(store.get(name, "config.json")), _read_json(store.get(name, "inputs.json"))
    env = _read_json(store.get(name, "env.json"))
    leak = bool(ch.get("leak")) or name.endswith("_leak")
    shift = shift_days_of(name, config)
    best = ch.get("best") or {}
    # ⚠ 閾値つき売買の実行は「上乗せ」が対 B&H（edge_vs_bh。rules.md 13-7）。旧実行は対「常に上」
    folds, edge, dsr, breadth = (ch.get("folds"),
                                 ch.get("edge_vs_bh") or ch.get("edge_vs_drift"),
                                 ch.get("dsr"), ch.get("breadth"))
    gran, bar = granularity(config)
    return {
        "run_id": name,
        "title": title_of(config, inputs, leak) + (f"（偽薬: 日付 −{shift:,} 日）" if shift else ""),
        "kind": "偽薬（日付ずらし）" if shift else kind_of(config, leak),
        "leak": leak,
        "shift_days": shift,
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
        "started_at": env.get("started_at") or name.split("_")[0],
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
    """⚠ **DB が無くても落とさない**（git 管理外なので別環境では空になる）。"""
    with Store(runs_dir) as store:
        return [run for name in store.names() if (run := load_run(store, name))]


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
    """一覧。⚠ **スコアの降順。先読みの検査・偽薬・門前は別枠に出す**（混ぜると全部が嘘になる）。

    ⚠ **門前の実行は `total` / `positive` / `kinds` に数えない**（rules.md 14-5）。
    ⚠ **検証を回していないので「実行 N 件」に足すと水増しになる**（n_trials に数えないのと同じ）。
    ⚠ **日付をずらした偽薬も数えない**（本物と同じ設定・同じタイトルなので、混ぜると本物が埋もれる）。
    """
    runs = [with_marks(r) for r in load_all(runs_dir)]
    gated = [r for r in runs if r["gated"]]
    scored = [r for r in runs if not r["gated"]]
    real = [r for r in scored if not r["leak"] and not r["shift_days"]]
    leak = [r for r in scored if r["leak"]]
    placebo = [r for r in scored if r["shift_days"] and not r["leak"]]
    key = lambda r: (r["score"] is not None, r["score"] or 0.0)   # noqa: E731
    real.sort(key=key, reverse=True)
    leak.sort(key=key, reverse=True)
    placebo.sort(key=key, reverse=True)
    gated.sort(key=lambda r: r["run_id"], reverse=True)           # 門前はスコアが無いので新しい順
    kinds: dict[str, int] = {}
    for r in real:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    return {
        "runs": real,
        "leak_runs": leak,
        "placebo_runs": placebo,
        "gated_runs": gated,
        "kinds": kinds,
        "total": len(real),
        "positive": sum(1 for r in real if (r["score"] or 0) > 0),
        "missing_checks": [r["run_id"] for r in runs if not r["has_checks"]],
        "runs_dir": str(Path(runs_dir) / DB_NAME),
    }


def one(runs_dir: Path, run_id: str) -> dict | None:
    """1 実行の詳細。⚠ **`..` を含む run_id は受けない。**"""
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return None
    with Store(runs_dir) as store:
        run = load_run(store, run_id) if store.has(run_id) else None
    return with_marks(run) if run else None
