"""評価図を SVG で書く（プラン §9 の納品物 4: 確率の校正図・予測区間図）。⚠ **依存を足さない**（`.venv` に matplotlib は無い）。

- 色は 3 系列までの定性パレット（青・橙・青緑。明暗の両方で検査済み）。区間の帯は青 1 色の濃淡。
- ⚠ **文字は系列の色を着ない**（色は印だけ）。系列は凡例 ＋ 直接のラベルで見分ける。数値の表は記録の本文にある。
- 明暗は SVG の中の `prefers-color-scheme` で切り替わる（背景も自前で塗る）。
"""

from __future__ import annotations

from html import escape

import numpy as np

MIN_COUNT = 20        # 校正図に描く帯の件数の下限（それ未満は 1 件で 0% か 100% に飛ぶので図には描かず、表にだけ出す）
SERIES = {"cgan": ("cGAN（種 3 つの平均）", "s1"), "hist": ("履歴ベース", "s2"), "light": ("軽量モデル", "s3")}
STYLE = """
  .bg{fill:#fcfcfb} .ink{fill:#0b0b0b} .ink2{fill:#52514e} .mut{fill:#898781}
  .grid{stroke:#e1e0d9;stroke-width:1} .axis{stroke:#c3c2b7;stroke-width:1} .ref{stroke:#898781;stroke-width:1}
  .s1{fill:#2a78d6;stroke:#2a78d6} .s2{fill:#eb6834;stroke:#eb6834} .s3{fill:#1baf7a;stroke:#1baf7a}
  .ring{stroke:#fcfcfb;stroke-width:2} .b95{fill:#cde2fb} .b80{fill:#86b6ef} .b50{fill:#3987e5}
  .med{stroke:#104281;stroke-width:2;fill:none} .real{stroke:#0b0b0b;stroke-width:1.25;fill:none}
  text{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:12px}
  .t{font-size:14px;font-weight:600} .tick{font-size:11px;font-variant-numeric:tabular-nums}
  @media (prefers-color-scheme: dark){
    .bg{fill:#1a1a19} .ink{fill:#fff} .ink2{fill:#c3c2b7} .grid{stroke:#2c2c2a} .axis{stroke:#383835}
    .s1{fill:#3987e5;stroke:#3987e5} .s2{fill:#d95926;stroke:#d95926} .s3{fill:#199e70;stroke:#199e70}
    .ring{stroke:#1a1a19} .b95{fill:#0d366b} .b80{fill:#1c5cab} .b50{fill:#3987e5}
    .med{stroke:#cde2fb} .real{stroke:#fff}
  }
"""


def _svg(w: int, h: int, title: str, desc: str, body: list[str]) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img">'
            f"<title>{escape(title)}</title><desc>{escape(desc)}</desc><style>{STYLE}</style>"
            f'<rect class="bg" width="{w}" height="{h}"/>' + "".join(body) + "</svg>")


def _panel(x0, y0, w, h, xt, yt, xlab, ylab, fx, fy, fmt=lambda v: f"{v:g}"):
    """枠・目盛り・軸の名前。`fx`・`fy` は値 → 画面の座標。"""
    out = []
    for v in yt:
        out.append(f'<line class="grid" x1="{x0}" x2="{x0 + w}" y1="{fy(v):.1f}" y2="{fy(v):.1f}"/>')
        out.append(f'<text class="tick mut" x="{x0 - 8}" y="{fy(v) + 4:.1f}" text-anchor="end">{fmt(v)}</text>')
    for v in xt:
        out.append(f'<text class="tick mut" x="{fx(v):.1f}" y="{y0 + h + 16}" text-anchor="middle">{fmt(v)}</text>')
    out.append(f'<line class="axis" x1="{x0}" x2="{x0 + w}" y1="{y0 + h}" y2="{y0 + h}"/>')
    out.append(f'<text class="ink2" x="{x0 + w / 2}" y="{y0 + h + 36}" text-anchor="middle">{escape(xlab)}</text>')
    out.append(f'<text class="ink2" x="{x0}" y="{y0 - 10}">{escape(ylab)}</text>')
    return out


def _legend(x, y, keys) -> list[str]:
    out = []
    for k in keys:
        name, cls = SERIES[k]
        out.append(f'<circle class="{cls}" cx="{x + 5}" cy="{y - 4}" r="5"/><text class="ink2" x="{x + 16}" y="{y}">{escape(name)}</text>')
        x += 28 + 12 * len(name)
    return out


def calibration(reliability: dict[str, list[dict]], coverage: dict[str, dict[int, float]]) -> str:
    """左: 上昇確率の帯ごとの「予想 → 実際」（対角線が理想）。右: 区間の名目 → 実際の被覆率（横棒が名目）。"""
    W, H, y0, ph = 860, 430, 126, 230
    body = ['<text class="t ink" x="24" y="30">確率と区間は、実際の頻度と合っているか（テストの 5 塊・2,512 起点）</text>',
            '<text class="ink2" x="24" y="50">左は対角線、右は横線に近いほどよい。印の大きさは件数。重なった 5 日予測なので、独立な観測はおよそ 1/5</text>',
            f'<text class="ink2" x="24" y="68">⚠ {MIN_COUNT} 件未満の帯は描いていない（記録の表には件数つきで全部ある）</text>']
    body += _legend(24, 92, list(reliability))
    # 左: 校正
    ax, aw = 64, 330
    fx = lambda v: ax + (v - 0.3) / 0.6 * aw
    fy = lambda v: y0 + ph - (v - 0.3) / 0.6 * ph
    ticks = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    body += _panel(ax, y0, aw, ph, ticks, ticks, "モデルが出した上昇確率（帯の平均）", "実際に上がった割合", fx, fy, lambda v: f"{v:.0%}")
    body.append(f'<line class="ref" x1="{fx(0.3)}" y1="{fy(0.3)}" x2="{fx(0.9)}" y2="{fy(0.9)}"/>')
    for k, rows in reliability.items():
        for b in rows:
            if b["件数"] < MIN_COUNT or b["予想"] is None:
                continue
            px, py = np.clip(b["予想"], 0.3, 0.9), np.clip(b["実際"], 0.3, 0.9)
            r = 4 + 6 * np.sqrt(b["件数"] / 2512)
            body.append(f'<circle class="{SERIES[k][1]} ring" cx="{fx(px):.1f}" cy="{fy(py):.1f}" r="{r:.1f}">'
                        f'<title>{escape(SERIES[k][0])} 帯 {b["帯"]}: 予想 {b["予想"]:.1%} → 実際 {b["実際"]:.1%}（{b["件数"]} 件）</title></circle>')
    # 右: 被覆率
    bx, bw = 500, 320
    levels = [50, 80, 95]
    gx = lambda i: bx + bw * (i + 0.5) / 3
    gy = lambda v: y0 + ph - (v - 0.4) / 0.6 * ph
    body += _panel(bx, y0, bw, ph, [], [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], "区間の名目（横線がその値）", "実際に入った割合", gx, gy, lambda v: f"{v:.0%}")
    for i, lv in enumerate(levels):
        body.append(f'<line class="ref" x1="{gx(i) - 44}" x2="{gx(i) + 44}" y1="{gy(lv / 100):.1f}" y2="{gy(lv / 100):.1f}"/>')
        body.append(f'<text class="tick mut" x="{gx(i):.1f}" y="{y0 + ph + 16}" text-anchor="middle">{lv}% 区間</text>')
        for j, (k, cov) in enumerate(coverage.items()):
            cx = gx(i) + (j - (len(coverage) - 1) / 2) * 26
            body.append(f'<circle class="{SERIES[k][1]} ring" cx="{cx:.1f}" cy="{gy(np.clip(cov[lv], 0.4, 1.0)):.1f}" r="6">'
                        f'<title>{escape(SERIES[k][0])} {lv}% 区間: 実際 {cov[lv]:.1%}</title></circle>')
    return _svg(W, H, "確率の校正図", "上昇確率の帯ごとの予想と実際、区間の名目と実際の被覆率", body)


def intervals(dates: list[str], q: dict[str, np.ndarray], realized: np.ndarray, subtitle: str) -> str:
    """予測区間図: 起点ごとの 5 日累積リターンの 95・80・50% 帯 ＋ 中央値 ＋ 実測。`q` は分位点の名前 → `[n]`。"""
    W, H, x0, y0, pw, ph = 860, 400, 64, 96, 700, 230
    n = len(dates)
    lo, hi = float(min(q["2.5"].min(), realized.min())), float(max(q["97.5"].max(), realized.max()))
    lo, hi = np.floor(lo * 50) / 50, np.ceil(hi * 50) / 50
    fx = lambda i: x0 + pw * i / max(n - 1, 1)
    fy = lambda v: y0 + ph - (v - lo) / (hi - lo) * ph
    step = 0.05 if hi - lo > 0.2 else 0.02
    yt = [round(v, 2) for v in np.arange(np.ceil(lo / step) * step, hi + 1e-9, step)]
    body = ['<text class="t ink" x="24" y="30">予測区間と実測（5 営業日後の累積リターン）</text>',
            f'<text class="ink2" x="24" y="50">{escape(subtitle)}</text>',
            '<text class="ink2" x="24" y="72">⚠ 帯は起点ごとの周辺分布の区間（経路全体が帯に入る確率ではない）。日足の終値ベース</text>']
    body += _panel(x0, y0, pw, ph, [], yt, "予測起点（営業日）", "5 日累積リターン", fx, fy, lambda v: f"{v:+.0%}")
    for a, b, cls in (("2.5", "97.5", "b95"), ("10", "90", "b80"), ("25", "75", "b50")):
        up = " ".join(f"{fx(i):.1f},{fy(v):.1f}" for i, v in enumerate(q[b]))
        down = " ".join(f"{fx(i):.1f},{fy(v):.1f}" for i, v in reversed(list(enumerate(q[a]))))
        body.append(f'<polygon class="{cls}" points="{up} {down}"/>')
    body.append('<polyline class="med" points="' + " ".join(f"{fx(i):.1f},{fy(v):.1f}" for i, v in enumerate(q["50"])) + '"/>')
    body.append('<polyline class="real" points="' + " ".join(f"{fx(i):.1f},{fy(v):.1f}" for i, v in enumerate(realized)) + '"/>')
    body.append(f'<line class="ref" x1="{x0}" x2="{x0 + pw}" y1="{fy(0):.1f}" y2="{fy(0):.1f}"/>')
    for i in range(0, n, max(n // 6, 1)):
        body.append(f'<text class="tick mut" x="{fx(i):.1f}" y="{y0 + ph + 16}" text-anchor="middle">{dates[i][:7]}</text>')
    lx = x0 + pw + 10                                                  # 直接のラベル（右端）と凡例
    body.append(f'<text class="ink2" x="{lx}" y="{fy(realized[-1]) + 4:.1f}">実測</text>')
    ly = y0 + 4
    for cls, name in (("b95", "95% 区間"), ("b80", "80% 区間"), ("b50", "50% 区間")):
        body.append(f'<rect class="{cls}" x="{lx}" y="{ly}" width="12" height="12" rx="2"/><text class="ink2" x="{lx + 17}" y="{ly + 10}">{name}</text>')
        ly += 20
    body.append(f'<line class="med" x1="{lx}" x2="{lx + 12}" y1="{ly + 6}" y2="{ly + 6}"/><text class="ink2" x="{lx + 17}" y="{ly + 10}">中央値</text>')
    return _svg(W, H, "予測区間図", "起点ごとの 95・80・50% 区間と中央値、実測の 5 日累積リターン", body)
