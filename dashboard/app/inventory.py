"""データの在庫（`/data`）。実験（feature-discovery）が何をどれだけ持っているかを 1 画面にする。

⚠ **読むだけ。** 正本は実験側が書いたもので、この画面は数え直さない・書かない
（[dashboard.md §11](../../docs/specs/dashboard.md) が仕様。§10 と同じ立て方）:

  - 系列数・行数・期間     → `data/manifests/*.json`（取得時に検査して書かれた指紋）
  - 特徴量の表             → `data/features/*/<粒度>.meta.json`（`cli/build.py` の sidecar）
  - 枠（本命 ／ 偽薬）・仮説 → `config/dataset/*.toml`（⚠ **取得の前の宣言。** 結果を見て分類しない）
  - 銘柄の種別（会社株 ／ ETF）→ `config/universe/*.toml` の `groups`（銘柄名で引く）
  - ずらし幅・規約の判定    → `config/sources.toml`（コードとの一致は実験側のテストが固定）
  - 割り当て               → `config/exposure/*.toml`（⚠ **重みは全部【推測】**）

⚠ **標準ライブラリだけで書く**（pandas も tomli も入れない。TOML は 3.11+ の tomllib）。
⚠ **どのファイルが無くても落とさない**（g3plus には実験ディレクトリを COPY しない。空でも 200）。
"""

from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path

PERIOD_LABEL = {"d": "日足", "m": "1 分足", "series": "系列"}
LAYER_LABEL = {"raw": "調整前", "adjusted": "調整後"}
# universe の groups のキー → 画面の種別。⚠ **どの銘柄がどのグループかは config の宣言が正**
KIND_LABEL = {"company": "会社株", "etf": "ETF", "equity_like": "ETF（実質は株）"}
KIND_UNKNOWN = "分類なし"
# manifest の totals のうち「行数」以外は検査の引っかかり。0 でないものだけ画面に出す
NOT_ISSUES = ("rows",)


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return {}


def _date(ms) -> str | None:
    """ms → `YYYY-MM-DD`（UTC）。manifest の oldest_ms / newest_ms を日付にする。"""
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _series_rows(manifest: dict) -> list[dict]:
    out = []
    for name, s in (manifest.get("series") or {}).items():
        if not isinstance(s, dict):
            continue
        out.append({"name": name, "rows": s.get("rows"),
                    "oldest": _date(s.get("oldest_ms")), "newest": _date(s.get("newest_ms"))})
    return out


def _issues(manifest: dict) -> list[str]:
    """検査の引っかかり（totals の 0 でない項目）。⚠ **数え直さず manifest の値をそのまま出す。**"""
    totals = manifest.get("totals") or {}
    return [f"{k} {v:,}" for k, v in totals.items()
            if k not in NOT_ISSUES and isinstance(v, (int, float)) and v]


def _span(series: list[dict]) -> tuple[str | None, str | None]:
    oldest = min((s["oldest"] for s in series if s["oldest"]), default=None)
    newest = max((s["newest"] for s in series if s["newest"]), default=None)
    return oldest, newest


# ---------------------------------------------------------------- 宣言（config）


def load_sources(path: Path) -> dict[str, dict]:
    """`config/sources.toml`。取得元 → ずらし幅・公表の遅れ・規約の判定。"""
    return {src: entry for src, entry in _read_toml(path).items() if isinstance(entry, dict)}


def load_universes(config_dir: Path) -> list[dict]:
    """`config/universe/*.toml`。銘柄の集合と、⚠ **種別（会社株 ／ ETF）の宣言（`groups`）**。"""
    out = []
    if not config_dir.is_dir():
        return out
    for path in sorted(config_dir.glob("*.toml")):
        conf = _read_toml(path)
        if not conf:
            continue
        groups = {k: list(v) for k, v in (conf.get("groups") or {}).items()
                  if isinstance(v, list) and v}
        out.append({"name": conf.get("name") or path.stem,
                    "description": conf.get("description"),
                    "selected_on": conf.get("selected_on"),
                    "survivorship_bias": bool(conf.get("survivorship_bias")),
                    "groups": groups})
    return out


def kind_map(universes: list[dict]) -> dict[str, str]:
    """銘柄 → 種別。⚠ **universe の宣言を写すだけ**（画面が銘柄名から推測しない）。"""
    kinds: dict[str, str] = {}
    for u in universes:
        for group, symbols in u["groups"].items():
            label = KIND_LABEL.get(group, group)
            for s in symbols:
                kinds.setdefault(s, label)
    return kinds


def kind_summary(series: list[dict], kinds: dict[str, str]) -> list[tuple[str, int]]:
    """1 層の銘柄を種別ごとに数える（多い順。宣言に無い銘柄は「分類なし」で隠さない）。"""
    count: dict[str, int] = {}
    for s in series:
        k = kinds.get(s["name"], KIND_UNKNOWN)
        count[k] = count.get(k, 0) + 1
    return sorted(count.items(), key=lambda kv: -kv[1])


def load_datasets(config_dir: Path) -> list[dict]:
    """`config/dataset/*.toml`。⚠ **枠（role）と仮説（hypothesis）はここの宣言を写すだけ。**"""
    out = []
    if not config_dir.is_dir():
        return out
    for path in sorted(config_dir.glob("*.toml")):
        conf = _read_toml(path)
        if not conf:
            continue
        name = conf.get("name") or path.stem
        blocks = [b for b in conf.get("series") or [] if isinstance(b, dict)]
        out.append({"name": name, "period": conf.get("period"),
                    "source": conf.get("source"), "universe": conf.get("universe"),
                    "days": conf.get("days"), "adjust": conf.get("adjust"), "series": blocks})
    return out


def declaration_of(datasets: list[dict], dataset: str, source: str) -> dict:
    """dataset と取得元から、config が宣言した枠・仮説を引く。無ければ空。"""
    for d in datasets:
        if d["name"] != dataset:
            continue
        for b in d["series"]:
            if b.get("source") == source:
                return b
    return {}


def load_exposures(config_dir: Path) -> list[dict]:
    """`config/exposure/*.toml`。どの銘柄にどの災害の重みが付いているか。⚠ **全部【推測】。**"""
    out = []
    if not config_dir.is_dir():
        return out
    for path in sorted(config_dir.glob("*.toml")):
        conf = _read_toml(path)
        if not conf:
            continue
        channels = [c for c in conf.get("channel") or [] if isinstance(c, dict)]
        tables = {}
        for table, rows in (conf.get("weights") or {}).items():
            if not isinstance(rows, dict):
                continue
            entries = []
            for symbol, w in rows.items():
                if isinstance(w, dict):
                    parts = sorted(w.items(), key=lambda kv: -kv[1])
                    entries.append({"symbol": symbol, "total": round(sum(w.values()), 4),
                                    "detail": " · ".join(f"{k} {v:g}" for k, v in parts)})
                else:
                    entries.append({"symbol": symbol, "total": w, "detail": f"{w:g}"})
            entries.sort(key=lambda e: -(e["total"] or 0))
            tables[table] = entries
        out.append({"name": conf.get("name") or path.stem, "note": conf.get("note"),
                    "selected_on": conf.get("selected_on"), "hindsight": bool(conf.get("hindsight")),
                    "channels": channels, "weights": tables,
                    "symbols": sorted({e["symbol"] for t in tables.values() for e in t})})
    return out


# ---------------------------------------------------------------- 実測（manifest / meta）


def load_manifests(manifests_dir: Path) -> list[dict]:
    if not manifests_dir.is_dir():
        return []
    out = []
    for path in sorted(manifests_dir.glob("*.json")):
        m = _read_json(path)
        if not m.get("layer"):
            continue
        series = _series_rows(m)
        oldest, newest = _span(series)
        out.append({
            "file": path.name,
            "layer": m.get("layer"),
            "period": m.get("period"),
            "period_label": PERIOD_LABEL.get(m.get("period"), m.get("period") or "—"),
            "source": m.get("source"),
            "dataset": m.get("dataset"),
            "role": m.get("role"),
            "note": m.get("note"),
            "written_at": m.get("written_at"),
            "symbols": m.get("symbols"),
            "rows": (m.get("totals") or {}).get("rows"),
            "oldest": oldest,
            "newest": newest,
            "issues": _issues(m),
            # adjusted 層だけが持つ修復の内訳（無ければ None のまま）
            "repaired_breaks": m.get("repaired_breaks"),
            "kept_as_real_move": m.get("kept_as_real_move"),
            "rows_rescaled": m.get("rows_rescaled"),
            "dividend_adjusted": m.get("dividend_adjusted"),
            "series": series,
        })
    return out


def load_features(features_dir: Path) -> list[dict]:
    """`data/features/<実験>/<粒度>.meta.json`。特徴量の表の指紋。"""
    if not features_dir.is_dir():
        return []
    out = []
    for meta_path in sorted(features_dir.glob("*/*.meta.json")):
        m = _read_json(meta_path)
        if not m:
            continue
        out.append({
            "experiment": m.get("experiment") or meta_path.parent.name,
            "layer": m.get("layer"),
            "layer_label": LAYER_LABEL.get(m.get("layer"), m.get("layer") or "—"),
            "period_label": PERIOD_LABEL.get(m.get("period"), m.get("period") or "—"),
            "leak": bool(m.get("leak")) or meta_path.parent.name.endswith("_leak"),
            "rows": m.get("rows"),
            "features": m.get("features"),
            "built_at": m.get("built_at"),
        })
    out.sort(key=lambda f: (f["leak"], f["experiment"]))
    return out


# ---------------------------------------------------------------- 画面の形に組む


def index(settings) -> dict:
    sources = load_sources(settings.sources_config)
    datasets = load_datasets(settings.dataset_config_dir)
    universes = load_universes(settings.universe_config_dir)
    manifests = load_manifests(settings.manifests_dir)
    features = load_features(settings.features_dir)
    exposures = load_exposures(settings.exposure_config_dir)
    kinds = kind_map(universes)

    def label_of(src) -> str:
        return (sources.get(src) or {}).get("label") or src or "—"

    # 足（価格）と外部系列に分ける。外部系列は period == "series"
    bars, external = [], []
    for m in manifests:
        if m["period"] == "series":
            decl = declaration_of(datasets, m["dataset"], m["source"])
            src = sources.get(m["source"]) or {}
            # ⚠ 枠は config の宣言が正。manifest（取得時の写し）と食い違ったら画面に ⚠ を出す
            role = decl.get("role") or m["role"]
            external.append({**m, "source_label": label_of(m["source"]),
                             "role": role,
                             "role_mismatch": bool(decl.get("role") and m["role"]
                                                   and decl["role"] != m["role"]),
                             "hypothesis": decl.get("hypothesis"),
                             "lag_days": src.get("lag_days"),
                             "publish_note": src.get("publish_note"),
                             "terms": src.get("terms"),
                             "terms_note": src.get("terms_note")})
        else:
            # ⚠ 種別（会社株 ／ ETF）は universe の宣言を銘柄名で引く。宣言に無ければ「分類なし」
            series = [{**s, "kind": kinds.get(s["name"], KIND_UNKNOWN)} for s in m["series"]]
            bars.append({**m, "series": series,
                         "layer_label": LAYER_LABEL.get(m["layer"], m["layer"]),
                         "source_label": label_of(m["source"]),
                         "kinds": kind_summary(series, kinds)})

    bars.sort(key=lambda m: (m["layer"] != "raw", m["period"] != "d"))
    external.sort(key=lambda m: (m["role"] != "本命", m["source"] or ""))

    source_rows = [{"source": src, **entry} for src, entry in sources.items()]
    role_count = {}
    for e in external:
        role_count[e["role"] or "—"] = role_count.get(e["role"] or "—", 0) + (e["symbols"] or 0)

    return {
        "bars": bars,
        "external": external,
        "features": [f for f in features if not f["leak"]],
        "leak_features": [f for f in features if f["leak"]],
        "sources": source_rows,
        "universes": universes,
        "exposures": exposures,
        "external_roles": role_count,
        "total_rows": sum(m["rows"] or 0 for m in manifests),
        "exp_dir": str(settings.exp_dir),
        "empty": not (manifests or features or source_rows or universes or exposures),
    }
