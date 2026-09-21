"""vibeboard の「システム説明」タブの画面（HTML の body と図）。仕様は docs/specs/dashboard.md §18。

**このシステムの説明**。全体は簡単に、モデルを作る ／ 過去のデータで確かめる ／ 実際の売買で使う の 3 ページは手厚く。
各段 ＝ やさしい言葉の本文（＋ 図 ＋ 表）→ 「詳しく（用語あり）」の囲み（⚠ **畳まない・開いたままだけ** ＝ 利用者の指示 2026-09-20）。

  - ⚠ **言葉の正本は `dashboard/system.toml`**（ページ → 段。説明をこのコードに書かない）。型ごとの段の文は
    `dashboard/models.toml` から写す（正本を増やさない）
  - ⚠ **図はサーバで組むインライン SVG**（外部リソースなし）。箱の文字は TOML・描き方だけがここ。
    ⚠ 1 図 1 主張（`claim` を図の直前に出す）・箱は 12 個以内（`MAX_NODES`。超えたぶんは描かない ＝ テストが数える）
  - ⚠ **検証結果一覧の合計は描くたびに `ledger.md` の §0 から読む**（読めなければその段を出さない。数字を TOML に書き写さない）
  - ⚠ **売買結果は出さない・`out/`・`state/`・`.env`・`runs/` を開かない**（開くのは TOML と `ledger.md` だけ）
  - ⚠ **標準ライブラリだけ・読むだけ**（vibeboard の sidecar が `python3` で起こす。tailnet の閲覧者にも見える）
"""

from __future__ import annotations

import re

import modelview
import traderview
from figures import MAX_NODES, _width, flow_svg  # noqa: F401（図の描き方は figures.py。ここからも同じ名前で引ける）
from traderview import TraderPaths, esc

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SYSTEM_URL, TAB_URLS = modelview.SYSTEM_URL, modelview.TAB_URLS

CSS = modelview.CSS + """
 .mtype { border-left: 4px solid #2a78d6; padding: 2px 0 2px 14px; margin: 16px 0; }
 .mtype .lb { font-size: 15px; font-weight: 700; }
 .big { display: flex; flex-wrap: wrap; gap: 10px; margin: 8px 0; }
 .big div { border: 1px solid #d0d7de; border-radius: 6px; padding: 6px 14px; min-width: 96px; }
 .big b { display: block; font-size: 20px; }
 .big span { font-size: 12px; color: #656d76; }
"""


def load(paths: TraderPaths) -> dict:
    doc = traderview._load(paths.system)
    pages = [p for p in doc.get("page", []) if ID_PATTERN.match(str(p.get("id") or ""))]
    return {"common": doc.get("common") or {}, "pages": pages}


def sidebar(paths: TraderPaths) -> dict:
    return {"items": [{"id": p["id"], "label": str(p.get("label") or p["id"]), "sub": str(p.get("sub") or "")}
                      for p in load(paths)["pages"]]}


# ---------------------------------------------------------------- 図（インライン SVG）


def folds_svg(fig: dict) -> str:
    """期間を分けて、先へ進みながら試す図。行 ＝ 1 回の試し。学ぶ期間（だんだん長くなる）→ すき間 → 答え合わせの期間。"""
    n = max(2, min(int(fig.get("n") or 5), 8))
    labels = {k: str(fig.get(k) or "") for k in ("learn", "gap", "test", "unused", "axis_from", "axis_to", "row")}
    left, top, row_h, bar_h, total_w = 86, 30, 30, 18, 560
    unit = total_w / (n + 1)                       # 最初の学ぶ期間 ＝ 1 単位、答え合わせ ＝ 1 単位ずつ
    gap_w = 6
    width, height = left + total_w + 16, top + n * row_h + 46
    out = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img'>"]
    out.append(f"<text x='{left}' y='16' font-size='11.5' fill='#57606a'>{esc(labels['axis_from'])}</text>"
               f"<text x='{left + total_w}' y='16' font-size='11.5' fill='#57606a' text-anchor='end'>{esc(labels['axis_to'])}</text>")
    for i in range(n):
        y = top + i * row_h
        learn_w = unit * (i + 1) - gap_w
        out.append(f"<text x='{left - 8}' y='{y + 13}' font-size='12' fill='#424a53' text-anchor='end'>{esc(labels['row'])} {i + 1}</text>")
        out.append(f"<rect class='learn' x='{left}' y='{y}' width='{learn_w:g}' height='{bar_h}' rx='3' fill='#d8dee4'/>")
        out.append(f"<rect class='test' x='{left + unit * (i + 1):g}' y='{y}' width='{unit:g}' height='{bar_h}' rx='3' fill='#2a78d6'/>")
        rest = total_w - unit * (i + 2)
        if rest > 1:
            out.append(f"<rect x='{left + unit * (i + 2):g}' y='{y}' width='{rest:g}' height='{bar_h}' rx='3' fill='none' stroke='#d0d7de' stroke-dasharray='3 3'/>")
    ly = top + n * row_h + 18
    legend = [("#d8dee4", "", labels["learn"]), ("#ffffff", "", labels["gap"]), ("#2a78d6", "", labels["test"]), ("none", "dash", labels["unused"])]
    x = left
    for fill, dash, label in legend:
        if not label:
            continue
        stroke = " stroke='#d0d7de'" + (" stroke-dasharray='3 3'" if dash else "") if fill in ("none", "#ffffff") else ""
        out.append(f"<rect x='{x}' y='{ly - 10}' width='14' height='12' rx='2' fill='{fill}'{stroke}/>"
                   f"<text x='{x + 19}' y='{ly}' font-size='11.5' fill='#424a53'>{esc(label)}</text>")
        x += 19 + _width(label) * 11.5 + 18
    out.append("</svg>")
    return "".join(out)


def figure_html(fig: dict | None) -> str:
    if not fig:
        return ""
    kind = str(fig.get("kind") or "flow")
    svg = folds_svg(fig) if kind == "folds" else flow_svg(fig.get("steps") or [], int(fig.get("per_row") or 4))
    if not svg:
        return ""
    claim = f"<p class='claim'>{esc(fig.get('claim'))}</p>" if fig.get("claim") else ""
    return f"{claim}<div class='fig'>{svg}</div>"


# ---------------------------------------------------------------- 検証結果一覧（旧: 台帳）の合計（§0 の 1 文から読む）


def ledger_totals(paths: TraderPaths) -> dict | None:
    """検証結果一覧（`ledger.md`。生成物）の頭から、試した行数と 採る ／ 保留 ／ 落とす の合計を読む。読めなければ None。"""
    try:
        with open(paths.ledger, encoding="utf-8") as f:
            head = "".join(line for _i, line in zip(range(60), f))
    except OSError:
        return None
    found = {k: re.search(p, head) for k, p in (
        ("date", r"生成日\s*(\d{4}-\d{2}-\d{2})"), ("rows", r"(?:検証|試行)は\s*([\d,]+)\s*行（手法）"),
        ("adopt", r"「採る」は\s*([\d,]+)\s*件"), ("drop", r"落とす\s*([\d,]+)\s*行"), ("hold", r"保留\s*([\d,]+)\s*行"))}
    if not all(found.values()):
        return None
    return {k: m.group(1) for k, m in found.items()}


def _ledger_html(common: dict, paths: TraderPaths) -> str:
    t = ledger_totals(paths)
    if t is None:
        return ""
    cells = [(t["rows"], common.get("ledger_rows")), (t["adopt"], common.get("ledger_adopt")),
             (t["hold"], common.get("ledger_hold")), (t["drop"], common.get("ledger_drop"))]
    return ("<div class='big'>" + "".join(f"<div><b>{esc(v)}</b><span>{esc(k)}</span></div>" for v, k in cells) + "</div>"
            f"<p class='sub'>{esc(str(common.get('ledger_note') or '').replace('{date}', t['date']))}</p>")


# ---------------------------------------------------------------- 段


def _table(tb: dict | None) -> str:
    if not tb or not tb.get("rows"):
        return ""
    head = "".join(f"<th>{esc(h)}</th>" for h in tb.get("head") or [])
    rows = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in tb["rows"])
    return f"<div class='fig'><table>{'<tr>' + head + '</tr>' if head else ''}{rows}</table></div>"


_link, _links, _paras = modelview._link, modelview._links, modelview._paras      # リンクと段落の組み方は予測モデルのタブと同じ


def _detail(common: dict, sec: dict) -> str:
    points = traderview._ul(sec["detail_points"]) if sec.get("detail_points") else ""
    inner = _paras(sec.get("detail")) + points + _table(sec.get("detail_table")) + _links(sec.get("detail_links"))
    if not inner:
        return ""
    # ⚠ 畳まない（<details> にしない）。開いた状態だけ ＝ 利用者の指示 2026-09-20
    return f"<div class='more'><div class='more-h'>{esc(common.get('detail_label') or '詳しく')}</div>{inner}</div>"


def _model_types(common: dict, paths: TraderPaths) -> str:
    """いま使っているモデルの型ごとのしくみ。⚠ 文は `models.toml` から写す（ここに書かない）。図の箱は `[[model]]` の `flow`。"""
    data = modelview.load(paths)
    out = []
    for m in data["models"]:
        if m["id"] not in data["users"]:
            continue
        fig = {"steps": m.get("flow"), "per_row": 5}
        out.append(f"<div class='mtype'><div class='lb'>{esc(m.get('label'))}</div><p>{esc(m.get('summary'))}</p>"
                   f"{figure_html(fig)}<p>{esc(m.get('how'))}</p>"
                   f"<p class='sub'><a href='{traderview.MODEL_URL}{esc(m['id'])}' target='_top'>"
                   f"{esc(data['common'].get('open_page'))}</a></p></div>")
    return "".join(out) or f"<p class='sub'>{esc(common.get('no_live_models'))}</p>"


def section_html(common: dict, paths: TraderPaths, no: int, sec: dict) -> str:
    out = [traderview._h2(no, str(sec.get("title") or "")), _paras(sec.get("text"))]
    if sec.get("points"):
        out.append(traderview._ul(sec["points"]))
    out.append(figure_html(sec.get("figure")))
    out.append(_paras(sec.get("after")))
    out.append(_table(sec.get("table")))
    if sec.get("models") is True:
        out.append(_model_types(common, paths))
    if sec.get("ledger") is True:
        out.append(_ledger_html(common, paths))
    if sec.get("note"):
        out.append(f"<p class='note'>{esc(sec['note'])}</p>")
    out.append(_links(sec.get("links")))
    out.append(_detail(common, sec))
    return "".join(out)


def body(paths: TraderPaths, item: str) -> str | None:
    data = load(paths)
    page = next((p for p in data["pages"] if p["id"] == item), None)
    if page is None:
        return None
    common = data["common"]
    out = ["<div style='--who:#2a78d6'>", f"<h1>{esc(page.get('title') or page.get('label'))}</h1>"]
    if page.get("lead"):
        out.append(f"<p class='lead'>{esc(page['lead'])}</p>")
    for i, sec in enumerate(page.get("section") or [], start=1):
        out.append(section_html(common, paths, i, sec))
    others = [p for p in data["pages"] if p["id"] != item]
    if others:
        out.append(f"<h3>{esc(common.get('other_pages'))}</h3><div class='nav'>" + "".join(
            f"<a href='{SYSTEM_URL}{esc(p['id'])}' target='_top'>{esc(p.get('label'))}</a>" for p in others) + "</div>")
    out.append("</div>")
    return "\n".join(out)


def fingerprint(paths: TraderPaths) -> dict[str, float]:
    """見張り用。システム説明の言葉・検証結果一覧 ＋ モデルの言葉と実売買の設定（型ごとの段がそこから写す）。"""
    out = traderview.fingerprint(paths)
    for f in (paths.system, paths.ledger):
        try:
            out[str(f)] = f.stat().st_mtime
        except OSError:
            continue
    return out
