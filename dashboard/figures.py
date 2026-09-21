"""vibeboard のタブで使う図（サーバで組むインライン SVG）。仕様は docs/specs/dashboard.md §18-2。

「システム説明」（systemview.py）と「予測モデル」（modelview.py）の両方が使う ＝ 循環しない置き場。

  - ⚠ **箱の文字は TOML・描き方だけがここ**（説明をこのコードに書かない）
  - ⚠ 1 図 1 主張（`claim` を図の直前に出す）・箱は 12 個以内（`MAX_NODES`。超えたぶんは描かない ＝ テストが数える）
  - ⚠ **標準ライブラリだけ・外部リソースなし**
"""

from __future__ import annotations

import html
import unicodedata

MAX_NODES = 12

CSS = """
 .claim { font-size: 12px; color: #656d76; margin: 14px 0 2px; }
 .claim::before { content: "この図の主張: "; }
 .fig { margin: 4px 0 12px; overflow-x: auto; }
 .fig svg { display: block; max-width: 100%; height: auto; }
 .fig text { font-family: system-ui, sans-serif; }
"""


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _width(text: str) -> float:
    """文字の幅のめやす（全角 1・半角 0.55）。箱の中で折り返す位置を決めるだけ。"""
    return sum(1.0 if unicodedata.east_asian_width(c) in "WFA" else 0.55 for c in text)


def _wrap(text: str, limit: float) -> list[str]:
    lines, cur = [], ""
    for ch in str(text or ""):
        if _width(cur + ch) > limit and cur:
            lines.append(cur)
            cur = ""
        cur += ch
    return lines + [cur] if cur else lines


def flow_svg(steps: list[dict], per_row: int = 4) -> str:
    """箱と矢印の流れ図。左から右へ、`per_row` 個で折り返す。⚠ 箱は `MAX_NODES` 個まで。

    箱 ＝ `{t = 見出し, s = 小さい字, kind = "" | "note" | "out"}`。
    """
    steps = [s for s in steps or [] if s.get("t")][:MAX_NODES]
    if not steps:
        return ""
    bw, gap, pad = 176, 34, 10
    per_row = max(1, min(per_row, len(steps)))
    boxes = []
    for s in steps:
        title, sub = _wrap(s.get("t"), 10.5), _wrap(s.get("s"), 13.5)
        boxes.append((s, title, sub, 16 + 18 * len(title) + 15 * len(sub) + (4 if sub else 0)))
    rows = [boxes[i:i + per_row] for i in range(0, len(boxes), per_row)]
    heights = [max(b[3] for b in row) for row in rows]
    width = pad * 2 + per_row * bw + (per_row - 1) * gap
    height = pad * 2 + sum(heights) + gap * (len(rows) - 1)
    fills = {"note": ("#fff8c5", "#d4a72c"), "out": ("#ddf4ff", "#2a78d6")}
    out = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' width='{width}' height='{height}' role='img'>",
           "<defs><marker id='ah' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' orient='auto'>"
           "<path d='M0,0 L10,5 L0,10 z' fill='#57606a'/></marker></defs>"]
    y = pad
    for r, row in enumerate(rows):
        h = heights[r]
        for c, (s, title, sub, _h) in enumerate(row):
            x = pad + c * (bw + gap)
            fill, stroke = fills.get(str(s.get("kind") or ""), ("#f6f8fa", "#8c959f"))
            out.append(f"<g class='node'><rect x='{x}' y='{y}' width='{bw}' height='{h}' rx='6' fill='{fill}' stroke='{stroke}'/>")
            ty = y + 22
            for line in title:
                out.append(f"<text x='{x + bw / 2:g}' y='{ty}' text-anchor='middle' font-size='14' font-weight='700' fill='#1f2328'>{esc(line)}</text>")
                ty += 18
            ty += 1
            for line in sub:
                out.append(f"<text x='{x + bw / 2:g}' y='{ty}' text-anchor='middle' font-size='11.5' fill='#57606a'>{esc(line)}</text>")
                ty += 15
            out.append("</g>")
            last_in_row, last = c == len(row) - 1, r == len(rows) - 1 and c == len(row) - 1
            if not last_in_row:
                ay = y + h / 2
                out.append(f"<line x1='{x + bw + 3}' y1='{ay:g}' x2='{x + bw + gap - 3}' y2='{ay:g}' stroke='#57606a' stroke-width='1.5' marker-end='url(#ah)'/>")
            elif not last:                       # 行の終わり → 次の行の頭へ（右端から下がって左端へ）
                x0, y0 = x + bw / 2, y + h
                x1, y1 = pad + bw / 2, y + h + gap
                mid = y + h + gap / 2
                out.append(f"<path d='M{x0:g},{y0 + 2:g} V{mid:g} H{x1:g} V{y1 - 3:g}' fill='none' stroke='#57606a' stroke-width='1.5' marker-end='url(#ah)'/>")
        y += h + gap
    out.append("</svg>")
    return "".join(out)


def flow_html(steps: list[dict] | None, claim: str | None = None, per_row: int = 4) -> str:
    """主張（図の直前）＋ 流れ図。箱が無ければ何も出さない。"""
    svg = flow_svg(steps or [], per_row)
    if not svg:
        return ""
    return (f"<p class='claim'>{esc(claim)}</p>" if claim else "") + f"<div class='fig'>{svg}</div>"
