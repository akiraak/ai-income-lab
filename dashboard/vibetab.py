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

# 層を除く 4 検査（純利・fold・上乗せ・DSR）。「合格」＝ この 4 つが全部 ✅
SCORE_MARKS = ("純利", "fold", "上乗せ", "DSR")

# 検証方法の 4 軸。⚠ 画面向けの文言だけ（正本は rules.md §6・§7・§9 と dashboard.md §10）。
# ⚠ 文言に数字を書かない。数字は checks.json の写し（下の節）から出す
AXIS_NOTES = [
    ("種類", "特徴量に他銘柄の層（cs・rel・ll）が入るか。先読みの検査は対照実験なので別枠"),
    ("粒度", "足の長さ"),
    ("先", "ラベルの地平（何本先の終値までのリターンを当てるか）"),
    ("層", "入力データの層。⚠ 調整前は分割調整の誤りを含むので、有効性の比較には使わない（層の検査が ⚠）"),
]

# 種類ごとの特徴（何を見るか ／ 強み ／ 弱み・注意）
KIND_TRAITS = [
    ("断面",
     "own に加えて、同じ足の他銘柄を見る層（cs・rel・ll）を特徴量に含める",
     "銘柄間の相対（どの銘柄が上がるか）を使える。行が銘柄 × 時刻に広がるぶん実効観測数が積み上がりやすい",
     "銘柄が揃わない足では意味が薄い。fold の境目を同じ足の他銘柄が跨ぐので、エンバーゴが要る"),
    ("プーリング",
     "各銘柄が自分の履歴（own。ex を足すことも）だけを見て、銘柄を縦に積む",
     "組が単純で、層・手法・費用の効き方を切り分けやすい",
     "own だけでは銘柄を増やしても「1 銘柄の実験を N 回」に近い。銘柄どうしは独立でないので、実効系列数まで割り引くと標本は見た目ほど増えない"),
    ("先読みの検査",
     "ラベルを混ぜた列（LEAK_）を 1 本足した対照実験。どの検証にも対で回す",
     "数字が跳ねなければ検証の配線（分割・パージ・コスト）が壊れている、を毎回確かめられる",
     "スコアが高いのは正常（そういう検査）。手法の成績としては読まない"),
]


def _passes(run: dict) -> bool:
    """層を除く 4 検査が全部 ✅ か。⚠ しきい値は `with_marks` が持つ（ここで二重に判定しない）。"""
    marks = run.get("marks") or {}
    return all(marks.get(k) == "✅" for k in SCORE_MARKS)


def _bp(v) -> str:
    return "—" if v is None else f"{v:+.2f}"


def exp_methods(idx: dict) -> list[dict]:
    """検証方法（種類・粒度・先・層の組）ごとの要約。

    ⚠ **数字は各組で最もスコアの高い実行の checks.json の写し**（`index()` はスコアの降順なので
    先頭が最良）。組で数えるのは実行の件数と ✅ の件数だけで、統計は計算しない。
    """
    groups: dict[tuple, dict] = {}
    for r in idx["runs"] + idx["leak_runs"]:
        key = (r["kind"], r["gran"], r["horizon"], r["layer_label"])
        g = groups.setdefault(key, {"label": "・".join(key), "kind": r["kind"],
                                    "count": 0, "passed": 0, "best": r})
        g["count"] += 1
        g["passed"] += 1 if _passes(r) else 0
    return list(groups.values())


def _traits_html(idx: dict) -> str:
    """① 検証方法とその特徴。軸の「いまの値」だけ runs/ から拾い、説明は静的な文言。"""
    values: dict[str, list] = {axis: [] for axis, _ in AXIS_NOTES}
    for r in idx["runs"] + idx["leak_runs"]:
        for axis, v in (("種類", r["kind"]), ("粒度", r["gran"]),
                        ("先", r["horizon"]), ("層", r["layer_label"])):
            if v not in values[axis]:
                values[axis].append(v)
    body = ["<h2>検証方法とその特徴</h2>",
            "<p class='meta'>検証方法 ＝ 種類・粒度・先・層の 4 軸の組。一覧のタイトルもこの組から"
            "組み立てている。正本は rules.md（§6 層・§7 先読み・§9 検証）と dashboard.md §10。</p>"]
    body.append(table(["軸", "いまの値", "意味"],
                      [[esc(axis), " ／ ".join(esc(v) for v in values[axis]) or "—", esc(note)]
                       for axis, note in AXIS_NOTES]))
    body.append(table(["種類", "何を見るか", "強み", "弱み・注意"],
                      [[esc(c) for c in row] for row in KIND_TRAITS]))
    return "\n".join(body)


def _methods_html(groups: list[dict]) -> str:
    """② 検証方法ごとの成績。数字は各組の最良実行の写し。"""
    body = ["<h2>検証方法ごとの成績</h2>"]
    if not groups:
        body.append("<p class='meta'>実行がまだ無い。</p>")
        return "\n".join(body)
    rows = []
    for g in groups:
        b = g["best"]
        folds = b.get("folds") or {}
        rows.append([
            esc(g["label"]), fmt(g["count"], 0), f"{g['passed']} / {g['count']}",
            _bp(b["score"]),
            (f"{fmt(folds.get('positive'))} / {fmt(folds.get('folds'))} 正" if folds else "—"),
            fmt((b.get("edge") or {}).get("t")),
            fmt((b.get("dsr") or {}).get("DSR"), 3),
            fmt((b.get("breadth") or {}).get("実効観測数"), 0),
        ])
    body.append(table(["検証方法", "実行", "4 検査 ✅", "最良 純利bp", "fold", "上乗せ t", "DSR", "実効観測数"],
                      rows, {1, 3, 5, 6, 7}))
    body.append("<p class='meta'>数字は各組で最もスコアの高い実行の checks.json の写し（組では数え直さない）。"
                "4 検査 ＝ 純利・fold・上乗せ・DSR（層を除く）。"
                "⚠ 組どうしは条件（銘柄・期間・列の数）が違うので、スコアの差が手法の差とは限らない。</p>")
    return "\n".join(body)


def _analysis_html(idx: dict, groups: list[dict]) -> str:
    """③ どの検証が有効か。⚠ 文面の分岐だけがここにあり、判定は marks の数え上げで決まる。"""
    real, leaks = idx["runs"], idx["leak_runs"]
    items: list[str] = []

    # 1) 配線: 先読みの検査（対照実験）が跳ねているか
    if not leaks:
        items.append("⏳ <b>配線の確認がまだ無い。</b>先読みの検査（対照実験）を先に回す。"
                     "跳ねる先読みが無いうちは、実検証の数字を読まない（rules.md §7）。")
    else:
        ng = [r for r in leaks if not _passes(r)]
        if ng:
            items.append("⚠ <b>跳ねない先読みの検査がある</b>（"
                         + "、".join(esc(r["run_id"]) for r in ng)
                         + "）。実検証の数字より先に、検証の配線を疑う（rules.md §7）。")
        else:
            scores = sorted(r["score"] for r in leaks if r["score"] is not None)
            rng = (f"純利 {_bp(scores[0])}〜{_bp(scores[-1])}bp" if len(scores) > 1
                   else f"純利 {_bp(scores[0])}bp" if scores else "スコアなし")
            items.append(f"✅ <b>配線は働いている。</b>先読みの検査 {len(leaks)} 件は 4 検査ぜんぶ ✅"
                         f"（{rng}）。わざと先読みさせるとこれだけ跳ねるので、"
                         "分割・パージ・コストの配線は先読みを見逃していない。")

    # 2) 実検証に「発見あり」と言えるものがあるか
    passed = [r for r in real if _passes(r)]
    if not real:
        items.append("⏳ 実検証の実行がまだ無い。")
    elif not passed:
        items.append(f"⚠ <b>「発見あり」と言える検証はまだ無い。</b>実検証 {len(real)} 件のうち"
                     f"純利 &gt; 0 は {idx['positive']} 件あるが、4 検査を同時に満たす実行は 0 件。"
                     "スコアが正でも、fold の符号が割れる・基準線への上乗せが小さい・DSR が低いうちは"
                     "偶然と区別できない（rules.md §11: 良い数字は根拠「中」が上限）。")
    else:
        items.append(f"✅ <b>4 検査を満たす実行が {len(passed)} 件ある</b>（"
                     + "、".join(f"{esc(r['title'])}〔{esc(r['run_id'])}〕" for r in passed)
                     + "）。⚠ n_trials は増え続けるので、確定は台帳（ledger.md）を正とする。")

    # 3) 組の比較（層 ✅ の実検証だけ。groups は最良スコアの降順に並んでいる）
    valid = [g for g in groups
             if g["kind"] != "先読みの検査" and (g["best"].get("marks") or {}).get("層") == "✅"]
    if len(valid) >= 2:
        a, b = valid[0], valid[1]
        ea = (a["best"].get("breadth") or {}).get("実効観測数")
        eb = (b["best"].get("breadth") or {}).get("実効観測数")
        marks_a = a["best"].get("marks") or {}
        items.append(f"スコアの上では <b>{esc(a['label'])}</b> が最良（{_bp(a['best']['score'])}bp・"
                     f"実効観測数 {fmt(ea, 0)}）。次点は {esc(b['label'])}"
                     f"（{_bp(b['best']['score'])}bp・{fmt(eb, 0)}）。実効観測数が大きい組ほど、"
                     "同じ強さの効きでも検査に乗りやすい。ただし最良の組の検査も "
                     f"{esc(' '.join(marks_a.values()))}（層 純利 fold 上乗せ DSR）で、上の判定は変わらない。")

    # 4) 層 ⚠ の組は比較から外す
    invalid = [g for g in groups if (g["best"].get("marks") or {}).get("層") == "⚠"]
    if invalid:
        items.append("⚠ <b>"
                     + "、".join(esc(g["label"]) for g in invalid)
                     + f" の {sum(g['count'] for g in invalid)} 件は有効性の比較から外す。</b>"
                     "調整前の層は分割調整の誤りを含む（層 ⚠）。過去の数字の再現用としてだけ残す。")

    return ("<h2>どの検証が有効か</h2>\n<ul>"
            + "".join(f"<li>{i}</li>" for i in items)
            + "</ul>\n<p class='meta'>この節は runs/ の写し（✅ / ⚠ / ⏳ は仕様 §10-3 の条件）から"
              "機械的に組む。run が増えれば文面も変わる。</p>")


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
    groups = exp_methods(idx)
    body = ["<h1>検証のまとめ</h1>",
            f"<div class='meta'>{esc(idx['runs_dir'])}</div>"]
    body.append(kv_table([
        ("検証（先読みの検査を除く）", fmt(idx["total"])),
        ("スコア（最良手法の純利 bp）が正", fmt(idx["positive"])),
        ("種類", " ・ ".join(f"{esc(k)} {v} 件" for k, v in idx["kinds"].items()) or "—"),
        ("先読みの検査", fmt(len(idx["leak_runs"]))),
        ("checks.json が無い実行", esc(", ".join(idx["missing_checks"]) or "なし")),
    ]))
    body.append(_traits_html(idx))
    body.append(_methods_html(groups))
    body.append(_analysis_html(idx, groups))
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
        ("最良手法", esc((run["best"] or {}).get("method") or "—")
                    + (f"（θ={(run['best'] or {}).get('閾値'):g}%）"
                       if (run.get("best") or {}).get("閾値") is not None else "")),
    ]
    threshold = run.get("style") == "threshold"
    if folds:
        checks.append(("fold の符号" + ("（対 B&H 上乗せ）" if threshold else ""),
                       f"{fmt(folds.get('positive'))} / {fmt(folds.get('folds'))} が正"))
    if edge:
        checks.append(("対 B&H の上乗せ t" if threshold else "基準線への上乗せ t",
                       fmt(edge.get("t"))))
    if dsr:
        checks.append(("デフレーテッド SR", fmt(dsr.get("DSR"), 3)))
    if breadth:
        checks.append(("実効標本数", esc(json.dumps(breadth, ensure_ascii=False))))
    if run["drift_gross"] is not None:
        checks.append(("基準線（常に上）の粗利 bp", fmt(run["drift_gross"])))
    body.append(kv_table(checks))
    if run["panel_note"]:
        body.append(f"<p class='warn'>⚠ {esc(run['panel_note'])}</p>")
    if run.get("by_threshold"):
        # 閾値つき売買（rules.md 13 章）。⚠ checks.json の写しを出すだけ（ここで数え直さない）
        body.append("<h2>閾値ごとの成績（3 水準とも載せる）</h2>")
        rows = []
        for th, e in run["by_threshold"].items():
            b, ed, ps = e.get("best") or {}, e.get("edge_vs_bh") or {}, e.get("per_symbol") or {}
            rows.append([
                f"{esc(th)}%", esc(b.get("method") or "—"), fmt(b.get("純利bp")),
                fmt(e.get("bh_純利bp")),
                (f"{ed.get('mean_bp', 0):+.2f}" + (f" (t={ed['t']:.2f})" if ed.get("t") is not None else "")
                 if ed else "—"),
                (f"{ed.get('positive')}/{ed.get('folds')} {esc(ed.get('pattern') or '')}" if ed else "—"),
                fmt((e.get("dsr") or {}).get("DSR"), 3),
                fmt(b.get("取引回数"), 0), fmt(b.get("保有日率")),
                (f"{ps.get('中央値bp', 0):+.2f} ／ 勝ち {ps.get('勝ち銘柄')}/{ps.get('銘柄数')}"
                 if ps else "—"),
            ])
        body.append(table(["θ", "最良手法", "純利bp", "B&H 純利", "上乗せ", "上乗せ fold",
                           "DSR", "取引/fold", "保有日率", "銘柄別 bp"], rows,
                          {2, 3, 4, 6, 7, 8}))
        body.append("<p class='meta'>⚠ 閾値は事前固定（rules.md 13-3。良かった閾値だけ報告しない）。"
                    "fold の符号は対 B&H の上乗せで見る（13-7）。「θ が高いほど良い」は"
                    "「取引しないだけ」の可能性があるので取引回数を必ず横に読む（13-10）。"
                    "銘柄別 bp は成果物（per_symbol.csv）で採否には使わない。</p>")
    body.append("<h2>手法ごとの成績（summary.csv）</h2>")
    if threshold:
        rows = [[esc(s["手法"]), fmt(s.get("閾値"), 0), fmt(s["本数"], 0), fmt(s["的中率"], 3),
                 fmt(s["IC"], 3), fmt(s["粗利bp"]), fmt(s["純利bp"]),
                 fmt(s.get("取引回数"), 0), fmt(s.get("保有日率"))] for s in run["summary"]]
        body.append(table(["手法", "θ", "本数", "的中率", "IC", "粗利bp", "純利bp",
                           "取引/fold", "保有日率"], rows, {1, 2, 3, 4, 5, 6, 7, 8}))
    else:
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


def _series_details(series: list[dict], with_kind: bool = True) -> str:
    """系列の一覧の details。足は種別つき、外部系列は種別なし（宣言が無いため）。"""
    if with_kind:
        rows = [[esc(s["name"]), esc(s.get("kind") or ""), fmt(s["rows"], 0),
                 esc(s["oldest"] or "—"), esc(s["newest"] or "—")] for s in series]
        return (f"<details><summary>系列 {len(series)} 本</summary>"
                + table(["系列", "種別", "行数", "最古", "最新"], rows, {2}) + "</details>")
    rows = [[f"<code>{esc(s['name'])}</code>", fmt(s["rows"], 0),
             f"{esc(s['oldest'] or '—')} 〜 {esc(s['newest'] or '—')}"] for s in series]
    return (f"<details><summary>系列の一覧（{len(series)}）</summary>"
            + table(["系列", "行", "期間"], rows, {1}) + "</details>")


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
        body.append("<p class='meta'><b>この画面は読むだけ。</b>系列数・行数・期間は取得時に実験側が書いた"
                    " manifest（data/manifests/*.json）の値をそのまま出す（CSV を開いて数え直さない）。"
                    "枠（本命 ／ 偽薬）と仮説は config/dataset/*.toml の宣言の写しで、結果を見て分類し直さない。"
                    "ずらし幅と規約の判定は config/sources.toml の宣言（コードとの一致は実験側のテストが固定）。</p>")
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
    elif section == "bars":
        for m in inv["bars"]:
            body.append(f"<h2>{esc(m['layer_label'])} ・ {esc(m['period_label'])} ・ {esc(m['source_label'])}</h2>")
            pairs = [("manifest", f"<code>{esc(m['file'])}</code>"), ("dataset", esc(m["dataset"])),
                     ("銘柄", fmt(m["symbols"], 0)),
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
        body.append("<p class='meta'>⚠ 種別（会社株 ／ ETF）は config/universe/*.toml の groups の宣言を"
                    "銘柄名で引いたもの（画面は銘柄名から推測しない）。"
                    "⚠ 調整前（raw）の日足は分割の断層を含みうる。検証に使うのは調整後（rules.md 2 章）。</p>")
    elif section == "external":
        rows = []
        for e in inv["external"]:
            role = f"<b>{esc(e['role'] or '—')}</b>" + (
                "<div class='warn'>⚠ config と manifest で枠が食い違う</div>" if e["role_mismatch"] else "")
            src = (esc(e["source_label"])
                   + f"<div class='meta'>{esc(e.get('dataset') or '')} ・ {esc(e.get('written_at') or '—')}</div>"
                   + _series_details(e["series"], with_kind=False))
            lag = f"{fmt(e['lag_days'], 0)} 日" if e.get("lag_days") else "—"
            if (e.get("lag_days") or 0) > 1:
                lag = f"<span class='warn'>{lag}</span>"
            hyp = esc(e["hypothesis"] or ("⚠ 値動きと因果を想定しない（偽薬）" if e["role"] == "偽薬" else "—"))
            if e.get("note"):
                hyp += f"<br>{esc(e['note'])}"
            rows.append([role, src, fmt(e["symbols"], 0), fmt(e["rows"], 0),
                         f"{esc(e['oldest'] or '—')} 〜 {esc(e['newest'] or '—')}",
                         lag, esc(e.get("terms") or "—"), hyp])
        body.append(table(["枠", "取得元", "系列", "行数", "期間", "ずらし幅", "規約", "仮説（取得の前の宣言）"],
                          rows, {2, 3}))
        body.append("<p class='meta'>枠（本命 ／ 偽薬）と仮説は config/dataset の宣言の写し。結果を見てからの分類はしない。"
                    "⚠ 偽薬（気象・地震）は値動きと因果を想定しない対照。選別手法がそれを選んだ割合が"
                    "偽発見率の実測になる（daily-data-sources.md §9）。<br>"
                    "⚠ ずらし幅 ＝ 足の日から何日前の時点で公表されている値を使うか。"
                    "NCEI Storm Events は公表が 101 日遅れる【実測】ので 120 日ずらす。</p>")
    elif section == "features":
        for title, feats in (("検証に使う表", inv["features"]), ("先読みの検査の表", inv["leak_features"])):
            if not feats:
                continue
            body.append(f"<h2>{title}</h2>")
            rows = [[esc(f["experiment"]),
                     (esc(f["layer_label"]) if f["layer"] == "adjusted"
                      else f"<span class='warn'>⚠ {esc(f['layer_label'])}</span>"),
                     esc(f["period_label"]),
                     fmt(f["rows"], 0), fmt(f["features"], 0), esc(f["built_at"] or "—")] for f in feats]
            body.append(table(["実験", "元の層", "粒度", "行数", "列数", "作成"], rows, {3, 4}))
        body.append("<p class='meta'>⚠ 先読みの検査用の表は、わざと未来の値を混ぜて配線を確かめるためのもの"
                    "（検証には使わない）。</p>")
    elif section == "sources":
        rows = []
        for s in inv["sources"]:
            rows.append([f"<b>{esc(s.get('label') or s['source'])}</b>"
                         f"<div class='meta'><code>{esc(s['source'])}</code></div>",
                         esc(s.get("terms") or "—"),
                         esc(s.get("terms_note") or "—"),
                         ((f"<b>{fmt(s.get('lag_days'), 0)} 日ずらす</b> — " if s.get("lag_days") else "")
                          + esc(s.get("publish_note") or "—"))])
        body.append(table(["取得元", "規約の判定", "根拠", "公表の遅れ ／ ずらし幅"], rows))
        body.append("<p class='meta'>ずらし幅と規約は config/sources.toml の写し。コードとの一致は実験側のテストが固定する。"
                    "⚠ 「要判断」で採っていない取得元（FRED・Open-Meteo・SILSO）はデータを持っていないので"
                    "この表に無い（daily-data-sources.md §2・§4 が正本）。</p>")
    elif section == "universes":
        for u in inv["universes"]:
            body.append(f"<h2>{esc(u['name'])}</h2>")
            body.append(kv_table([
                ("説明", esc(u["description"] or "—")),
                ("選定日", esc(u["selected_on"] or "—")),
                ("生存バイアス", "<span class='warn'>⚠ あり（選定時点で存在する銘柄から選んでいる）</span>"
                 if u["survivorship_bias"] else "なし"),
            ]))
            for group, symbols in u["groups"].items():
                label_g = inventory.KIND_LABEL.get(group, group)
                body.append(f"<details><summary>{esc(label_g)} {len(symbols)} 銘柄</summary>"
                            f"<p><code>{esc(' '.join(symbols))}</code></p></details>")
    elif section == "exposures":
        for x in inv["exposures"]:
            body.append(f"<h2>{esc(x['name'])}</h2>")
            note = ("⚠ <b>重みは全部【推測】</b>（出典のある売上の地域内訳ではない。"
                    f"{esc(x['selected_on'] or '—')} 時点の主業種と拠点から振ったもの）。")
            if x["hindsight"]:
                note += "⚠ <b>後知恵あり</b> — 検証の期間の中で起きた出来事を知って振っている（偽薬でも消えない限界）。"
            note += "⚠ 割り当てそのものが仮説であり、外れれば効かない。"
            body.append(f"<p class='warn'>{note}</p>")
            body.append(f"<p class='meta'>曝露を持つ銘柄 {len(x['symbols'])} 本"
                        + (f" ・ {esc(x['note'])}" if x["note"] else "") + "</p>")
            if x["channels"]:
                rows = [[f"<b>{esc(c.get('name') or '—')}</b>", esc(c.get("source") or "—"),
                         f"<code>{esc(c.get('series') or '—')}</code>",
                         ("地域ごと" if c.get("kind") == "region" else "全国 × 1 重み"),
                         f"<code>{esc(c.get('weights') or '—')}</code>",
                         esc(c.get("hypothesis") or "—")
                         + (f"<br>{esc(c['note'])}" if c.get("note") else "")]
                        for c in x["channels"]]
                body.append(table(["経路", "取得元", "系列", "形", "重みの表", "仮説（取得の前の宣言）"], rows))
            for tname, entries in x["weights"].items():
                rows = [[esc(e["symbol"]), fmt(e["total"], 4), esc(e["detail"])] for e in entries]
                body.append(f"<details><summary>重みの表 {esc(tname)}（{len(entries)} 銘柄）【推測】</summary>"
                            + table(["銘柄", "合計", "内訳"], rows, {1}) + "</details>")
        body.append("<p class='meta'>書いていない銘柄の重みは 0（＝ 曝露なし。0 は欠損ではない）。"
                    "偽薬は「割り当ての入れ替え」（im_scramble）で、重みの分布はそのままに"
                    "付き先だけを撹乱する。</p>")
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
