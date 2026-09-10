#!/usr/bin/env python3
"""vibeboard のカスタムタブ「検証」「データ」の中身を出す小さなサーバ。

vibeboard 本体が `/ext/<name>/...` でこのサーバへ中継する（プラン:
docs/plans/vibeboard-experiments-tabs.md）。読むものと読み方は管理画面と同じで、
`app/experiments.py`（runs/ の一覧と検査）と `app/inventory.py`（データの在庫）を
そのまま import する。⚠ **読むだけ。資格情報・発注系のコードは import しない。**

  - `/experiments/api/sidebar` ・ `/experiments/view?item=<run_id|overview>` ・ `/experiments/api/watch`
  - `/data/api/sidebar` ・ `/data/view?item=<節>` ・ `/data/api/watch`

⚠ **標準ライブラリだけで書く**（venv 不要。vibeboard の sidecar が `python3` で起こす）。
⚠ **bind は 127.0.0.1 固定**。外に出る経路は vibeboard の中継だけ。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DASHBOARD_DIR = Path(__file__).resolve().parent
REPO_ROOT = DASHBOARD_DIR.parent
sys.path.insert(0, str(DASHBOARD_DIR))

from app import experiments, inventory  # noqa: E402

DEFAULT_PORT = 3015
WATCH_INTERVAL_S = 5.0
PING_INTERVAL_S = 30.0

# データの画面の節。⚠ **id はサイドバーと view で共有する**
DATA_SECTIONS = [
    ("overview", "概要"),
    ("bars", "足（価格）"),
    ("external", "外部系列"),
    ("features", "特徴量の表"),
    ("sources", "規約とずらし幅"),
    ("universes", "銘柄の集合"),
    ("exposures", "割り当て"),
]


class ExpPaths:
    """inventory.index() が要求する settings の形（読み場所だけ）。"""

    def __init__(self, exp_dir: Path):
        self.exp_dir = exp_dir

    @property
    def manifests_dir(self) -> Path:
        return self.exp_dir / "data" / "manifests"

    @property
    def features_dir(self) -> Path:
        return self.exp_dir / "data" / "features"

    @property
    def dataset_config_dir(self) -> Path:
        return self.exp_dir / "config" / "dataset"

    @property
    def exposure_config_dir(self) -> Path:
        return self.exp_dir / "config" / "exposure"

    @property
    def universe_config_dir(self) -> Path:
        return self.exp_dir / "config" / "universe"

    @property
    def sources_config(self) -> Path:
        return self.exp_dir / "config" / "sources.toml"


# ---------------------------------------------------------------- 表示の部品


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def fmt(v, digits: int = 2) -> str:
    """数値は桁を丸め、無いものは —。"""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "あり" if v else "なし"
    if isinstance(v, float):
        return f"{v:,.{digits}f}".rstrip("0").rstrip(".") if v == v else "—"
    if isinstance(v, int):
        return f"{v:,}"
    return esc(v)


def page(title: str, body: str) -> str:
    """view の HTML を 1 枚に組む。⚠ 外部リソースなし・CSS は同梱。"""
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font: 13px/1.7 system-ui, sans-serif; margin: 16px; }}
 h1 {{ font-size: 16px; margin: 0 0 4px; }}
 h2 {{ font-size: 13px; margin: 18px 0 6px; border-bottom: 1px solid color-mix(in srgb, currentColor 25%, transparent); }}
 table {{ border-collapse: collapse; margin: 6px 0; }}
 th, td {{ border: 1px solid color-mix(in srgb, currentColor 25%, transparent);
           padding: 2px 8px; text-align: left; vertical-align: top; }}
 th {{ font-weight: 600; }}
 td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
 .meta {{ opacity: .65; font-size: 12px; }}
 .warn {{ color: #b8860b; }}
 details {{ margin: 4px 0; }}
 summary {{ cursor: pointer; }}
 code {{ font-size: 12px; }}
</style></head>
<body>
{body}
</body></html>"""


def table(headers: list[str], rows: list[list[str]], num_cols: set[int] = frozenset()) -> str:
    th = "".join(f"<th{' class=num' if i in num_cols else ''}>{h}</th>" for i, h in enumerate(headers))
    trs = []
    for r in rows:
        tds = "".join(f"<td{' class=num' if i in num_cols else ''}>{c}</td>" for i, c in enumerate(r))
        trs.append(f"<tr>{tds}</tr>")
    return f"<table><tr>{th}</tr>{''.join(trs)}</table>"


def kv_table(pairs: list[tuple[str, str]]) -> str:
    return "<table>" + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in pairs) + "</table>"


# ---------------------------------------------------------------- 検証（runs/）


def exp_sidebar(runs_dir: Path) -> dict:
    idx = experiments.index(runs_dir)
    items = [{"id": "overview", "label": "まとめ",
              "sub": f"検証 {idx['total']} 件・純利>0 は {idx['positive']} 件", "group": "まとめ"}]
    # ⚠ index() はスコアの降順。サイドバーは種類でまとめないと group 見出しが繰り返されるので、
    # 種類の並び（kinds の順）を保ったまま各種類の中をスコア順にする
    for kind in idx["kinds"]:
        for r in idx["runs"]:
            if r["kind"] != kind:
                continue
            items.append({
                "id": r["run_id"], "label": r["title"], "sub": r["run_id"],
                "group": r["kind"],
                "badge": ("—" if r["score"] is None else f"{r['score']:+.2f}bp"),
            })
    for r in idx["leak_runs"]:
        items.append({"id": r["run_id"], "label": r["title"], "sub": r["run_id"],
                      "group": "先読みの検査",
                      "badge": ("—" if r["score"] is None else f"{r['score']:+.2f}bp")})
    return {"items": items}


def _marks_row(run: dict) -> str:
    marks = run.get("marks") or {}
    return table(list(marks.keys()), [[esc(v) for v in marks.values()]])


def exp_overview_html(runs_dir: Path) -> str:
    idx = experiments.index(runs_dir)
    body = ["<h1>検証のまとめ</h1>",
            f"<div class='meta'>{esc(idx['runs_dir'])}</div>"]
    body.append(kv_table([
        ("検証（先読みの検査を除く）", fmt(idx["total"])),
        ("スコア（最良手法の純利 bp）が正", fmt(idx["positive"])),
        ("種類", " ・ ".join(f"{esc(k)} {v} 件" for k, v in idx["kinds"].items()) or "—"),
        ("先読みの検査", fmt(len(idx["leak_runs"]))),
        ("checks.json が無い実行", esc(", ".join(idx["missing_checks"]) or "なし")),
    ]))
    body.append("<h2>一覧（スコアの降順）</h2>")
    rows = []
    for r in idx["runs"] + idx["leak_runs"]:
        marks = r.get("marks") or {}
        rows.append([esc(r["title"]), esc(r["run_id"]),
                     ("—" if r["score"] is None else f"{r['score']:+.2f}"),
                     esc(" ".join(marks.values()))])
    body.append(table(["検証", "実行", "純利bp", "層 純利 fold 上乗せ DSR"], rows, {2}))
    body.append("<p class='meta'>スコアは最良手法（基準線を除く）の純利 bp。"
                "1 つの数字なので、検査の列（fold の符号・上乗せ t・実効標本数・デフレーテッド SR）を必ず横に見る。</p>")
    return page("検証のまとめ", "\n".join(body))


def exp_run_html(runs_dir: Path, run_id: str) -> str | None:
    run = experiments.one(runs_dir, run_id)
    if not run:
        return None
    body = [f"<h1>{esc(run['title'])}</h1>",
            f"<div class='meta'>{esc(run['run_id'])} ・ 開始 {esc(run['started_at'])}"
            f" ・ commit {esc(run['commit'] or '—')}</div>"]
    body.append("<h2>検査</h2>")
    body.append(_marks_row(run))
    folds, edge, dsr, breadth = run["folds"], run["edge"], run["dsr"], run["breadth"]
    checks: list[tuple[str, str]] = [
        ("スコア（最良手法の純利 bp）", "—" if run["score"] is None else f"{run['score']:+.2f}"),
        ("最良手法", esc((run["best"] or {}).get("手法") or "—")),
    ]
    if folds:
        checks.append(("fold の符号", f"{fmt(folds.get('positive'))} / {fmt(folds.get('folds'))} が正"))
    if edge:
        checks.append(("基準線への上乗せ t", fmt(edge.get("t"))))
    if dsr:
        checks.append(("デフレーテッド SR", fmt(dsr.get("DSR"), 3)))
    if breadth:
        checks.append(("実効標本数", esc(json.dumps(breadth, ensure_ascii=False))))
    if run["drift_gross"] is not None:
        checks.append(("基準線（常に上）の粗利 bp", fmt(run["drift_gross"])))
    body.append(kv_table(checks))
    if run["panel_note"]:
        body.append(f"<p class='warn'>⚠ {esc(run['panel_note'])}</p>")
    body.append("<h2>手法ごとの成績（summary.csv）</h2>")
    rows = [[esc(s["手法"]), fmt(s["本数"], 0), fmt(s["的中率"], 3), fmt(s["IC"], 3),
             fmt(s["粗利bp"]), fmt(s["純利bp"]), fmt(s["fold数"], 0)] for s in run["summary"]]
    body.append(table(["手法", "本数", "的中率", "IC", "粗利bp", "純利bp", "fold数"],
                      rows, {1, 2, 3, 4, 5, 6}))
    body.append("<h2>設定</h2>")
    body.append(kv_table([
        ("種類", esc(run["kind"])), ("粒度", esc(run["gran"])), ("先", esc(run["horizon"])),
        ("層", esc(run["layer_label"])), ("特徴量の層", esc(run["feature_layers"])),
        ("特徴量", fmt(run["features"], 0)), ("銘柄", fmt(run["symbols"], 0)),
        ("行数", fmt(run["rows"], 0)), ("k", fmt(run["k"], 0)),
        ("費用 bp", fmt(run["cost_bp"])), ("seed", fmt(run["seed"], 0)),
    ]))
    return page(run["title"], "\n".join(body))


# ---------------------------------------------------------------- データ（在庫）


def data_sidebar(paths: ExpPaths) -> dict:
    inv = inventory.index(paths)
    subs = {
        "overview": f"合計 {fmt(inv['total_rows'], 0)} 行",
        "bars": f"{len(inv['bars'])} 表",
        "external": f"{len(inv['external'])} 表 ・ " + " ／ ".join(
            f"{esc(k)} {v} 本" for k, v in inv["external_roles"].items()),
        "features": f"{len(inv['features'])} 表（先読み {len(inv['leak_features'])}）",
        "sources": f"{len(inv['sources'])} 取得元",
        "universes": f"{len(inv['universes'])} 集合",
        "exposures": f"{len(inv['exposures'])} 定義",
    }
    items = [{"id": sec, "label": label, "sub": subs.get(sec, ""), "group": "データの在庫"}
             for sec, label in DATA_SECTIONS]
    return {"items": items}


def _series_details(series: list[dict]) -> str:
    rows = [[esc(s["name"]), esc(s.get("kind") or ""), fmt(s["rows"], 0),
             esc(s["oldest"] or "—"), esc(s["newest"] or "—")] for s in series]
    return (f"<details><summary>系列 {len(series)} 本</summary>"
            + table(["系列", "種別", "行数", "最古", "最新"], rows, {2}) + "</details>")


def data_section_html(paths: ExpPaths, section: str) -> str | None:
    inv = inventory.index(paths)
    label = dict(DATA_SECTIONS).get(section)
    if label is None:
        return None
    body = [f"<h1>{esc(label)}</h1>", f"<div class='meta'>{esc(inv['exp_dir'])}</div>"]
    if inv["empty"]:
        body.append("<p>実験ディレクトリが空（またはこの環境に無い）。</p>")
        return page(label, "\n".join(body))

    if section == "overview":
        body.append(kv_table([
            ("行数の合計（manifest の写し）", fmt(inv["total_rows"], 0)),
            ("足（価格）", f"{len(inv['bars'])} 表"),
            ("外部系列", f"{len(inv['external'])} 表 ・ " + " ／ ".join(
                f"{esc(k)} {v} 本" for k, v in inv["external_roles"].items())),
            ("特徴量の表", f"{len(inv['features'])} 表（先読みの検査 {len(inv['leak_features'])}）"),
            ("取得元（規約）", fmt(len(inv["sources"]), 0)),
            ("銘柄の集合", fmt(len(inv["universes"]), 0)),
            ("割り当て", fmt(len(inv["exposures"]), 0)),
        ]))
        body.append("<p class='meta'>数字はすべて実験側の manifest / config の写し。この画面は数え直さない。</p>")
    elif section == "bars":
        for m in inv["bars"]:
            body.append(f"<h2>{esc(m['layer_label'])} ・ {esc(m['period_label'])} ・ {esc(m['source_label'])}</h2>")
            pairs = [("dataset", esc(m["dataset"])), ("銘柄", fmt(m["symbols"], 0)),
                     ("行数", fmt(m["rows"], 0)), ("期間", f"{esc(m['oldest'] or '—')} 〜 {esc(m['newest'] or '—')}"),
                     ("種別", " ／ ".join(f"{esc(k)} {v}" for k, v in m["kinds"]) or "—"),
                     ("書かれた時刻", esc(m["written_at"] or "—"))]
            for key, lab in (("repaired_breaks", "修復した断絶"), ("kept_as_real_move", "実際の値動きとして残した"),
                             ("rows_rescaled", "水準を合わせた行"), ("dividend_adjusted", "配当調整")):
                if m.get(key) is not None:
                    pairs.append((lab, fmt(m[key], 0)))
            if m["issues"]:
                pairs.append(("検査の引っかかり", "<span class='warn'>⚠ " + esc(" ／ ".join(m["issues"])) + "</span>"))
            body.append(kv_table(pairs))
            body.append(_series_details(m["series"]))
    elif section == "external":
        rows = []
        for e in inv["external"]:
            role = esc(e["role"] or "—") + (" <span class='warn'>⚠ manifest と不一致</span>" if e["role_mismatch"] else "")
            rows.append([esc(e["source_label"]), role, fmt(e["symbols"], 0), fmt(e["rows"], 0),
                         f"{esc(e['oldest'] or '—')} 〜 {esc(e['newest'] or '—')}",
                         fmt(e["lag_days"], 0), esc(e["hypothesis"] or "—")])
        body.append(table(["取得元", "枠", "系列", "行数", "期間", "ずらし(日)", "仮説（取得の前の宣言）"], rows, {2, 3, 5}))
        body.append("<p class='meta'>枠（本命 ／ 偽薬）と仮説は config/dataset の宣言の写し。結果を見てからの分類はしない。</p>")
        for e in inv["external"]:
            if e.get("publish_note") or e.get("terms_note"):
                body.append(f"<details><summary>{esc(e['source_label'])} の注記</summary><p>"
                            + esc(e.get("publish_note") or "") + " " + esc(e.get("terms_note") or "") + "</p></details>")
    elif section == "features":
        for title, feats in (("検証に使う表", inv["features"]), ("先読みの検査の表", inv["leak_features"])):
            if not feats:
                continue
            body.append(f"<h2>{title}</h2>")
            rows = [[esc(f["experiment"]), esc(f["layer_label"]), esc(f["period_label"]),
                     fmt(f["rows"], 0), fmt(f["features"], 0), esc(f["built_at"] or "—")] for f in feats]
            body.append(table(["実験", "層", "粒度", "行数", "特徴量", "作られた時刻"], rows, {3, 4}))
    elif section == "sources":
        rows = []
        for s in inv["sources"]:
            rows.append([esc(s["source"]), esc(s.get("label") or "—"), fmt(s.get("lag_days"), 0),
                         esc(s.get("terms") or "—"), esc(s.get("terms_note") or s.get("publish_note") or "—")])
        body.append(table(["取得元", "表示名", "ずらし(日)", "規約の判定", "注記"], rows, {2}))
        body.append("<p class='meta'>ずらし幅と規約は config/sources.toml の写し。コードとの一致は実験側のテストが固定する。</p>")
    elif section == "universes":
        for u in inv["universes"]:
            body.append(f"<h2>{esc(u['name'])}</h2>")
            body.append(kv_table([
                ("説明", esc(u["description"] or "—")),
                ("選定日", esc(u["selected_on"] or "—")),
                ("生存バイアス", "<span class='warn'>⚠ あり</span>" if u["survivorship_bias"] else "なし"),
            ]))
            for group, symbols in u["groups"].items():
                label_g = inventory.KIND_LABEL.get(group, group)
                body.append(f"<details><summary>{esc(label_g)} {len(symbols)} 銘柄</summary>"
                            f"<p><code>{esc(' '.join(symbols))}</code></p></details>")
    elif section == "exposures":
        for x in inv["exposures"]:
            body.append(f"<h2>{esc(x['name'])}</h2>")
            pairs = [("注記", esc(x["note"] or "—")), ("選定日", esc(x["selected_on"] or "—")),
                     ("後知恵", "<span class='warn'>⚠ あり（重みは全部【推測】）</span>" if x["hindsight"] else "なし"),
                     ("銘柄", esc(" ".join(x["symbols"])) or "—")]
            body.append(kv_table(pairs))
            if x["channels"]:
                rows = [[esc(c.get("name") or "—"), esc(c.get("hypothesis") or c.get("note") or "—")]
                        for c in x["channels"]]
                body.append(f"<details><summary>経路 {len(x['channels'])} 本</summary>"
                            + table(["経路", "仮説"], rows) + "</details>")
            for tname, entries in x["weights"].items():
                rows = [[esc(e["symbol"]), fmt(e["total"], 4), esc(e["detail"])] for e in entries]
                body.append(f"<details><summary>重み: {esc(tname)}（{len(entries)} 銘柄）</summary>"
                            + table(["銘柄", "合計", "内訳"], rows, {1}) + "</details>")
    return page(label, "\n".join(body))


# ---------------------------------------------------------------- 変更の見張り（SSE）


def exp_fingerprint(runs_dir: Path) -> dict[str, float]:
    """run ごとの更新時刻。⚠ dir の mtime はファイルの上書きでは動かないので、中の記録も見る。"""
    out: dict[str, float] = {}
    if not runs_dir.is_dir():
        return out
    for d in runs_dir.iterdir():
        if not d.is_dir():
            continue
        mt = d.stat().st_mtime
        for name in ("summary.csv", "checks.json", "config.json", "inputs.json", "env.json"):
            try:
                mt = max(mt, (d / name).stat().st_mtime)
            except OSError:
                pass
        out[d.name] = mt
    return out


def data_fingerprint(paths: ExpPaths) -> dict[str, float]:
    out: dict[str, float] = {}
    globs = [(paths.manifests_dir, "*.json"), (paths.features_dir, "*/*.meta.json"),
             (paths.dataset_config_dir, "*.toml"), (paths.universe_config_dir, "*.toml"),
             (paths.exposure_config_dir, "*.toml")]
    for base, pat in globs:
        if base.is_dir():
            for p in base.glob(pat):
                try:
                    out[str(p.relative_to(paths.exp_dir))] = p.stat().st_mtime
                except OSError:
                    pass
    try:
        out["config/sources.toml"] = paths.sources_config.stat().st_mtime
    except OSError:
        pass
    return out


# ---------------------------------------------------------------- HTTP


def make_handler(runs_dir: Path, paths: ExpPaths):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt_, *args):  # 1 行ログ（vibeboard 側で [name] が付く）
            sys.stdout.write(f"{self.address_string()} {fmt_ % args}\n")
            sys.stdout.flush()

        def _send(self, status: int, ctype: str, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if ctype.startswith("text/html"):
                # vibeboard が /ext/<name> で中継するので iframe は同一オリジン
                self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802（http.server の流儀）
            url = urlparse(self.path)
            parts = url.path.strip("/").split("/", 1)
            tab, rest = parts[0], (parts[1] if len(parts) > 1 else "")
            if url.path == "/":
                self._send(200, "text/plain; charset=utf-8", "vibetab ok\n")
                return
            if tab not in ("experiments", "data"):
                self._send(404, "text/plain; charset=utf-8", "not found\n")
                return
            if rest == "":
                self._send(200, "text/plain; charset=utf-8", f"vibetab {tab} ok\n")
            elif rest == "api/sidebar":
                sidebar = exp_sidebar(runs_dir) if tab == "experiments" else data_sidebar(paths)
                self._send(200, "application/json; charset=utf-8",
                           json.dumps(sidebar, ensure_ascii=False))
            elif rest == "view":
                item = (parse_qs(url.query).get("item") or [""])[0]
                if tab == "experiments":
                    body = exp_overview_html(runs_dir) if item == "overview" else exp_run_html(runs_dir, item)
                else:
                    body = data_section_html(paths, item)
                if body is None:
                    self._send(404, "text/plain; charset=utf-8", "unknown item\n")
                else:
                    self._send(200, "text/html; charset=utf-8", body)
            elif rest == "api/watch":
                self._watch(tab)
            else:
                self._send(404, "text/plain; charset=utf-8", "not found\n")

        def _watch(self, tab: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            take = (lambda: exp_fingerprint(runs_dir)) if tab == "experiments" \
                else (lambda: data_fingerprint(paths))
            last = take()
            last_ping = time.monotonic()
            try:
                self.wfile.write(b": hello\n\n")
                self.wfile.flush()
                while True:
                    time.sleep(WATCH_INTERVAL_S)
                    now = take()
                    if now != last:
                        changed = [k for k in now if now.get(k) != last.get(k)]
                        removed = [k for k in last if k not in now]
                        self.wfile.write(b"event: sidebar\ndata: {}\n\n")
                        # 表示中の item だけが reload されるので、多めに投げて構わない
                        ids = (changed + removed) if tab == "experiments" \
                            else [sec for sec, _ in DATA_SECTIONS]
                        for i in ids:
                            payload = json.dumps({"id": i}, ensure_ascii=False)
                            self.wfile.write(f"event: item-changed\ndata: {payload}\n\n".encode())
                        if tab == "experiments":
                            self.wfile.write(b'event: item-changed\ndata: {"id": "overview"}\n\n')
                        last = now
                        self.wfile.flush()
                    if time.monotonic() - last_ping >= PING_INTERVAL_S:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_ping = time.monotonic()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("AIL_VIBETAB_PORT") or DEFAULT_PORT))
    ap.add_argument("--runs-dir", default=os.environ.get("AIL_RUNS_DIR")
                    or str(REPO_ROOT / "experiments" / "feature-discovery" / "runs"))
    ap.add_argument("--exp-dir", default=os.environ.get("AIL_EXP_DIR")
                    or str(REPO_ROOT / "experiments" / "feature-discovery"))
    args = ap.parse_args(argv)

    runs_dir = Path(args.runs_dir).resolve()
    paths = ExpPaths(Path(args.exp_dir).resolve())
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(runs_dir, paths))
    except OSError as e:
        # sidecar は起動前に baseUrl を叩くが、直後の 2 本目とは競走になり得る。
        # 二重起動は静かに引く（先に居るほうが正）
        print(f"[vibetab] port {args.port} を bind できない（{e}）。先に居るものに任せて終了する")
        return
    srv.daemon_threads = True
    print(f"[vibetab] listening on http://127.0.0.1:{args.port} "
          f"(runs={runs_dir}, exp={paths.exp_dir})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
